#!/usr/bin/env python3
"""
Classify refined tasks into proactive positive and negative examples.

Pipeline:
1. Load refined tasks and cleaned operations.
2. Split tasks into positive candidates and negative candidates using edit length
   and operation type heuristics.
3. Optionally call an LLM classifier.
4. Write the final operation sequence with positive/negative labels.

Usage:
  # Single-session test
  python3 classify_proactive_tasks.py --single-session session_2cbc28ee \
      --refined-dir refine_results --cleaned-dir cleaned_data \
      --output-dir classify_output --call-llm

  # Rule-based statistics only; no LLM calls
  python3 classify_proactive_tasks.py --refined-dir refine_results --cleaned-dir cleaned_data \
      --output-dir classify_output

  # Use a fixed edit-length threshold
  python3 classify_proactive_tasks.py --edit-threshold 1500 --call-llm ...
"""

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Any, Tuple, List

import requests
from tqdm import tqdm

# ===== API =====
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"

# ===== Default parameters =====
CONTEXT_OPS_BEFORE = 50       # Number of operations before a task used as context.

# ===== Prompt =====
CHAR_LIMIT = 350000
TOKEN_LIMIT = 90000
LIGHT_COMPRESS_TRIGGER = 0.70

# ===== Confidence thresholds =====
POS_CONFIDENCE_THRESHOLD = 0.85
NEG_CONFIDENCE_THRESHOLD = 0.85


# ===================== Compression helpers =====================

def _clip_text(text: str, max_len: int = 180) -> str:
    if len(text) <= max_len:
        return text
    keep = max_len // 2
    return f"{text[:keep]} ... {text[-keep:]}"


def _sum_tag_content_len(text: str, tag: str) -> int:
    return sum(len(x) for x in re.findall(fr"<{tag}>(.*?)</{tag}>", text, flags=re.S))


def _extract_first_tag_content(text: str, tag: str) -> str:
    m = re.search(fr"<{tag}>(.*?)</{tag}>", text, flags=re.S)
    return m.group(1) if m else ""


def light_compress_readable_details(op: Dict[str, Any]) -> str:
    """"""
    raw = str(op.get('readableDetails', ''))
    op_type = str(op.get('operationType', ''))

    path_m = re.search(r"<path>(.*?)</path>", raw, flags=re.S)
    path = path_m.group(1) if path_m else ""
    s_m = re.search(r"<context_start_line>(\d+)</context_start_line>", raw)
    e_m = re.search(r"<context_end_line>(\d+)</context_end_line>", raw)
    line_range = f"{s_m.group(1)}-{e_m.group(1)}" if s_m and e_m else "N/A"

    if op_type == "edit_text":
        ins_len = _sum_tag_content_len(raw, "FT_insert")
        del_len = _sum_tag_content_len(raw, "FT_delete")
        ins_sample = _clip_text(_extract_first_tag_content(raw, "FT_insert"), 120)
        del_sample = _clip_text(_extract_first_tag_content(raw, "FT_delete"), 120)
        return (
            f"path={path}; lines={line_range}; "
            f"insert_chars={ins_len}; delete_chars={del_len}; "
            f"insert_sample={ins_sample}; delete_sample={del_sample}"
        )

    if op_type == "user_request":
        ur_match = re.search(r'<user_request>(.*?)</user_request>', raw, re.DOTALL)
        ur_text = _clip_text(ur_match.group(1).strip(), 500) if ur_match else ""
        return f"user_request={ur_text}"

    if op_type == "terminal_execution":
        cmd = str(op.get('commandLine', ''))[:300]
        output = _clip_text(str(op.get('output', '')), 200)
        return f"command={cmd}; output={output}"

    cursor_line_m = re.search(r"<line>(\d+)</line>", raw)
    cursor_col_m = re.search(r"<column>(\d+)</column>", raw)
    cursor_info = ""
    if cursor_line_m and cursor_col_m:
        cursor_info = f"; cursor={cursor_line_m.group(1)}:{cursor_col_m.group(1)}"

    short_plain = re.sub(r"<[^>]+>", " ", raw)
    short_plain = re.sub(r"\s+", " ", short_plain).strip()
    short_plain = _clip_text(short_plain, 180)
    return f"path={path}; lines={line_range}{cursor_info}; summary={short_plain}"


def estimate_prompt_size(prompt: str) -> Tuple[int, int]:
    """ (char_count, token_count)"""
    char_count = len(prompt)
    try:
        import tiktoken
        encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        token_count = len(encoding.encode(prompt, disallowed_special=()))
    except Exception:
        token_count = char_count // 4
    return char_count, token_count


# =====================  =====================

def extract_edit_length(readable_details: str) -> int:
    """ <FT_delete>  <FT_insert> """
    total = 0
    for tag in ('FT_delete', 'FT_insert'):
        for m in re.finditer(rf'<{tag}>(.*?)</{tag}>', readable_details, re.DOTALL):
            total += len(m.group(1))
    return total


def find_cleaned_file(refined_path: Path, refined_dir: Path, cleaned_dir: Path) -> Path:
    """refinedcleaned_data"""
    rel = refined_path.relative_to(refined_dir)
    cleaned_name = rel.name.replace("_refined.json", ".json")
    return cleaned_dir / rel.parent / cleaned_name


def load_json(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_output_path(refined_path: Path, refined_dir: Path, output_dir: Path) -> Path:
    """session"""
    rel = refined_path.relative_to(refined_dir)
    out_name = rel.name.replace('_refined.json', '_classified.json')
    return output_dir / rel.parent / out_name


def compact_operation(op: dict, compressed: bool = False) -> dict:
    """operationtoken"""
    if compressed:
        return {
            'id': op.get('id'),
            'type': op.get('operationType'),
            'details': light_compress_readable_details(op),
        }

    compact = {
        'id': op.get('id'),
        'type': op.get('operationType'),
    }
    rd = op.get('readableDetails', '')
    # path
    path_match = re.search(r'<path>(.*?)</path>', rd)
    if path_match:
        compact['path'] = path_match.group(1)
    # edit_text
    if op.get('operationType') == 'edit_text':
        edits = []
        for tag in ('FT_delete', 'FT_insert'):
            for m in re.finditer(rf'<{tag}>(.*?)</{tag}>', rd, re.DOTALL):
                content = m.group(1).strip()
                if content:
                    edits.append(f'[{tag}] {content[:500]}')
        if edits:
            compact['edits'] = edits
    # user_request
    elif op.get('operationType') == 'user_request':
        ur_match = re.search(r'<user_request>(.*?)</user_request>', rd, re.DOTALL)
        if ur_match:
            compact['user_request'] = ur_match.group(1).strip()[:1000]
    # terminal_execution
    elif op.get('operationType') == 'terminal_execution':
        cmd = op.get('commandLine', '')
        if cmd:
            compact['command'] = cmd.strip()[:500]
        output = op.get('output', '')
        if output:
            compact['output'] = output.strip()[:500]
    # readableDetails
    else:
        if len(rd) > 300:
            compact['details'] = rd[:300] + '...'
        else:
            compact['details'] = rd
    return compact


def format_operations_for_prompt(operations: list, compressed: bool = False) -> str:
    """operationsprompt"""
    lines = []
    for op in operations:
        cop = compact_operation(op, compressed=compressed)
        lines.append(json.dumps(cop, ensure_ascii=False))
    return '\n'.join(lines)


# ===================== LLM =====================

def call_llm(prompt: str, api_key: str, endpoint: str, model: str,
             max_retries_429: int = 5, token_stats: dict = None) -> str:
    """LLM APItoken
    429(rate limit)"""
    url = f"{endpoint}chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    for attempt in range(max_retries_429):
        resp = requests.post(url, headers=headers, json=payload, timeout=120)

        if resp.status_code == 429:
            if attempt < max_retries_429 - 1:
                wait = 2 ** attempt * 5  # 5, 10, 20, 40, 80
                tqdm.write(f"    429 rate limit (attempt {attempt+1}), {wait}s")
                time.sleep(wait)
                continue
            else:
                raise RuntimeError(f"429 rate limit: {max_retries_429}")

        resp.raise_for_status()
        data = resp.json()

        # token
        if token_stats is not None:
            usage = data.get('usage', {})
            token_stats['llm_calls'] += 1
            token_stats['total_input_tokens'] += usage.get('prompt_tokens', 0)
            token_stats['total_output_tokens'] += usage.get('completion_tokens', 0)
            token_stats['total_tokens'] += (
                usage.get('prompt_tokens', 0) + usage.get('completion_tokens', 0))

        return data['choices'][0]['message']['content']


def parse_llm_json(response: str) -> dict:
    """LLMJSON"""
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r'\{.*\}', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


# ===================== Prompt =====================

DETAIL_GENERATION_PROMPT = """Based on the following user operations from a software development session, describe what the user did as a structured workflow.

Use ONLY these operators:
  Edit(file/function): what was changed
  User_Request: what the user explicitly asked or intended
  Execute(command): what was run and the outcome
  Search(file or keyword): what was searched for

Rules:
  - Omit non-semantic actions (Open, Close, Select, Cursor movements).
  - Be specific about WHAT changed; do NOT include line numbers.
  - Keep chronological order; one operator per logical step.
  - Number each step starting from 1.

## Task Summary
{task_summary}

## Operations (id range: {op_start_id} ~ {op_end_id})
{task_operations}

Respond with ONLY the numbered steps, no JSON, no markdown formatting."""

POSITIVE_PROMPT_TEMPLATE = """You are an expert evaluator determining whether an AI coding agent could have proactively predicted and suggested a user's next task based on their recent activity.

## Context
Below is a sequence of user operations in a software development session. The operations are divided into two parts:
1. **Recent Activity** (operations BEFORE the task): What the user was doing leading up to this task.
2. **Task Operations** (operations WITHIN the task): What the user actually did in this task.

## Task Info
- Summary: {task_summary}
- Detail: {task_detail}
- Operation range: {op_start_id} ~ {op_end_id}

## Recent Activity (before the task)
{prev_operations}

## Task Operations
{task_operations}

## Your Job
Determine whether this task could have been **proactively suggested** by an AI coding agent BEFORE the user started it, based solely on the Recent Activity context.

A task is worth proactively suggesting ONLY if ALL of the following are true:
1. **Predictable**: The task is a logical next step given the recent activity. There must be direct, concrete signals in the context — not just thematic similarity. For example, the user just modified a function signature, so updating the callers is predictable. Merely working in the same file or same feature area is NOT sufficient.
2. **Valuable**: The suggestion would save the user time or prevent a likely mistake. The task should involve meaningful work, not something the user would trivially do on their own in seconds.
3. **Actionable**: An AI agent could provide a concrete suggestion with specific file names, function names, or exact changes. Vague suggestions like "you might want to continue working on the import feature" are NOT actionable enough.

**Precision over recall**: When you are unsure whether all three criteria are clearly met, choose "skip". We prefer missing some positives over including questionable ones.

Use high confidence (>=0.85) only when you are genuinely certain. Use moderate confidence (0.6-0.84) when the case is reasonable but has some ambiguity. Use low confidence (<0.6) when the case is weak.

## Output Format
Respond with a JSON object ONLY (no other text):
{{
  "decision": "positive" or "skip",
  "confidence": 0.0 ~ 1.0,
  "reasoning": "Brief explanation of why this task should or should not be proactively suggested",
  "suggested_prompt": "If positive, write the proactive suggestion an agent could have given to the user (1-2 sentences in the user's working language). If skip, leave empty."
}}"""

NEGATIVE_PROMPT_TEMPLATE = """You are an expert evaluator identifying tasks that an AI coding agent should definitely NOT proactively suggest.

## Context
Below is a sequence of user operations in a software development session. The operations are divided into two parts:
1. **Recent Activity** (operations BEFORE the task): What the user was doing leading up to this task.
2. **Task Operations** (operations WITHIN the task): What the user actually did in this task.

## Task Info
- Summary: {task_summary}
- Detail: {task_detail}
- Operation range: {op_start_id} ~ {op_end_id}

## Recent Activity (before the task)
{prev_operations}

## Task Operations
{task_operations}

## Your Job
Determine whether this task should be marked as a **definite negative** — a task that an AI agent should absolutely NOT proactively suggest.

A task is a definite negative if ANY of the following is true:
1. **Unpredictable**: The task has no logical connection to the recent activity. It is clearly driven by external factors invisible in the context (e.g., responding to an external message, starting a completely unrelated feature with no prior signals). Note: a task that is part of the same feature area but takes an unexpected direction is NOT necessarily unpredictable — only classify as unpredictable when there is truly no signal in the context.
2. **Pure Noise**: The task consists entirely of trivial, meaningless operations with no productive coding outcome (e.g., random scrolling without purpose, accidental edits immediately reverted, only opening/closing files). Note: small but intentional edits (like removing an unused prop, fixing a value) are NOT pure noise.
3. **Requires Privileged Knowledge**: The task clearly requires information impossible to infer from the operation history (e.g., a brand-new product requirement communicated verbally, a decision to abandon a library due to an external compatibility report).

**Quality standard**: Choose "negative" only when you are confident the case is clear-cut. If the task has any reasonable connection to recent activity or represents intentional development work (even minor), choose "skip".

Use high confidence (>=0.85) only when you are genuinely certain. Use moderate confidence (0.6-0.84) when the case is reasonable but has some ambiguity. Use low confidence (<0.6) when the case is weak.

## Output Format
Respond with a JSON object ONLY (no other text):
{{
  "decision": "negative" or "skip",
  "confidence": 0.0 ~ 1.0,
  "reasoning": "Brief explanation of why this task should or should not be marked as a negative example"
}}"""


# =====================  =====================

def analyze_task(task: dict, ops_by_id: dict, all_op_ids: list,
                 edit_threshold: float, edit_upper_threshold: float = None) -> dict:
    """task"""
    start_id = task.get('op_start_id')
    end_id = task.get('op_end_id')

    task_op_ids = [oid for oid in all_op_ids if start_id <= oid <= end_id]
    task_ops = [ops_by_id[oid] for oid in task_op_ids]

    has_edit = False
    has_user_request = False
    edit_length = 0

    for op in task_ops:
        op_type = op.get('operationType')
        if op_type == 'edit_text':
            has_edit = True
            edit_length += extract_edit_length(op.get('readableDetails', ''))
        elif op_type == 'user_request':
            has_user_request = True

    if not has_edit and not has_user_request:
        return {
            'category': 'step5', 'has_edit': False,
            'has_user_request': False, 'edit_length': 0,
            'reason': 'no_edit_no_user_request'
        }
    elif has_edit and edit_length < edit_threshold:
        return {
            'category': 'step5', 'has_edit': True,
            'has_user_request': has_user_request, 'edit_length': edit_length,
            'reason': f'edit_too_short ({edit_length} < {edit_threshold:.0f})'
        }
    elif has_edit and edit_upper_threshold and edit_length > edit_upper_threshold:
        return {
            'category': 'step5', 'has_edit': True,
            'has_user_request': has_user_request, 'edit_length': edit_length,
            'reason': f'edit_too_long ({edit_length} > {edit_upper_threshold:.0f})'
        }
    else:
        return {
            'category': 'step4', 'has_edit': has_edit,
            'has_user_request': has_user_request, 'edit_length': edit_length,
            'reason': 'has_edit_long' if has_edit else 'has_user_request'
        }


def get_context_ops(task: dict, ops_by_id: dict, all_op_ids: list,
                    n_before: int) -> tuple:
    """taskcontext operationstaskoperations"""
    start_id = task['op_start_id']
    end_id = task['op_end_id']

    task_op_ids = [oid for oid in all_op_ids if start_id <= oid <= end_id]
    task_ops = [ops_by_id[oid] for oid in task_op_ids]

    before_op_ids = [oid for oid in all_op_ids if oid < start_id]
    before_op_ids = before_op_ids[-n_before:]
    before_ops = [ops_by_id[oid] for oid in before_op_ids]

    return before_ops, task_ops


def build_classification_prompt(task: dict, before_ops: list, task_ops: list,
                                category: str) -> Tuple[str, str]:
    """
    prompt
     (prompt, mode)  mode  "full"  "compressed"
    """
    template = POSITIVE_PROMPT_TEMPLATE if category == 'step4' else NEGATIVE_PROMPT_TEMPLATE

    def _fill(compressed=False):
        prev_text = format_operations_for_prompt(before_ops, compressed=compressed)
        task_text = format_operations_for_prompt(task_ops, compressed=compressed)
        return template.format(
            task_summary=task.get('summary', ''),
            task_detail=task.get('detail', ''),
            op_start_id=task.get('op_start_id'),
            op_end_id=task.get('op_end_id'),
            prev_operations=prev_text if prev_text else '(No prior operations)',
            task_operations=task_text if task_text else '(No operations)',
        )

    # prompt
    prompt_full = _fill(compressed=False)
    char_count, token_count = estimate_prompt_size(prompt_full)
    usage_ratio = max(char_count / CHAR_LIMIT, token_count / TOKEN_LIMIT)

    if usage_ratio < LIGHT_COMPRESS_TRIGGER:
        return prompt_full, "full"

    # 
    prompt_compressed = _fill(compressed=True)
    char_count, token_count = estimate_prompt_size(prompt_compressed)
    usage_ratio = max(char_count / CHAR_LIMIT, token_count / TOKEN_LIMIT)

    if usage_ratio < 1.0:
        return prompt_compressed, "compressed"

    # context operations
    while len(before_ops) > 5 and usage_ratio >= 1.0:
        before_ops = before_ops[len(before_ops) // 2:]  # 
        prompt_compressed = _fill(compressed=True)
        char_count, token_count = estimate_prompt_size(prompt_compressed)
        usage_ratio = max(char_count / CHAR_LIMIT, token_count / TOKEN_LIMIT)

    if usage_ratio < 1.0:
        return prompt_compressed, "compressed+truncated"

    # 
    return None, "too_long"


def classify_task_with_llm(task: dict, before_ops: list, task_ops: list,
                           category: str, api_key: str, endpoint: str,
                           model: str, token_stats: dict) -> Tuple[dict, str]:
    """LLMtask (result, prompt_mode)"""
    prompt, mode = build_classification_prompt(task, before_ops, task_ops, category)

    if prompt is None:
        return {'decision': 'skip', 'confidence': 0, 'reasoning': 'prompt too long'}, mode

    response = call_llm(prompt, api_key, endpoint, model, token_stats=token_stats)
    result = parse_llm_json(response)
    return result, mode


def compute_edit_lengths_for_session(tasks: list, ops_by_id: dict,
                                     all_op_ids: list) -> list:
    """edittaskedit"""
    edit_lengths = []
    for task in tasks:
        start_id = task.get('op_start_id')
        end_id = task.get('op_end_id')
        if start_id is None or end_id is None:
            continue
        task_op_ids = [oid for oid in all_op_ids if start_id <= oid <= end_id]
        task_edit_len = 0
        has_edit = False
        for oid in task_op_ids:
            op = ops_by_id[oid]
            if op.get('operationType') == 'edit_text':
                has_edit = True
                task_edit_len += extract_edit_length(op.get('readableDetails', ''))
        if has_edit:
            edit_lengths.append(task_edit_len)
    return edit_lengths


# =====================  =====================

def process_session(refined_path: Path, cleaned_path: Path,
                    output_dir: Path, args, edit_threshold: float) -> dict:
    """session"""
    session_id = refined_path.name.replace('_refined.json', '')
    tqdm.write(f"\n{'='*50}")
    tqdm.write(f" session: {session_id}")

    # sessiontoken
    token_stats = {
        'llm_calls': 0,
        'total_input_tokens': 0,
        'total_output_tokens': 0,
        'total_tokens': 0,
    }

    # 
    refined_data = load_json(refined_path)
    cleaned_data = load_json(cleaned_path)

    tasks = refined_data.get('tasks', [])
    if not tasks:
        tasks = refined_data.get('processing_info', {}).get('final_tasks', [])

    operations = cleaned_data.get('operations', [])
    ops_by_id = {op['id']: op for op in operations if 'id' in op}
    all_op_ids = sorted(ops_by_id.keys())

    tqdm.write(f"  tasks: {len(tasks)}, operations: {len(operations)}")

    # task
    valid_tasks = [t for t in tasks
                   if t.get('op_start_id') is not None and t.get('op_end_id') is not None]
    if len(valid_tasks) < len(tasks):
        tqdm.write(f"  task: {len(tasks) - len(valid_tasks)} ")
    tasks = valid_tasks

    if not tasks:
        tqdm.write("  task")
        return None

    edit_upper = getattr(args, 'edit_upper_threshold', None)
    tqdm.write(f"  edit: {edit_threshold:.0f}" +
          (f", : {edit_upper:.0f}" if edit_upper else ""))

    # task
    task_analyses = []
    step4_count = 0
    step5_count = 0

    for i, task in enumerate(tasks):
        analysis = analyze_task(task, ops_by_id, all_op_ids, edit_threshold, edit_upper)
        analysis['task_index'] = i
        analysis['task'] = task
        task_analyses.append(analysis)

        cat = analysis['category']
        if cat == 'step4':
            step4_count += 1
        else:
            step5_count += 1

    tqdm.write(f"\n  : step4(positive)={step4_count}, step5(negative)={step5_count}")
    for i, a in enumerate(task_analyses):
        tag = "POS" if a['category'] == 'step4' else "NEG"
        tqdm.write(f"    task {i}: [{tag}] edit_len={a['edit_length']}, "
              f"has_edit={a['has_edit']}, has_ur={a['has_user_request']}, "
              f"reason={a['reason']}")

    # LLM
    positives = []
    negatives = []

    if args.call_llm:
        # detail
        detail_generated = 0
        for a in task_analyses:
            task = a['task']
            if not task.get('detail'):
                _, task_ops = get_context_ops(
                    task, ops_by_id, all_op_ids, 0)
                tqdm.write(f"    task {a['task_index']}: detail...", end=' ')
                try:
                    task_text = format_operations_for_prompt(task_ops, compressed=True)
                    prompt = DETAIL_GENERATION_PROMPT.format(
                        task_summary=task.get('summary', ''),
                        op_start_id=task.get('op_start_id'),
                        op_end_id=task.get('op_end_id'),
                        task_operations=task_text,
                    )
                    detail = call_llm(prompt, args.api_key, args.endpoint,
                                      args.model, token_stats=token_stats)
                    task['detail'] = detail.strip()
                    detail_generated += 1
                    tqdm.write(f"OK ({len(task['detail'])} chars)")
                except Exception as e:
                    tqdm.write(f": {e}")
                time.sleep(0.5)
        if detail_generated:
            tqdm.write(f"   {detail_generated} taskdetail")

        # LLM step4 
        tqdm.write(f"\n  LLM...")
        for a in task_analyses:
            if a['category'] != 'step4':
                continue  # LLM
            task = a['task']
            before_ops, task_ops = get_context_ops(
                task, ops_by_id, all_op_ids, CONTEXT_OPS_BEFORE)

            tqdm.write(f"    task {a['task_index']} ({a['category']}): "
                  f"context={len(before_ops)}ops, task={len(task_ops)}ops ... ",
                  end='')

            try:
                result, prompt_mode = classify_task_with_llm(
                    task, before_ops, task_ops, a['category'],
                    args.api_key, args.endpoint, args.model, token_stats
                )

                if prompt_mode == "too_long":
                    tqdm.write(f"prompt")
                    continue
                if prompt_mode != "full":
                    tqdm.write(f"[{prompt_mode}] ", end='')

                decision = result.get('decision', 'skip')
                confidence = result.get('confidence', 0)
                reasoning = result.get('reasoning', '')
                tqdm.write(f"{decision} (conf={confidence:.2f})")
                tqdm.write(f"      reasoning: {reasoning[:100]}")

                pos_threshold = POS_CONFIDENCE_THRESHOLD
                neg_threshold = NEG_CONFIDENCE_THRESHOLD

                if decision == 'positive' and confidence >= pos_threshold:
                    positives.append({
                        'summary': task.get('summary', ''),
                        'detail': task.get('detail', ''),
                        'op_start_id': task['op_start_id'],
                        'op_end_id': task['op_end_id'],
                        'confidence': confidence,
                        'reasoning': reasoning,
                        'suggested_prompt': result.get('suggested_prompt', ''),
                        'trigger_after_op_id': before_ops[-1]['id'] if before_ops else None,
                    })
                else:
                    if decision == 'positive':
                        tqdm.write(f"      -> low confidence ({confidence:.2f} < {pos_threshold}), skipped")
                    else:
                        tqdm.write(f"      -> skipped by LLM")

            except RuntimeError as e:
                # 429sessionresume
                raise
            except Exception as e:
                tqdm.write(f"LLM error: {e}")

            time.sleep(0.5)  # rate limit

    # 
    output = {
        'session_id': session_id,
        'metadata': cleaned_data.get('metadata', {}),
        'operations': operations,
        'positive': positives,
        'classification_info': {
            'total_tasks': len(tasks),
            'step4_candidates': step4_count,
            'step5_candidates': step5_count,
            'positive_count': len(positives),
            'edit_threshold': edit_threshold,
            'context_ops_before': CONTEXT_OPS_BEFORE,
        }
    }

    # 
    rel = refined_path.relative_to(Path(args.refined_dir))
    out_name = rel.name.replace('_refined.json', '_classified.json')
    out_path = output_dir / rel.parent / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    tqdm.write(f"\n  : {out_path}")
    tqdm.write(f"  positive: {len(positives)}")

    return output


def process_session_wrapper(rf: Path, args, refined_dir: Path, cleaned_dir: Path,
                            output_dir: Path, edit_threshold: float,
                            idx: int, total: int) -> dict:
    """
    process_session
    
    """
    session_id = rf.name.replace('_refined.json', '')

    # cleaned_data
    cleaned_path = find_cleaned_file(rf, refined_dir, cleaned_dir)
    if not cleaned_path.exists():
        tqdm.write(f"  [{idx}/{total}]  {session_id}: cleaned_data")
        return {'status': 'skipped', 'reason': 'no_cleaned_data'}

    # JSON
    try:
        load_json(rf)
        load_json(cleaned_path)
    except Exception as e:
        tqdm.write(f"  [{idx}/{total}]  {session_id}: JSON ({e})")
        return {'status': 'skipped', 'reason': f'json_error: {e}'}

    # session
    try:
        result = process_session(rf, cleaned_path, output_dir, args, edit_threshold)
        if result:
            return {'status': 'ok', 'result': result}
        else:
            return {'status': 'skipped', 'reason': 'no_valid_tasks'}
    except Exception as e:
        tqdm.write(f"  [{idx}/{total}] ❌ {session_id}: {e}")
        return {'status': 'error', 'reason': str(e)}


def main():
    parser = argparse.ArgumentParser(description="task")
    parser.add_argument('--refined-dir', default='refine_results',
                        help='refined')
    parser.add_argument('--cleaned-dir', default='cleaned_data',
                        help='cleaned_data')
    parser.add_argument('--output-dir', default='classify_output',
                        help='')
    parser.add_argument('--single-session', default=None,
                        help='session_id')
    parser.add_argument('--call-llm', action='store_true',
                        help='LLMtask')
    parser.add_argument('--api-key', default=DEFAULT_API_KEY)
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--edit-threshold', type=float, default=None,
                        help='editsession')
    parser.add_argument('--edit-upper-threshold', type=float, default=None,
                        help='editnegative')
    parser.add_argument('--stats-only', action='store_true',
                        help='edit')
    parser.add_argument('--workers', type=int, default=1,
                        help='worker1')
    parser.add_argument('--resume', action='store_true',
                        help='session')

    args = parser.parse_args()

    refined_dir = Path(args.refined_dir)
    cleaned_dir = Path(args.cleaned_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # refinedrefine_resultssession
    if args.single_session:
        sid = args.single_session
        refined_files = list(refined_dir.rglob(f"**/{sid}_refined.json"))
        if not refined_files:
            print(f" {sid} refined")
            sys.exit(1)
    else:
        refined_files = sorted(refined_dir.rglob("session_*_refined.json"))

    print(f" {len(refined_files)} refined")

    # ===== edit =====
    if args.edit_threshold is None or args.stats_only:
        print("\nsessionedit...")
        import numpy as np
        all_edit_lengths = []
        scanned = 0
        skipped_scan = 0

        for rf in refined_files:
            cleaned_path = find_cleaned_file(rf, refined_dir, cleaned_dir)
            if not cleaned_path.exists():
                skipped_scan += 1
                continue

            try:
                rdata = load_json(rf)
                cdata = load_json(cleaned_path)
            except Exception:
                skipped_scan += 1
                continue

            tasks = rdata.get('tasks', [])
            if not tasks:
                tasks = rdata.get('processing_info', {}).get('final_tasks', [])

            ops = cdata.get('operations', [])
            ops_by_id = {op['id']: op for op in ops if 'id' in op}
            all_op_ids = sorted(ops_by_id.keys())

            valid_tasks = [t for t in tasks
                           if t.get('op_start_id') is not None and t.get('op_end_id') is not None]

            lengths = compute_edit_lengths_for_session(valid_tasks, ops_by_id, all_op_ids)
            all_edit_lengths.extend(lengths)
            scanned += 1

            if (scanned % 200) == 0:
                print(f"   {scanned} session...")

        print(f"\n: {scanned} sessions,  {skipped_scan}")

        if all_edit_lengths:
            arr = np.array(all_edit_lengths)
            median_val = float(np.median(arr))
            print(f"\n===== editedittask =====")
            print(f"  taskedit: {len(arr)}")
            print(f"  : {arr.mean():.0f}")
            print(f"  : {median_val:.0f}")
            print(f"  : {arr.min()}")
            print(f"  : {arr.max()}")
            q25, q75, q90, q95, q99 = np.percentile(arr, [25, 75, 90, 95, 99])
            print(f"  25%: {q25:.0f}")
            print(f"  75%: {q75:.0f}")
            print(f"  90%: {q90:.0f}")
            print(f"  95%: {q95:.0f}")
            print(f"  99%: {q99:.0f}")

            if args.edit_threshold is None:
                args.edit_threshold = median_val
                print(f"\n  ->  {median_val:.0f} edit")
        else:
            print("  edittask")
            if args.edit_threshold is None:
                args.edit_threshold = 0.0

        if args.stats_only:
            print("\n--stats-only")
            return

    edit_threshold = args.edit_threshold
    print(f"\nedit: {edit_threshold:.0f}")

    # ===== Resumesession =====
    if args.resume:
        before_count = len(refined_files)
        refined_files = [
            rf for rf in refined_files
            if not get_output_path(rf, refined_dir, output_dir).exists()
        ]
        skipped_resume = before_count - len(refined_files)
        if skipped_resume > 0:
            print(f"Resume {skipped_resume} session"
                  f" {len(refined_files)} ")

    if not refined_files:
        print("session")
        return

    # ===== session =====
    stats_lock = threading.Lock()
    total_positive = 0
    total_tasks = 0
    processed = 0
    errors = 0
    skipped = 0

    total = len(refined_files)
    workers = args.workers

    def accumulate_stats(result_info):
        """"""
        nonlocal total_positive, total_tasks, processed
        info = result_info['classification_info']
        with stats_lock:
            total_positive += info['positive_count']
            total_tasks += info['total_tasks']
            processed += 1

    start_time = time.time()

    pbar = tqdm(total=total, desc="", unit="session",
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

    def handle_result(ret):
        nonlocal errors, skipped
        if ret['status'] == 'ok':
            accumulate_stats(ret['result'])
            info = ret['result']['classification_info']
            pbar.set_postfix_str(
                f"P={total_positive} E={errors}", refresh=False)
        elif ret['status'] == 'error':
            errors += 1
        else:
            skipped += 1
        pbar.update(1)

    if workers <= 1:
        # 
        print(f"\n {total} session...")
        for idx, rf in enumerate(refined_files, 1):
            ret = process_session_wrapper(
                rf, args, refined_dir, cleaned_dir, output_dir,
                edit_threshold, idx, total
            )
            handle_result(ret)
    else:
        # 
        print(f"\n {total} session{workers} workers...")

        # Ctrl+C
        shutdown_event = threading.Event()
        original_sigint = signal.getsignal(signal.SIGINT)

        def signal_handler(signum, frame):
            tqdm.write(f"\n\n...")
            shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)

        try:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for idx, rf in enumerate(refined_files, 1):
                    if shutdown_event.is_set():
                        break
                    future = executor.submit(
                        process_session_wrapper,
                        rf, args, refined_dir, cleaned_dir, output_dir,
                        edit_threshold, idx, total
                    )
                    futures[future] = rf

                for future in as_completed(futures):
                    if shutdown_event.is_set():
                        break
                    try:
                        ret = future.result()
                        handle_result(ret)
                    except Exception as e:
                        errors += 1
                        pbar.update(1)
                        tqdm.write(f"  : {e}")

            if shutdown_event.is_set():
                tqdm.write(f"\n {processed} session")
        finally:
            signal.signal(signal.SIGINT, original_sigint)

    pbar.close()
    elapsed = time.time() - start_time

    # =====  =====
    print(f"\n{'='*60}")
    print(f": {processed} sessions, {skipped} , {errors} ")
    print(f"tasks: {total_tasks}, positive: {total_positive}")
    print(f": {elapsed:.1f}s ({elapsed/60:.1f}min)")
    if processed > 0:
        print(f": {elapsed/processed:.1f}s/session")


if __name__ == "__main__":
    main()
