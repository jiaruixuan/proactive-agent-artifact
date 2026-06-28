#!/usr/bin/env python3
"""
Refine workflow in training/test data:
1. Replace User_Request → Ask_Agent in workflow field only
2. Filter non-agent-executable operators (Cursor_Selection, Scroll, etc.)
3. Abstract fine-grained workflow via LLM (two-stage)

Usage:
  # Process all splits
  python data_processing/refine_workflow_text.py --input-dir hf_dataset --output-dir hf_dataset_refined

  # Process single file
  python data_processing/refine_workflow_text.py --input-file hf_dataset/test.jsonl --output-file hf_dataset_refined/test.jsonl

  # Skip LLM abstraction (only do rule-based cleanup)
  python data_processing/refine_workflow_text.py --input-dir hf_dataset --output-dir hf_dataset_refined --skip-llm

  # Specify API config
  python data_processing/refine_workflow_text.py --input-dir hf_dataset --output-dir hf_dataset_refined \
      --api-key YOUR_API_KEY --endpoint $OPENAI_API_BASE --model deepseek-v3.2
"""

import json
import os
import re
import sys
import asyncio
from pathlib import Path

import fire
import requests
from tqdm import tqdm
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=False)

# ===== Config =====
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"

# Operators that an agent CANNOT perform (pure UI interactions)
FILTER_OPERATORS = {
    "cursor_selection", "select_text", "scroll", "navigate_cursor",
    "cursor_move", "cursor", "select", "highlight",
    "clipboard_change", "clipboard", "copy", "paste", "cut",
}

# ===== Step 1a: Replace User_Request → Ask_Agent in workflow =====

def replace_user_request(workflow: str) -> str:
    """Replace User_Request with Ask_Agent in workflow text only."""
    return re.sub(r'\bUser_Request\b', 'Ask_Agent', workflow)


# ===== Step 1b: Convert Accept_completion → Edit =====

def convert_accept_completion(workflow: str) -> str:
    """Convert Accept_completion/Accept_Completion to Edit, preserving file name if present."""
    # Match: Accept_completion(file): desc  or  Accept_Completion: desc
    workflow = re.sub(
        r'(?i)(Accept_?Completion)(\([^)]+\))',
        lambda m: 'Edit' + m.group(2),
        workflow,
    )
    workflow = re.sub(
        r'(?i)(Accept_?Completion):',
        'Edit:',
        workflow,
    )
    return workflow


# ===== Step 2: Filter non-agent operators =====

# Keywords indicating scroll/view-lines behavior in Change_view steps
SCROLL_KEYWORDS = {"scroll", "view lines", "view line", "viewed", "scrolled", "focused on line"}

def filter_operators(workflow: str) -> str:
    """Remove workflow steps with non-agent-executable operators."""
    lines = workflow.strip().split("\n")
    kept = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Extract operator name: "1. OperatorName(...): ..."
        m = re.match(r'\d+\.\s*(\w+)', line)
        if m:
            op_name = m.group(1).lower()
            if op_name in FILTER_OPERATORS:
                continue
            # Filter scroll-type Change_view/Change_View but keep file-switch ones
            if op_name in ("change_view", "changeview"):
                desc = line.lower()
                if any(kw in desc for kw in SCROLL_KEYWORDS):
                    continue
        kept.append(line)

    # Renumber
    renumbered = []
    for i, line in enumerate(kept):
        # Replace leading number
        renumbered.append(re.sub(r'^\d+\.', f'{i+1}.', line))

    return "\n".join(renumbered)


# ===== Step 3: LLM-based workflow abstraction =====

ABSTRACT_PROMPT_STAGE1 = """\
You are refining a workflow description by adjusting its granularity. You must NOT change the semantic meaning.

Task summary: {summary}
Current workflow:
{workflow}

STRICT RULES:
- You MUST preserve ALL file names, function names, and variable names from the original workflow. Do NOT invent new ones.
- You MUST preserve the semantic meaning of each step. Only adjust the level of detail.
- Merge consecutive Edit steps on the SAME file into one step (e.g., 3 edits to auth.py → 1 edit to auth.py)
- Remove character-level or line-level details (e.g., "change line 5 from 'x' to 'y'" → "update validation logic")
- Keep the operator format: Edit(file/function), Execute(command), Search(file or keyword), Ask_Agent, Open(file)
- Keep 2-8 steps total
- Number each step starting from 1
- If the workflow is already at a good abstraction level (2-8 steps, no character-level details), return it EXACTLY unchanged

FORBIDDEN:
- Do NOT add steps that don't exist in the original
- Do NOT change file names or function names
- Do NOT change the task's intent or scope

If you cannot determine the right abstraction from summary + workflow alone, respond with exactly: NEED_CONTEXT

Respond with ONLY the refined workflow (numbered steps), nothing else."""

ABSTRACT_PROMPT_STAGE2 = """\
You are refining a workflow description by adjusting its granularity. You must NOT change the semantic meaning.

Task summary: {summary}
Current workflow:
{workflow}

Recent IDE operations for context:
{operations}

STRICT RULES:
- You MUST preserve ALL file names, function names, and variable names from the original workflow. Do NOT invent new ones.
- You MUST preserve the semantic meaning of each step. Only adjust the level of detail.
- Merge consecutive Edit steps on the SAME file into one step (e.g., 3 edits to auth.py → 1 edit to auth.py)
- Remove character-level or line-level details (e.g., "change line 5 from 'x' to 'y'" → "update validation logic")
- Keep the operator format: Edit(file/function), Execute(command), Search(file or keyword), Ask_Agent, Open(file)
- Keep 2-8 steps total
- Number each step starting from 1

FORBIDDEN:
- Do NOT add steps that don't exist in the original
- Do NOT change file names or function names
- Do NOT change the task's intent or scope

Respond with ONLY the refined workflow (numbered steps), nothing else."""


def _call_llm(messages, api_key, endpoint, model):
    """Synchronous LLM call."""
    resp = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 1024},
        timeout=120,
    )
    if resp.status_code == 429:
        import time
        time.sleep(10)
        return _call_llm(messages, api_key, endpoint, model)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def abstract_workflow(
    summary: str,
    workflow: str,
    operations: list = None,
    api_key: str = DEFAULT_API_KEY,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
) -> str:
    """Two-stage LLM abstraction of workflow."""

    # Stage 1: summary + workflow only
    prompt = ABSTRACT_PROMPT_STAGE1.format(summary=summary, workflow=workflow)
    messages = [{"role": "user", "content": prompt}]

    try:
        result = _call_llm(messages, api_key, endpoint, model)
    except Exception as e:
        print(f"  LLM Stage 1 error: {e}")
        return workflow

    if "NEED_CONTEXT" in result and operations:
        # Stage 2: add operations context
        ops_text = json.dumps(operations[:50], ensure_ascii=False, indent=1)
        prompt2 = ABSTRACT_PROMPT_STAGE2.format(
            summary=summary, workflow=workflow, operations=ops_text
        )
        messages2 = [{"role": "user", "content": prompt2}]
        try:
            result = _call_llm(messages2, api_key, endpoint, model)
        except Exception as e:
            print(f"  LLM Stage 2 error: {e}")
            return workflow

    # Validate result
    if not result or not re.match(r'^\d+\.', result.strip()):
        return workflow

    result = result.strip()

    # Post-validation: check that file names from original are preserved
    orig_files = set(re.findall(r'(?:Edit|Search|Open)\(([^)]+)\)', workflow))
    new_files = set(re.findall(r'(?:Edit|Search|Open)\(([^)]+)\)', result))
    orig_bases = {f.split('/')[0] for f in orig_files}
    new_bases = {f.split('/')[0] for f in new_files}
    if new_bases - orig_bases:
        return workflow

    # Post-validation: refined steps should not exceed original steps
    orig_step_count = len([l for l in workflow.strip().split('\n') if l.strip()])
    new_step_count = len([l for l in result.strip().split('\n') if l.strip()])
    if new_step_count > orig_step_count:
        # Retry with explicit step count constraint
        retry_prompt = f"Your output has {new_step_count} steps but the original only has {orig_step_count}. " \
                       f"Reduce to at most {orig_step_count} steps by merging related steps. " \
                       f"Respond with ONLY the refined workflow:\n{result}"
        try:
            retry_result = _call_llm(
                [{"role": "user", "content": retry_prompt}],
                api_key, endpoint, model
            )
            if retry_result and re.match(r'^\d+\.', retry_result.strip()):
                retry_count = len([l for l in retry_result.strip().split('\n') if l.strip()])
                if retry_count <= orig_step_count:
                    return retry_result.strip()
        except Exception:
            pass
        # Retry also failed, fall back to original
        return workflow

    return result


# ===== Main processing =====

def process_sample(
    sample: dict,
    skip_llm: bool = False,
    api_key: str = DEFAULT_API_KEY,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Process a single sample: replace operators, filter, abstract."""
    sample = dict(sample)  # shallow copy

    workflow = sample.get("workflow", "")

    # Step 1a: Replace User_Request → Ask_Agent
    workflow = replace_user_request(workflow)

    # Step 1b: Convert Accept_completion → Edit
    workflow = convert_accept_completion(workflow)

    # Step 2: Filter non-agent operators
    workflow = filter_operators(workflow)

    # Step 3: LLM abstraction
    if not skip_llm and workflow:
        operations = sample.get("window_ops", None)
        workflow = abstract_workflow(
            summary=sample.get("task", ""),
            workflow=workflow,
            operations=operations,
            api_key=api_key,
            endpoint=endpoint,
            model=model,
        )

    sample["workflow"] = workflow
    return sample


def process_file(
    input_file: str,
    output_file: str,
    skip_llm: bool = False,
    api_key: str = DEFAULT_API_KEY,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    resume: bool = True,
    workers: int = 8,
):
    """Process a single JSONL file."""
    input_path = Path(input_file)
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load input
    samples = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            samples.append(json.loads(line))

    # Resume: load already processed
    done = {}
    if resume and output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                s = json.loads(line)
                key = f"{s.get('session_id', '')}_{s.get('trigger_op_id', '')}"
                done[key] = s
        print(f"  Resume: {len(done)} already processed")

    results = list(done.values())
    remaining = [
        s for s in samples
        if f"{s.get('session_id', '')}_{s.get('trigger_op_id', '')}" not in done
    ]

    print(f"  {input_path.name}: {len(samples)} total, {len(remaining)} to process")

    if skip_llm:
        # No LLM calls, process sequentially (fast)
        for s in tqdm(remaining, desc=f"  {input_path.name}", ncols=100):
            processed = process_sample(s, skip_llm=True)
            results.append(processed)
    else:
        # Parallel LLM calls
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        lock = threading.Lock()
        pbar = tqdm(total=len(remaining), desc=f"  {input_path.name}", ncols=100)

        def _process_one(s):
            processed = process_sample(s, skip_llm=False, api_key=api_key, endpoint=endpoint, model=model)
            with lock:
                results.append(processed)
                # Incremental save every 50 samples
                if len(results) % 50 == 0:
                    with open(output_path, "w", encoding="utf-8") as f:
                        for r in results:
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            pbar.update(1)
            return processed

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_process_one, s) for s in remaining]
            for f in as_completed(futures):
                pass

        pbar.close()

    # Final save
    with open(output_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"  Saved: {len(results)} samples → {output_path}")


def main(
    input_dir: str = None,
    output_dir: str = None,
    input_file: str = None,
    output_file: str = None,
    skip_llm: bool = False,
    api_key: str = DEFAULT_API_KEY,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    resume: bool = True,
    workers: int = 8,
):
    """Refine workflow in dataset files.

    Args:
        input_dir:   Input directory containing JSONL files
        output_dir:  Output directory for refined files
        input_file:  Single input JSONL file (alternative to input_dir)
        output_file: Single output JSONL file (alternative to output_dir)
        skip_llm:    Skip LLM abstraction, only do rule-based cleanup
        api_key:     API key for LLM calls
        endpoint:    API endpoint for LLM calls
        model:       Model name for LLM calls
        resume:      Resume from previous run
    """
    if input_file:
        if output_file is None:
            output_file = input_file.replace(".jsonl", "_refined.jsonl")
        process_file(input_file, output_file, skip_llm, api_key, endpoint, model, resume, workers)
    elif input_dir:
        if output_dir is None:
            output_dir = input_dir.rstrip("/") + "_refined"
        input_path = Path(input_dir)
        output_path = Path(output_dir)

        for f in sorted(input_path.glob("*.jsonl")):
            out_f = output_path / f.name
            process_file(str(f), str(out_f), skip_llm, api_key, endpoint, model, resume, workers)
    else:
        print("Error: specify --input-dir or --input-file")
        return

    print("\nDone!")


if __name__ == "__main__":
    fire.Fire(main)
