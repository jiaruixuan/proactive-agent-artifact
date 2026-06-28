#!/usr/bin/env python3
"""
Batch-generate task aggregation prompts over cleaned session data and (optionally)
call deepseek-v3.2 via OpenAI SDK (KSC endpoint). Results per session are written
into a single JSON file containing all sliding windows.

Sliding window:
- size: 50 operations (default)
- stride: 25 operations (default)
- direction: from the end of the operations list backward
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List

# Prompt template
PROMPT_TEMPLATE = """You are an interval segmentation engine.

You are given a chronological sequence of user operations (oldest first in this window).
Your goal is to output coarse-grained tasks as NON-OVERLAPPING intervals.

########## HARD CONSTRAINTS (MUST SATISFY ALL) ##########
1) op_start_id and op_end_id MUST be operation IDs that appear in the provided list below.
2) Each task interval MUST be valid: op_start_id <= op_end_id.
3) After sorting by op_start_id, tasks MUST be strictly non-overlapping:
   task[i].op_end_id < task[i+1].op_start_id
4) No nested tasks. If one interval contains another, MERGE them.
5) If two intervals overlap OR touch, MERGE them into one interval:
   merged_start=min(startA,startB), merged_end=max(endA,endB)
6) An operation ID can belong to at most ONE task interval.
7) Prefer fewer tasks when uncertain (merge, do not over-split).
8) Output at most 3 tasks for this window.
9) If you cannot satisfy all constraints confidently, return [].
##########################################################

########## SUMMARY & DETAIL FORMAT ##########
"summary" — one sentence in the format: [target object] + [specific function/change].
  GOOD: "Add process health check and auto-restart to monitor_mysql.sh"
  BAD:  "Edit monitor_mysql.sh for reliability, test it, and update batch script"
  Rules:
  - Name the concrete file, function, class, or component being changed.
  - State WHAT was done functionally, not HOW (no "open file, edit, save" workflow).
  - Do NOT chain multiple files/actions with "and"; pick the primary goal.

"detail" — a structured workflow using ONLY these operators:
  Edit(<file/function>): <what specifically changed>
  User_Request: <what the user explicitly asked or intended>
  Execute(<command>): <what was run and the outcome>
  Search(<scope>): <what was looked up>
  - Omit non-semantic actions: Open, Close, Select, Cursor, scroll, navigate.
  - Omit line numbers.
  - Be specific: "Edit(utils.py/parse_config): add timeout parameter with default 30s"
    not "Edit utils.py: modify function".
  - Keep chronological order; one operator per logical step.
  - Number each step starting from 1.
  Example:
    1. Edit(utils.py/parse_config): add timeout parameter with default 30s
    2. Execute(python test.py): run unit tests, all passed
    3. Edit(utils.py/parse_config): fix typo in docstring
##############################################

OUTPUT FORMAT (JSON array only, no markdown/code fence):
[
  {{
    "summary": "[target] + [specific function/change]",
    "detail": "1. Edit(...): ...\\n2. Execute(...): ...\\n...",
    "op_start_id": 123,
    "op_end_id": 456
  }}
]

SELF-CHECK BEFORE FINAL OUTPUT (MANDATORY):
A) Sort tasks by op_start_id.
B) Merge all overlap/touch/nested conflicts.
C) Re-check strict non-overlap: task[i].op_end_id < task[i+1].op_start_id.
D) Re-check all boundary IDs exist in provided operations.
E) If any check fails, return [] instead of invalid output.

Operations (oldest -> newest within this window):
{operations_block}
"""

def load_ops(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text())
    return data.get("operations", [])


def format_ops_block(ops: List[Dict[str, Any]], max_detail_chars: int) -> str:
    lines = []
    for op in ops:
        op_type = op.get("operationType", "")
        op_id = op.get("id", "")
        ts = op.get("timestamp", "")
        details = op.get("readableDetails", "").replace("\n", " ")
        if max_detail_chars > 0 and len(details) > max_detail_chars:
            details = details[:max_detail_chars] + " ...[truncated]"
        lines.append(f"- id:{op_id} type:{op_type} ts:{ts} details:{details}")
    return "\n".join(lines)


def sliding_windows(ops: List[Dict[str, Any]], window_size: int, window_stride: int):
    """
    Yield non-overlapping windows (window_ops, start_index_in_ops), oldest->newest in each window.

    Strategy (tail-first segmentation):
      - First window: the last `window_size` operations.
      - Then move backward by `window_stride` each time.
      - To avoid overlap, each next window ends exactly at the previous window's start.
      - Always fully covers [0, n).

    Example: n=67, window_size=50, window_stride=50
      -> [17,67), [0,17)
    (callers may sort by start index if they need head-first processing)
    """
    n = len(ops)
    if n == 0:
        return
    if n <= window_size:
        yield ops, 0
        return

    if window_stride <= 0:
        raise ValueError("window_stride must be >= 1")

    start = max(0, n - window_size)
    end = n
    while True:
        yield ops[start:end], start
        if start == 0:
            break
        end = start
        start = max(0, start - window_stride)


def count_windows_for_ops(ops: List[Dict[str, Any]], window_size: int, window_stride: int) -> int:
    return sum(1 for _w, _s in sliding_windows(ops, window_size, window_stride))


def call_llm(
    client: Any,
    prompt: str,
    model: str,
    min_request_interval: float,
    _rate_state: Dict[str, float],
    retry_max_attempts: int,
    retry_backoff_base: float,
    retry_backoff_max: float,
    retry_on_429: bool,
    retry_on_connection: bool,
) -> Dict[str, Any] | None:
    """Send prompt; return response dict or None if api_key empty."""
    if client is None:
        return None
    from openai import APIConnectionError, APIStatusError, RateLimitError  # type: ignore
    backoff = max(0.0, retry_backoff_base)
    attempts = max(1, retry_max_attempts)
    for attempt in range(attempts):
        # Per-process throttle to reduce token-rate spikes.
        if min_request_interval > 0:
            now = time.time()
            wait = _rate_state["next_allowed_ts"] - now
            if wait > 0:
                time.sleep(wait)
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            _rate_state["next_allowed_ts"] = time.time() + max(0.0, min_request_interval)
            return completion.to_dict()
        except RateLimitError as e:
            _rate_state["next_allowed_ts"] = time.time() + max(backoff, min_request_interval)
            if (not retry_on_429) or (attempt == attempts - 1):
                return {"error": str(e), "error_type": e.__class__.__name__}
            time.sleep(backoff)
            backoff = min(max(backoff * 2, retry_backoff_base), retry_backoff_max)
        except (APIConnectionError, APIStatusError) as e:
            _rate_state["next_allowed_ts"] = time.time() + max(backoff, min_request_interval)
            if (not retry_on_connection) or (attempt == attempts - 1):
                return {"error": str(e), "error_type": e.__class__.__name__}
            time.sleep(backoff)
            backoff = min(max(backoff * 2, retry_backoff_base), retry_backoff_max)
        except Exception as e:
            return {"error": str(e), "error_type": "Exception"}


def extract_message_content(resp: Dict[str, Any] | None) -> str | None:
    if not resp or "choices" not in resp:
        # bubble up error for logging
        return None
    try:
        return resp["choices"][0]["message"]["content"]
    except Exception:
        return None


def parse_content_to_tasks(content: str) -> List[Dict[str, Any]]:
    """Extract JSON array from LLM content; tolerate fenced code."""
    if content is None:
        return []
    txt = content.strip()
    # strip code fences
    if txt.startswith("```"):
        # remove first fence
        first_newline = txt.find("\n")
        if first_newline != -1:
            txt = txt[first_newline + 1 :]
        if txt.endswith("```"):
            txt = txt[: -3]
        txt = txt.strip()
    try:
        data = json.loads(txt)
        if isinstance(data, list):
            return data
        # if single object, wrap
        return [data]
    except Exception:
        return [{"raw_text": content}]


def process_file(
    path: Path,
    input_root: Path,
    output_root: Path,
    window_size: int,
    window_stride: int,
    client: Any,
    call_api: bool,
    model: str,
    save_prompts: bool,
    max_detail_chars: int,
    max_prompt_chars: int,
    min_request_interval: float,
    rate_state: Dict[str, float],
    retry_max_attempts: int,
    retry_backoff_base: float,
    retry_backoff_max: float,
    retry_on_429: bool,
    retry_on_connection: bool,
    on_window_done=None,
) -> tuple[int, int]:
    ops = load_ops(path)
    tasks: List[Dict[str, Any]] = []
    if len(ops) == 0:
        # nothing to do but still emit file
        rel = path.relative_to(input_root)
        out_dir = output_root / rel.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{rel.stem}_aggregated.json"
        out_path.write_text(json.dumps({"session": str(rel), "tasks": []}, ensure_ascii=False, indent=2))
        return 0, 0

    prompts_for_debug: List[str] = []
    window_count = 0

    for idx, (w_ops, _start) in enumerate(sliding_windows(ops, window_size, window_stride), start=1):
        window_count += 1
        block = format_ops_block(w_ops, max_detail_chars)
        prompt = PROMPT_TEMPLATE.format(operations_block=block)
        # Hard cap to avoid provider-side 400 on input character limit.
        if max_prompt_chars > 0 and len(prompt) > max_prompt_chars:
            local_detail_cap = max(80, max_detail_chars // 2)
            block = format_ops_block(w_ops, local_detail_cap)
            prompt = PROMPT_TEMPLATE.format(operations_block=block)
            while len(prompt) > max_prompt_chars and len(w_ops) > 1:
                w_ops = w_ops[1:]
                block = format_ops_block(w_ops, local_detail_cap)
                prompt = PROMPT_TEMPLATE.format(operations_block=block)

        if save_prompts:
            prompts_for_debug.append(f"### window {idx}\n{prompt}")
        resp = (
            call_llm(
                client,
                prompt,
                model,
                min_request_interval,
                rate_state,
                retry_max_attempts,
                retry_backoff_base,
                retry_backoff_max,
                retry_on_429,
                retry_on_connection,
            )
            if call_api
            else None
        )
        if resp and "error" in resp:
            print(f"LLM error on {path.name} window {idx}: {resp['error_type']}: {resp['error']}")
        content = extract_message_content(resp)
        window_tasks = parse_content_to_tasks(content)

        # 
        valid_tasks = [t for t in window_tasks if "op_start_id" in t and "op_end_id" in t]
        valid_tasks.sort(key=lambda t: t["op_start_id"])
        for j in range(len(valid_tasks) - 1):
            if valid_tasks[j]["op_end_id"] >= valid_tasks[j + 1]["op_start_id"]:
                print(f"⚠ Overlap in {path.name} window {idx}: "
                      f"[{valid_tasks[j]['op_start_id']}-{valid_tasks[j]['op_end_id']}] "
                      f"∩ [{valid_tasks[j+1]['op_start_id']}-{valid_tasks[j+1]['op_end_id']}]")

        # 
        if w_ops:
            win_min_id = w_ops[0].get("id", 0)
            win_max_id = w_ops[-1].get("id", 0)
            for t in valid_tasks:
                if t["op_start_id"] < win_min_id or t["op_end_id"] > win_max_id:
                    print(f"⚠ Out-of-window in {path.name} window {idx}: "
                          f"task [{t['op_start_id']}-{t['op_end_id']}] "
                          f"exceeds window [{win_min_id}-{win_max_id}]")

        tasks.extend(window_tasks)
        if on_window_done is not None:
            on_window_done()

    #  op_start_id 
    tasks.sort(key=lambda t: t.get("op_start_id", float("inf")))

    rel = path.relative_to(input_root)
    out_dir = output_root / rel.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{rel.stem}_aggregated.json"
    out_path.write_text(
        json.dumps(
            {
                "session": str(rel),
                "tasks": tasks,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if save_prompts and prompts_for_debug:
        prompts_path = out_dir / f"{rel.stem}_prompts.txt"
        prompts_path.write_text("\n\n".join(prompts_for_debug))
    return len(tasks), window_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input_root", default="cleaned_data", help="Root directory containing cleaned session JSON files")
    parser.add_argument("--output_root", default="aggregate_results", help="Where to write aggregated JSON files")
    parser.add_argument("--window_size", type=int, default=50, help="Sliding window size")
    parser.add_argument("--window_stride", type=int, default=25, help="Sliding window stride")
    parser.add_argument("--call-llm", action="store_true", help="If set, send prompts to the LLM")
    parser.add_argument("--api_key", default="", help="LLM API key (leave empty to skip calls)")
    parser.add_argument("--endpoint", default=os.environ.get("OPENAI_API_BASE", ""), help="LLM base URL for OpenAI client")
    parser.add_argument("--model", default="deepseek-v3.2", help="LLM model name")
    parser.add_argument("--resume", action="store_true", help="Skip sessions whose aggregated output already exists")
    parser.add_argument("--save-prompts", action="store_true", help="If set, also save prompts per session for debugging")
    parser.add_argument("--filelist", help="Optional newline-separated file list (relative to input_root or absolute)")
    parser.add_argument("--shard-index", type=int, default=0, help="Shard index (0-based) when splitting work")
    parser.add_argument("--shard-total", type=int, default=1, help="Total shards; keep files with idx %% total == index")
    parser.add_argument("--max-detail-chars", type=int, default=1200, help="Max chars per operation readableDetails in prompt")
    parser.add_argument("--max-prompt-chars", type=int, default=350000, help="Hard cap for total prompt length")
    parser.add_argument("--min-request-interval", type=float, default=1.2, help="Per-process minimum seconds between API calls")
    parser.add_argument("--tqdm", action="store_true", help="Show window-based progress bar")
    parser.add_argument("--retry-max-attempts", type=int, default=3, help="Max attempts per window API call")
    parser.add_argument("--retry-backoff-base", type=float, default=0.5, help="Initial retry backoff seconds")
    parser.add_argument("--retry-backoff-max", type=float, default=4.0, help="Maximum retry backoff seconds")
    parser.add_argument("--no-retry-429", action="store_true", help="Disable retries on 429 rate limits")
    parser.add_argument("--no-retry-connection", action="store_true", help="Disable retries on connection/status errors")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers (default 1 = serial)")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)
    client = None
    if args.call_llm and args.api_key:
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=args.api_key, base_url=args.endpoint)

    # build file list
    if args.filelist:
        lines = [ln.strip() for ln in Path(args.filelist).read_text().splitlines() if ln.strip()]
        all_files = []
        for ln in lines:
            p = Path(ln)
            if not p.is_absolute():
                p = input_root / p
            if p.exists():
                all_files.append(p)
    else:
        all_files = sorted(input_root.rglob("*.json"))

    # sharding
    if args.shard_total <= 0:
        raise SystemExit("--shard-total must be >= 1")
    if not (0 <= args.shard_index < args.shard_total):
        raise SystemExit("--shard-index must satisfy 0 <= index < shard_total")

    all_files = [p for i, p in enumerate(all_files) if i % args.shard_total == args.shard_index]

    total_tasks = 0
    total_files = len(all_files)
    processed_files = 0
    rate_state = {"next_allowed_ts": 0.0}
    pbar = None
    planned_window_total = None
    if args.tqdm:
        try:
            from tqdm import tqdm  # type: ignore

            candidate_files = []
            for path in all_files:
                rel = path.relative_to(input_root)
                out_path = output_root / rel.parent / f"{rel.stem}_aggregated.json"
                if args.resume and out_path.exists():
                    continue
                candidate_files.append(path)
            planned_window_total = 0
            for path in candidate_files:
                planned_window_total += count_windows_for_ops(load_ops(path), args.window_size, args.window_stride)
            pbar = tqdm(total=planned_window_total, desc="windows", unit="win")
        except Exception as e:
            print(f"tqdm unavailable, fallback to plain logs: {e}")
            pbar = None

    # Filter files to process (resume skip)
    files_to_process = []
    for idx, path in enumerate(all_files, start=1):
        rel = path.relative_to(input_root)
        out_path = (output_root / rel.parent / f"{rel.stem}_aggregated.json")
        if args.resume and out_path.exists():
            print(f"[{idx}/{total_files}] skip (resume): {rel}")
            continue
        files_to_process.append((idx, path))

    workers = max(1, args.workers)

    if workers <= 1:
        # Serial mode
        for idx, path in files_to_process:
            rel = path.relative_to(input_root)
            print(f"[{idx}/{total_files}] processing {rel}")
            processed_files += 1
            task_count, win_count = process_file(
                path, input_root, output_root,
                args.window_size, args.window_stride,
                client, args.call_llm, args.model,
                args.save_prompts, args.max_detail_chars, args.max_prompt_chars,
                args.min_request_interval, rate_state,
                args.retry_max_attempts, args.retry_backoff_base, args.retry_backoff_max,
                (not args.no_retry_429), (not args.no_retry_connection),
                (lambda: pbar.update(1)) if pbar is not None else None,
            )
            total_tasks += task_count
            if pbar is not None:
                pbar.set_postfix(files=f"{processed_files}/{total_files}", tasks=total_tasks)
    else:
        # Parallel mode with semaphore
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        sem = threading.Semaphore(workers)
        stats_lock = threading.Lock()
        errors = 0

        def _worker(idx, path):
            # Each worker gets its own client and rate_state
            worker_client = None
            if args.call_llm and args.api_key:
                from openai import OpenAI as _OpenAI
                worker_client = _OpenAI(api_key=args.api_key, base_url=args.endpoint)
            worker_rate_state = {"next_allowed_ts": 0.0}
            sem.acquire()
            try:
                tc, wc = process_file(
                    path, input_root, output_root,
                    args.window_size, args.window_stride,
                    worker_client, args.call_llm, args.model,
                    args.save_prompts, args.max_detail_chars, args.max_prompt_chars,
                    args.min_request_interval, worker_rate_state,
                    args.retry_max_attempts, args.retry_backoff_base, args.retry_backoff_max,
                    (not args.no_retry_429), (not args.no_retry_connection),
                    None,
                )
                return tc
            finally:
                sem.release()

        print(f"\nParallel processing {len(files_to_process)} sessions ({workers} workers)...")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for idx, path in files_to_process:
                future = executor.submit(_worker, idx, path)
                futures[future] = (idx, path)

            for future in as_completed(futures):
                idx, path = futures[future]
                rel = path.relative_to(input_root)
                try:
                    tc = future.result()
                    with stats_lock:
                        processed_files += 1
                        total_tasks += tc
                    print(f"[{processed_files}/{len(files_to_process)}] done {rel} ({tc} tasks)")
                except Exception as e:
                    with stats_lock:
                        errors += 1
                    print(f"[ERROR] {rel}: {e}")

        if errors:
            print(f"\n{errors} sessions failed")

    if pbar is not None:
        pbar.close()
    print(f"Processed {processed_files}/{len(files_to_process) if workers > 1 else total_files} session files; aggregated tasks: {total_tasks} entries (parsed) under {output_root}")
    if args.call_llm:
        if args.api_key:
            print("LLM calls attempted; errors (if any) are stored in response_raw.")
        else:
            print("LLM calls skipped because api_key is empty.")
    else:
        print("LLM call stub: set --call-llm and --api_key to enable requests.")


if __name__ == "__main__":
    main()
