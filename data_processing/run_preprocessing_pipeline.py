#!/usr/bin/env python3
"""
Unified data processing pipeline.

Consolidates the 4-step workflow into a single entry point:
  Step 0: Merge edit+cursor_selection sequences
  Step 1: Aggregate tasks via sliding window + LLM
  Step 2: Refine tasks (granularity filter, merge, count, split oversized)
  Step 3: Classify tasks into positive/negative (optional)

Usage:
  # Full run (incremental by default)
  python3 data_processing/run_preprocessing_pipeline.py --call-llm --api-key YOUR_KEY

  # Rerun everything
  python3 data_processing/run_preprocessing_pipeline.py --call-llm --api-key YOUR_KEY --rerun-all

  # Only specific sessions
  python3 data_processing/run_preprocessing_pipeline.py --call-llm --api-key YOUR_KEY --sessions my_sessions.txt

  # With classification
  python3 data_processing/run_preprocessing_pipeline.py --call-llm --api-key YOUR_KEY --classify

  # Parallel processing
  python3 data_processing/run_preprocessing_pipeline.py --call-llm --api-key YOUR_KEY --workers 4
"""

import argparse
import json
import sys
import time
import os
import traceback
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Any

# Ensure sibling modules are importable
sys.path.insert(0, str(Path(__file__).parent))

# ============================================================================
# Configuration defaults
# ============================================================================

DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"
DEFAULT_WINDOW_SIZE = 50
DEFAULT_WINDOW_STRIDE = 50  # no overlap → simpler refine


# ============================================================================
# Status Manager
# ============================================================================

class StatusManager:
    """Per-session status tracking with file-based persistence."""

    STEPS = ("merge", "aggregate", "refine", "classify")

    def __init__(self, status_file: Path):
        self.status_file = status_file
        self._data: Dict[str, Any] = {"sessions": {}}
        self._load()

    # -- persistence --

    def _load(self):
        if self.status_file.exists():
            try:
                self._data = json.loads(self.status_file.read_text())
            except Exception:
                self._data = {"sessions": {}}

    def save(self):
        self.status_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.status_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))
        tmp.replace(self.status_file)

    # -- queries --

    def get(self, key: str) -> Dict:
        return self._data["sessions"].get(key, {})

    def step_done(self, key: str, step: str) -> bool:
        return self.get(key).get("steps", {}).get(step, {}).get("status") == "done"

    def all_done(self, key: str, steps: List[str]) -> bool:
        return all(self.step_done(key, s) for s in steps)

    # -- mutations --

    def update_step(self, key: str, step: str, status: str, **extra):
        sess = self._data["sessions"].setdefault(key, {"steps": {}})
        sess["steps"][step] = {
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **extra,
        }

    def set_error(self, key: str, error: str):
        sess = self._data["sessions"].setdefault(key, {"steps": {}})
        sess["error"] = error
        sess["error_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    def reset_all(self):
        self._data = {"sessions": {}}

    def summary(self) -> Dict[str, int]:
        total = len(self._data["sessions"])
        done = sum(1 for s in self._data["sessions"].values()
                   if all(s.get("steps", {}).get(st, {}).get("status") == "done"
                          for st in ("merge", "aggregate", "refine")))
        failed = sum(1 for s in self._data["sessions"].values() if s.get("error"))
        return {"total": total, "done": done, "failed": failed, "pending": total - done - failed}


# ============================================================================
# Session Discovery
# ============================================================================

def discover_sessions(data_dir: Path, sessions_file: Optional[str] = None) -> List[Path]:
    """Find all session JSON files under data_dir."""
    if sessions_file:
        lines = Path(sessions_file).read_text().splitlines()
        paths = []
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            p = Path(ln)
            if not p.is_absolute():
                p = data_dir / p
            if p.exists():
                paths.append(p)
        return sorted(paths)

    # Recursive glob for session_*.json under data/
    return sorted(data_dir.rglob("session_*.json"))


def session_key(session_path: Path, data_dir: Path) -> str:
    """Stable key for a session (relative path from data_dir)."""
    try:
        return str(session_path.relative_to(data_dir))
    except ValueError:
        return str(session_path)


# ============================================================================
# Step 0: Merge Operations
# ============================================================================

def step_merge(session_path: Path, output_dir: Path) -> Path:
    """Merge alternating edit+cursor sequences. Returns cleaned file path."""
    from clean_operations import merge_operations

    data = json.loads(session_path.read_text())
    ops = data.get("operations", [])
    original_count = len(ops)

    merged_ops = merge_operations(ops)
    data["operations"] = merged_ops
    if "metadata" in data and isinstance(data["metadata"], dict):
        data["metadata"]["total_operations"] = len(merged_ops)
        data["metadata"]["_original_total_operations"] = original_count
        data["metadata"]["_merged"] = True

    out_path = output_dir / session_path.name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    reduction = (1 - len(merged_ops) / original_count) * 100 if original_count else 0
    print(f"  [merge] {original_count} → {len(merged_ops)} ops (-{reduction:.0f}%)")
    return out_path


# ============================================================================
# Step 1: Aggregate (sliding window + LLM)
# ============================================================================

def step_aggregate(
    cleaned_path: Path,
    output_dir: Path,
    llm_config: Dict,
    window_size: int,
    window_stride: int,
) -> Path:
    """Generate tasks via sliding window. Returns aggregated file path."""
    from aggregate_task_windows import (
        load_ops, sliding_windows, format_ops_block, call_llm,
        extract_message_content, parse_content_to_tasks, PROMPT_TEMPLATE,
    )

    ops = load_ops(cleaned_path)
    tasks: List[Dict] = []
    rate_state = {"next_allowed_ts": 0.0}
    client = None
    if llm_config.get("call_llm") and llm_config.get("api_key"):
        from openai import OpenAI
        client = OpenAI(api_key=llm_config["api_key"], base_url=llm_config["endpoint"])

    window_count = 0
    max_detail_chars = llm_config.get("max_detail_chars", 1200)
    max_prompt_chars = llm_config.get("max_prompt_chars", 350000)

    for idx, (w_ops, _start) in enumerate(sliding_windows(ops, window_size, window_stride), 1):
        window_count += 1
        block = format_ops_block(w_ops, max_detail_chars)
        prompt = PROMPT_TEMPLATE.format(operations_block=block)

        # Truncate if too long
        if max_prompt_chars > 0 and len(prompt) > max_prompt_chars:
            local_cap = max(80, max_detail_chars // 2)
            block = format_ops_block(w_ops, local_cap)
            prompt = PROMPT_TEMPLATE.format(operations_block=block)
            while len(prompt) > max_prompt_chars and len(w_ops) > 1:
                w_ops = w_ops[1:]
                block = format_ops_block(w_ops, local_cap)
                prompt = PROMPT_TEMPLATE.format(operations_block=block)

        resp = call_llm(client, prompt, llm_config["model"],
                        llm_config.get("min_request_interval", 1.2),
                        rate_state, 3, 0.5, 4.0, True, True) if client else None

        if resp and "error" in resp:
            print(f"    LLM error window {idx}: {resp.get('error_type')}: {resp.get('error')}")

        content = extract_message_content(resp)
        window_tasks = parse_content_to_tasks(content)
        tasks.extend(window_tasks)

    # Sort by start id
    tasks.sort(key=lambda t: t.get("op_start_id", float("inf")))

    out_path = output_dir / f"{cleaned_path.stem}_aggregated.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "session": cleaned_path.stem,
        "tasks": tasks,
    }, ensure_ascii=False, indent=2))

    print(f"  [aggregate] {window_count} windows → {len(tasks)} tasks")
    return out_path


# ============================================================================
# Step 2: Refine (strict version with retry + fallback)
# ============================================================================

def step_refine(
    aggregated_path: Path,
    cleaned_path: Path,
    output_dir: Path,
    llm_config: Dict,
) -> Path:
    """Refine tasks: filter, merge, count, split oversized. Returns refined file path."""
    from refine_task_intervals import (
        load_json, save_json,
        execute_granularity_filtering_simplified,
        merge_tasks_single_iteration,
        count_and_report_operations,
        split_oversized_tasks,
    )

    client = None
    if llm_config.get("call_llm") and llm_config.get("api_key"):
        from openai import OpenAI
        client = OpenAI(api_key=llm_config["api_key"], base_url=llm_config["endpoint"])
    model = llm_config["model"]

    token_stats = {
        'total_input_tokens': 0, 'total_output_tokens': 0,
        'total_tokens': 0, 'llm_calls': 0,
    }

    # Load data
    agg_data = load_json(aggregated_path)
    tasks = agg_data.get("tasks", [])

    # Filter invalid tasks
    valid_tasks = [t for t in tasks
                   if "op_start_id" in t and "op_end_id" in t
                   and "raw_text" not in t]
    if len(valid_tasks) < len(tasks):
        print(f"    filtered {len(tasks) - len(valid_tasks)} invalid tasks")
    tasks = valid_tasks

    cleaned_data = load_json(cleaned_path)
    ops = cleaned_data.get("operations", [])
    initial_count = len(tasks)

    if not tasks:
        print(f"  [refine] no tasks to refine")
    else:
        # Sub-step 1: Granularity filtering
        tasks = execute_granularity_filtering_simplified(tasks, ops)

        # Sub-step 2: Merge (overlapping → force merge, adjacent → LLM judge)
        tasks = merge_tasks_single_iteration(
            tasks, ops, client, model,
            max_time_gap=20, max_id_gap=10,
            min_confidence=0.7, token_stats=token_stats,
        )

        # Sub-step 3: Count operations
        tasks = count_and_report_operations(tasks, ops)

        # Sub-step 4: Split oversized tasks (>50 ops)
        tasks = split_oversized_tasks(
            tasks, ops, client, model,
            threshold=50, token_stats=token_stats,
        )

    session_id = aggregated_path.stem.replace("_aggregated", "")
    result_data = {
        "session_id": session_id,
        "tasks": tasks,
        "processing_info": {
            "initial_count": initial_count,
            "final_count": len(tasks),
            "reduction_pct": round(
                (initial_count - len(tasks)) / initial_count * 100, 2
            ) if initial_count else 0,
            "token_usage": {
                "llm_calls": token_stats["llm_calls"],
                "total_input_tokens": token_stats["total_input_tokens"],
                "total_output_tokens": token_stats["total_output_tokens"],
                "total_tokens": token_stats["total_tokens"],
            },
        },
    }

    out_path = output_dir / f"{session_id}_refined.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(result_data, out_path)

    print(f"  [refine] {initial_count} → {len(tasks)} tasks, "
          f"LLM calls: {token_stats['llm_calls']}, tokens: {token_stats['total_tokens']}")
    return out_path


# ============================================================================
# Step 3: Classify (optional)
# ============================================================================

def step_classify(
    refined_path: Path,
    cleaned_path: Path,
    output_dir: Path,
    llm_config: Dict,
) -> Path:
    """Classify tasks into positive/negative. Returns classified file path."""
    from classify_proactive_tasks import (
        load_json, analyze_task, get_context_ops,
        classify_task_with_llm, format_operations_for_prompt,
        compute_edit_lengths_for_session,
        CONTEXT_OPS_BEFORE, POS_CONFIDENCE_THRESHOLD, NEG_CONFIDENCE_THRESHOLD,
        DETAIL_GENERATION_PROMPT,
    )
    from classify_proactive_tasks import call_llm as classify_call_llm

    refined_data = load_json(refined_path)
    cleaned_data = load_json(cleaned_path)

    tasks = refined_data.get("tasks", [])
    operations = cleaned_data.get("operations", [])
    ops_by_id = {op["id"]: op for op in operations if "id" in op}
    all_op_ids = sorted(ops_by_id.keys())

    # Filter invalid tasks
    tasks = [t for t in tasks
             if t.get("op_start_id") is not None and t.get("op_end_id") is not None]
    if not tasks:
        print(f"  [classify] no valid tasks")
        return refined_path

    # Compute edit threshold (median of edit lengths)
    edit_lengths = compute_edit_lengths_for_session(tasks, ops_by_id, all_op_ids)
    if edit_lengths:
        edit_lengths_sorted = sorted(edit_lengths)
        edit_threshold = edit_lengths_sorted[len(edit_lengths_sorted) // 2]
    else:
        edit_threshold = 1500.0

    token_stats = {
        'llm_calls': 0, 'total_input_tokens': 0,
        'total_output_tokens': 0, 'total_tokens': 0,
    }

    # Analyze tasks (rule-based pre-classification)
    task_analyses = []
    for i, task in enumerate(tasks):
        analysis = analyze_task(task, ops_by_id, all_op_ids, edit_threshold, None)
        analysis["task_index"] = i
        analysis["task"] = task
        task_analyses.append(analysis)

    positives = []
    negatives = []

    if llm_config.get("call_llm") and llm_config.get("api_key"):
        # Generate missing details
        for a in task_analyses:
            task = a["task"]
            if not task.get("detail"):
                _, task_ops = get_context_ops(task, ops_by_id, all_op_ids, 0)
                try:
                    task_text = format_operations_for_prompt(task_ops, compressed=True)
                    prompt = DETAIL_GENERATION_PROMPT.format(
                        task_summary=task.get("summary", ""),
                        op_start_id=task.get("op_start_id"),
                        op_end_id=task.get("op_end_id"),
                        task_operations=task_text,
                    )
                    detail = classify_call_llm(
                        prompt, llm_config["api_key"], llm_config["endpoint"],
                        llm_config["model"], token_stats=token_stats,
                    )
                    task["detail"] = detail.strip()
                except Exception:
                    pass
                time.sleep(0.5)

        # LLM classification
        for a in task_analyses:
            task = a["task"]
            before_ops, task_ops = get_context_ops(
                task, ops_by_id, all_op_ids, CONTEXT_OPS_BEFORE)
            try:
                result, prompt_mode = classify_task_with_llm(
                    task, before_ops, task_ops, a["category"],
                    llm_config["api_key"], llm_config["endpoint"],
                    llm_config["model"], token_stats,
                )
                if prompt_mode == "too_long":
                    continue

                decision = result.get("decision", "skip")
                confidence = result.get("confidence", 0)
                reasoning = result.get("reasoning", "")

                entry = {
                    "summary": task.get("summary", ""),
                    "detail": task.get("detail", ""),
                    "op_start_id": task["op_start_id"],
                    "op_end_id": task["op_end_id"],
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "trigger_after_op_id": before_ops[-1]["id"] if before_ops else None,
                }

                if decision == "positive" and confidence >= POS_CONFIDENCE_THRESHOLD:
                    entry["suggested_prompt"] = result.get("suggested_prompt", "")
                    positives.append(entry)
                elif decision == "negative" and confidence >= NEG_CONFIDENCE_THRESHOLD:
                    negatives.append(entry)

            except RuntimeError:
                raise  # 429 exhaustion → bubble up
            except Exception as e:
                print(f"    classify error: {e}")

            time.sleep(0.5)

    session_id = refined_path.stem.replace("_refined", "")
    output = {
        "session_id": session_id,
        "metadata": cleaned_data.get("metadata", {}),
        "operations": operations,
        "positive": positives,
        "negative": negatives,
        "classification_info": {
            "total_tasks": len(tasks),
            "positive_count": len(positives),
            "negative_count": len(negatives),
            "edit_threshold": edit_threshold,
            "token_usage": token_stats,
        },
    }

    out_path = output_dir / f"{session_id}_classified.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"  [classify] positive={len(positives)}, negative={len(negatives)}")
    return out_path


# ============================================================================
# Single-Session Pipeline
# ============================================================================

def process_single_session(
    session_path: Path,
    data_dir: Path,
    output_root: Path,
    llm_config: Dict,
    window_size: int,
    window_stride: int,
    run_classify: bool,
    incremental: bool,
    status_mgr: Optional[StatusManager],
) -> Dict[str, Any]:
    """Run all pipeline steps for one session. Returns result dict."""

    key = session_key(session_path, data_dir)
    session_id = session_path.stem

    # Build per-session output directories (preserve relative structure)
    try:
        rel = session_path.relative_to(data_dir)
        rel_dir = rel.parent
    except ValueError:
        rel_dir = Path("")

    cleaned_dir = output_root / "cleaned" / rel_dir
    aggregated_dir = output_root / "aggregated" / rel_dir
    refined_dir = output_root / "refined" / rel_dir
    classified_dir = output_root / "classified" / rel_dir

    requested_steps = ["merge", "aggregate", "refine"]
    if run_classify:
        requested_steps.append("classify")

    # Incremental: skip if all requested steps are done
    if incremental and status_mgr and status_mgr.all_done(key, requested_steps):
        return {"session": key, "status": "skipped"}

    result = {"session": key, "status": "ok", "steps": {}}

    try:
        # ---- Step 0: Merge ----
        if incremental and status_mgr and status_mgr.step_done(key, "merge"):
            cleaned_path = cleaned_dir / f"{session_id}.json"
            print(f"[{session_id}] merge: skipped (done)")
        else:
            print(f"[{session_id}] processing...")
            cleaned_path = step_merge(session_path, cleaned_dir)
            if status_mgr:
                status_mgr.update_step(key, "merge", "done")
            result["steps"]["merge"] = "done"

        # ---- Step 1: Aggregate ----
        if incremental and status_mgr and status_mgr.step_done(key, "aggregate"):
            agg_path = aggregated_dir / f"{session_id}_aggregated.json"
            print(f"  [aggregate] skipped (done)")
        else:
            agg_path = step_aggregate(cleaned_path, aggregated_dir, llm_config,
                                       window_size, window_stride)
            if status_mgr:
                status_mgr.update_step(key, "aggregate", "done")
            result["steps"]["aggregate"] = "done"

        # ---- Step 2: Refine ----
        if incremental and status_mgr and status_mgr.step_done(key, "refine"):
            refined_path = refined_dir / f"{session_id}_refined.json"
            print(f"  [refine] skipped (done)")
        else:
            refined_path = step_refine(agg_path, cleaned_path, refined_dir, llm_config)
            if status_mgr:
                status_mgr.update_step(key, "refine", "done")
            result["steps"]["refine"] = "done"

        # ---- Step 3: Classify (optional) ----
        if run_classify:
            if incremental and status_mgr and status_mgr.step_done(key, "classify"):
                print(f"  [classify] skipped (done)")
            else:
                step_classify(refined_path, cleaned_path, classified_dir, llm_config)
                if status_mgr:
                    status_mgr.update_step(key, "classify", "done")
                result["steps"]["classify"] = "done"

    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
        print(f"  ERROR: {e}")
        traceback.print_exc()
        if status_mgr:
            status_mgr.set_error(key, str(e))

    return result


# ============================================================================
# Worker function for parallel execution
# ============================================================================

def _worker(
    session_path_str: str,
    data_dir_str: str,
    output_root_str: str,
    llm_config: Dict,
    window_size: int,
    window_stride: int,
    run_classify: bool,
) -> Dict[str, Any]:
    """Process one session in a subprocess (no StatusManager - not picklable)."""
    return process_single_session(
        session_path=Path(session_path_str),
        data_dir=Path(data_dir_str),
        output_root=Path(output_root_str),
        llm_config=llm_config,
        window_size=window_size,
        window_stride=window_stride,
        run_classify=run_classify,
        incremental=False,  # handled by main process pre-filtering
        status_mgr=None,    # main process updates status after worker returns
    )


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Unified data processing pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--data-dir", default="data",
                        help="Input data directory (default: data)")
    parser.add_argument("--output-dir", default="pipeline_output",
                        help="Output root directory (default: pipeline_output)")
    parser.add_argument("--incremental", action="store_true", default=True,
                        help="Skip already-completed sessions (default: True)")
    parser.add_argument("--rerun-all", action="store_true",
                        help="Reset all status and reprocess everything")
    parser.add_argument("--sessions", type=str, default=None,
                        help="File listing session paths to process")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel workers (default: 1)")
    parser.add_argument("--classify", action="store_true",
                        help="Run optional Step 3 (classification)")

    # LLM config
    parser.add_argument("--call-llm", action="store_true",
                        help="Enable LLM API calls")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY,
                        help="LLM API key")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help="LLM API endpoint")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="LLM model name")

    # Window config
    parser.add_argument("--window-size", type=int, default=DEFAULT_WINDOW_SIZE,
                        help="Sliding window size (default: 50)")
    parser.add_argument("--window-stride", type=int, default=DEFAULT_WINDOW_STRIDE,
                        help="Sliding window stride (default: 50)")

    # Advanced
    parser.add_argument("--max-detail-chars", type=int, default=1200,
                        help="Max chars per operation detail in prompt")
    parser.add_argument("--max-prompt-chars", type=int, default=350000,
                        help="Hard cap for total prompt length")
    parser.add_argument("--min-request-interval", type=float, default=1.2,
                        help="Min seconds between API calls")
    parser.add_argument("--status-file", default=None,
                        help="Status file path (default: <output-dir>/status.json)")

    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_root = Path(args.output_dir)
    status_file = Path(args.status_file) if args.status_file else output_root / "status.json"

    # Build LLM config dict (serializable for workers)
    llm_config = {
        "call_llm": args.call_llm,
        "api_key": args.api_key,
        "endpoint": args.endpoint,
        "model": args.model,
        "max_detail_chars": args.max_detail_chars,
        "max_prompt_chars": args.max_prompt_chars,
        "min_request_interval": args.min_request_interval,
    }

    # Status manager
    status_mgr = StatusManager(status_file)
    if args.rerun_all:
        status_mgr.reset_all()
        status_mgr.save()
        print("Status reset. Will reprocess all sessions.")

    # Discover sessions
    print(f"Scanning {data_dir} for sessions...")
    all_sessions = discover_sessions(data_dir, args.sessions)
    print(f"Found {len(all_sessions)} session files.")

    # Filter for incremental
    requested_steps = ["merge", "aggregate", "refine"]
    if args.classify:
        requested_steps.append("classify")

    if args.incremental and not args.rerun_all:
        pending = [s for s in all_sessions
                   if not status_mgr.all_done(session_key(s, data_dir), requested_steps)]
        print(f"Incremental mode: {len(pending)} sessions to process "
              f"({len(all_sessions) - len(pending)} already done).")
        sessions_to_process = pending
    else:
        sessions_to_process = all_sessions

    if not sessions_to_process:
        print("Nothing to process.")
        return

    # Run
    t0 = time.time()
    results = {"ok": 0, "failed": 0, "skipped": 0}

    if args.workers <= 1:
        # Sequential
        for i, session_path in enumerate(sessions_to_process, 1):
            print(f"\n[{i}/{len(sessions_to_process)}] ", end="")
            result = process_single_session(
                session_path=session_path,
                data_dir=data_dir,
                output_root=output_root,
                llm_config=llm_config,
                window_size=args.window_size,
                window_stride=args.window_stride,
                run_classify=args.classify,
                incremental=args.incremental,
                status_mgr=status_mgr,
            )
            results[result["status"]] = results.get(result["status"], 0) + 1
            # Save status periodically
            if i % 10 == 0:
                status_mgr.save()

        status_mgr.save()

    else:
        # Parallel
        print(f"Running with {args.workers} workers...")
        futures = {}
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            for session_path in sessions_to_process:
                future = executor.submit(
                    _worker,
                    str(session_path),
                    str(data_dir),
                    str(output_root),
                    llm_config,
                    args.window_size,
                    args.window_stride,
                    args.classify,
                )
                futures[future] = session_path

            done_count = 0
            for future in as_completed(futures):
                done_count += 1
                session_path = futures[future]
                key = session_key(session_path, data_dir)
                try:
                    result = future.result()
                    status = result["status"]
                    results[status] = results.get(status, 0) + 1

                    if status == "ok":
                        for step_name in result.get("steps", {}):
                            status_mgr.update_step(key, step_name, "done")
                    elif status == "failed":
                        status_mgr.set_error(key, result.get("error", "unknown"))

                    if done_count % 10 == 0:
                        print(f"\nProgress: {done_count}/{len(sessions_to_process)} "
                              f"(ok={results['ok']}, failed={results['failed']})")
                        status_mgr.save()

                except Exception as e:
                    results["failed"] += 1
                    status_mgr.set_error(key, str(e))
                    print(f"Worker exception for {key}: {e}")

        status_mgr.save()

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"Pipeline complete in {elapsed:.1f}s")
    print(f"  OK: {results['ok']}, Failed: {results['failed']}, Skipped: {results['skipped']}")
    summary = status_mgr.summary()
    print(f"  Total tracked: {summary['total']}, Done: {summary['done']}, "
          f"Failed: {summary['failed']}, Pending: {summary['pending']}")


if __name__ == "__main__":
    main()
