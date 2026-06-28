#!/usr/bin/env python3
"""
 - 

Step 1: Granularity Filtering - 
Step 2: Single-iteration Merge - LLM
Step 3: Count Operations - ID
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
import time
import re

try:
    from openai import OpenAI
except ImportError:
    print("Error: openai package not installed. Run: pip install openai")
    sys.exit(1)


def load_json(file_path: Path) -> Dict:
    """JSON"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(data: Dict, file_path: Path):
    """JSON"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_token_usage(response) -> Dict:
    """OpenAItoken"""
    usage = getattr(response, 'usage', None)
    if usage:
        return {
            'input_tokens': getattr(usage, 'prompt_tokens', 0),
            'output_tokens': getattr(usage, 'completion_tokens', 0),
            'total_tokens': getattr(usage, 'total_tokens', 0)
        }
    return {
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0
    }


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


def count_operations_in_range(start_id: int, end_id: int, ops: List[Dict]) -> int:
    """
    ID
    operationsID
    """
    count = 0

    # Debug: ops
    if not ops:
        return 0

    # Debug: operation
    if len(ops) > 0:
        first_op = ops[0]

    for op in ops:
        op_id = op.get('id')
        if op_id is not None and start_id <= op_id <= end_id:
            count += 1

    # Debug: count0ID
    if count == 0 and len(ops) > 0:
        all_ids = [op.get('id') for op in ops if op.get('id') is not None]
        if all_ids:
            min_id = min(all_ids)
            max_id = max(all_ids)

    return count


def calculate_time_gap(task_a: Dict, task_b: Dict, ops: List[Dict]) -> Optional[float]:
    """"""
    try:
        end_id_a = task_a.get('op_end_id', 0)
        start_id_b = task_b.get('op_start_id', 0)

        # cleaned operations'id''operation_id'
        end_op_a = next((op for op in ops if op.get('id') == end_id_a), None)
        start_op_b = next((op for op in ops if op.get('id') == start_id_b), None)

        if not end_op_a or not start_op_b:
            return None

        # timestampISO
        time_a = end_op_a.get('timestamp')
        time_b = start_op_b.get('timestamp')

        if time_a is None or time_b is None:
            return None

        # 
        return (time_b - time_a) / 1000.0
    except:
        return None


################################################################################
# STEP 1: GRANULARITY FILTERING ()
################################################################################

def find_containment_relationships(tasks: List[Dict]) -> List[Tuple[int, int, float]]:
    """
    Task A  Task B
    [(idx_a, idx_b, span_ratio), ...]

    AIDBspan
    - ABstart_a <= start_b and end_a >= end_b
    - AB
    """
    containments = []

    for i, task_a in enumerate(tasks):
        start_a = task_a.get('op_start_id', 0)
        end_a = task_a.get('op_end_id', 0)
        span_a = end_a - start_a

        if span_a == 0:
            continue

        for j, task_b in enumerate(tasks):
            if i == j:
                continue

            start_b = task_b.get('op_start_id', 0)
            end_b = task_b.get('op_end_id', 0)
            span_b = end_b - start_b

            if span_b == 0:
                continue

            # ABAIDB
            # startend
            if start_a <= start_b and end_a >= end_b:
                # 
                if not (start_a == start_b and end_a == end_b):
                    ratio = span_a / span_b if span_b > 0 else 0
                    containments.append((i, j, ratio))

    return containments


def execute_granularity_filtering_simplified(
    tasks: List[Dict],
    ops: List[Dict]
) -> List[Dict]:
    """
    
    
    """
    containments = find_containment_relationships(tasks)

    if not containments:
        return tasks

    # 
    to_remove = set()
    for idx_a, idx_b, ratio in containments:
        to_remove.add(idx_a)

    # 
    if to_remove:
        for idx in sorted(to_remove, reverse=True):
            tasks.pop(idx)

    return tasks


################################################################################
# STEP 2: SINGLE-ITERATION MERGE ()
################################################################################

def check_overlap(task_a: Dict, task_b: Dict) -> bool:
    """"""
    end_a = task_a.get('op_end_id', 0)
    start_b = task_b.get('op_start_id', 0)
    return start_b <= end_a


def llm_batch_judge_merge(
    client: OpenAI,
    model: str,
    pairs: List[Tuple[int, int, Dict, Dict, bool]],  # is_overlapping
    batch_size: int = 5,
    min_confidence: float = 0.7,
    token_stats: Dict = None
) -> List[Dict]:
    """
    LLMsummary
    LLMsummary
    LLMsummary
    """
    if not pairs:
        return []

    decisions = []
    num_batches = (len(pairs) + batch_size - 1) // batch_size


    for batch_idx in range(num_batches):
        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, len(pairs))
        batch = pairs[start_idx:end_idx]


        # prompt
        prompt_parts = [
            "Analyze the following task pairs.",
            "",
            "For OVERLAPPING tasks (marked with *MUST MERGE*):",
            "- These tasks MUST be merged (already decided)",
            "- Generate a merged summary following the format below",
            "- Set should_merge=true and confidence=1.0",
            "",
            "For ADJACENT tasks (not overlapping):",
            "- Determine if they should be merged based on:",
            "  1. Semantic continuity: Are they part of the same logical work unit?",
            "  2. Temporal coherence: Are they close in time and operation sequence?",
            "  3. Goal alignment: Do they serve the same high-level objective?",
            "- If merging, generate a merged summary following the format below",
            "",
            "SUMMARY FORMAT: [target object] + [specific function/change]",
            '  GOOD: "Add process health check and auto-restart to monitor_mysql.sh"',
            '  BAD:  "Edit monitor_mysql.sh for reliability, test it, and update batch script"',
            "  - Name the concrete file, function, class, or component being changed.",
            "  - State WHAT was done functionally, not HOW.",
            "  - Do NOT chain multiple files/actions with 'and'; pick the primary goal.",
            "",
            "Respond with a JSON array:",
            "[",
            '  {',
            '    "pair_index": 0,',
            '    "should_merge": true/false,',
            '    "merged_summary": "[target] + [specific function/change]" (required if should_merge=true),',
            '    "confidence": 0.0-1.0,',
            '    "reason": "brief explanation"',
            '  },',
            "  ...",
            "]",
            "",
            "Task pairs:",
            ""
        ]

        for local_idx, (idx_a, idx_b, task_a, task_b, is_overlapping) in enumerate(batch):
            start_a = task_a.get('op_start_id', 0)
            end_a = task_a.get('op_end_id', 0)
            start_b = task_b.get('op_start_id', 0)
            end_b = task_b.get('op_end_id', 0)

            overlap_mark = " *MUST MERGE* (overlapping)" if is_overlapping else ""

            prompt_parts.extend([
                f"Pair {local_idx}{overlap_mark}:",
                f"  Task A: [{start_a}-{end_a}]",
                f"    Summary: {task_a.get('summary', 'N/A')[:150]}",
                f"  Task B: [{start_b}-{end_b}]",
                f"    Summary: {task_b.get('summary', 'N/A')[:150]}",
                ""
            ])

        prompt = "\n".join(prompt_parts)

        # LLM
        try:
            max_retries = 3
            retry_delay = 1

            for attempt in range(max_retries):
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "You are an expert at analyzing software development tasks."},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.3,
                        response_format={"type": "json_object"}
                    )

                    raw_content = response.choices[0].message.content
                    result = json.loads(raw_content)

                    # token
                    if token_stats is not None:
                        usage = extract_token_usage(response)
                        token_stats['llm_calls'] += 1
                        token_stats['total_input_tokens'] += usage['input_tokens']
                        token_stats['total_output_tokens'] += usage['output_tokens']
                        token_stats['total_tokens'] += usage['total_tokens']

                    # key
                    batch_decisions = None
                    for key in ['decisions', 'result', 'analysis', 'pairs', 'judgments', 'data']:
                        if key in result:
                            batch_decisions = result[key]
                            break

                    # keyresult
                    if batch_decisions is None:
                        if isinstance(result, list):
                            batch_decisions = result
                        else:
                            # valuekey
                            if len(result) == 1:
                                batch_decisions = list(result.values())[0]
                            else:
                                batch_decisions = []

                    if not isinstance(batch_decisions, list):
                        batch_decisions = [batch_decisions]


                    for local_idx, decision in enumerate(batch_decisions):
                        if local_idx < len(batch):
                            idx_a, idx_b, _, _, is_overlapping = batch[local_idx]
                            should_merge = decision.get('should_merge', False)
                            confidence = decision.get('confidence', 0.0)
                            reason = decision.get('reason', 'No reason provided')
                            merged_summary = decision.get('merged_summary', '')

                            if should_merge and confidence >= min_confidence:
                                decision['approved'] = True
                            else:
                                decision['approved'] = False

                            decision['pair_index'] = start_idx + local_idx
                            decision['idx_a'] = idx_a
                            decision['idx_b'] = idx_b
                            decision['merged_summary'] = merged_summary
                            decisions.append(decision)

                    break

                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        retry_delay *= 2
                    else:
                        raise

        except Exception as e:
            # LLM call failed, skip this batch
            pass

    return decisions


def merge_tasks_single_iteration(
    tasks: List[Dict],
    ops: List[Dict],
    client: OpenAI,
    model: str,
    max_time_gap: int = 20,
    max_id_gap: int = 10,
    min_confidence: float = 0.7,
    token_stats: Dict = None
) -> List[Dict]:
    """
    
    - 
    - LLM
    """

    # 

    overlapping_pairs = []  # 
    adjacent_pairs = []     # LLM

    for i in range(len(tasks) - 1):
        task_a = tasks[i]
        task_b = tasks[i + 1]

        start_a = task_a.get('op_start_id', 0)
        end_a = task_a.get('op_end_id', 0)
        start_b = task_b.get('op_start_id', 0)
        end_b = task_b.get('op_end_id', 0)
        span_a = end_a - start_a
        span_b = end_b - start_b

        summary_a = task_a.get('summary', 'N/A')[:70]
        summary_b = task_b.get('summary', 'N/A')[:70]


        # 
        is_overlapping = check_overlap(task_a, task_b)

        if is_overlapping:
            overlapping_pairs.append((i, i + 1))
        else:
            # 
            id_gap = start_b - end_a
            id_close = id_gap <= max_id_gap

            time_gap = calculate_time_gap(task_a, task_b, ops)
            time_close = time_gap is not None and time_gap <= max_time_gap


            if time_gap is not None:
                pass
            else:
                pass

            # ORID
            if id_close or time_close:
                reason = "ID" if id_close else "Time"
                if id_close and time_close:
                    reason = "ID+Time"
                adjacent_pairs.append((i, i + 1))
            else:
                pass



    # 
    approved_pairs = []

    if overlapping_pairs:
        pass

        for idx_a, idx_b in overlapping_pairs:
            approved_pairs.append((idx_a, idx_b))


    # LLM
    all_pairs = []

    # 
    for idx_a, idx_b in overlapping_pairs:
        all_pairs.append((idx_a, idx_b, tasks[idx_a], tasks[idx_b], True))  # True = overlapping

    # LLM
    for idx_a, idx_b in adjacent_pairs:
        all_pairs.append((idx_a, idx_b, tasks[idx_a], tasks[idx_b], False))  # False = not overlapping

    if all_pairs:
        decisions = llm_batch_judge_merge(client, model, all_pairs, min_confidence=min_confidence, token_stats=token_stats)

        # merged_summary
        merge_info = {}  # {(idx_a, idx_b): merged_summary}

        for decision in decisions:
            if decision.get('approved', False):
                idx_a = decision['idx_a']
                idx_b = decision['idx_b']
                merged_summary = decision.get('merged_summary', '')
                approved_pairs.append((idx_a, idx_b))
                merge_info[(idx_a, idx_b)] = merged_summary


    if not approved_pairs:
        return tasks

    # 

    # (a,b), (b,c), (c,d)
    # 

    # 
    merge_graph = {}  # {task_idx: [adjacent_task_indices]}
    for idx_a, idx_b in approved_pairs:
        if idx_a not in merge_graph:
            merge_graph[idx_a] = []
        if idx_b not in merge_graph:
            merge_graph[idx_b] = []
        merge_graph[idx_a].append(idx_b)

    # 
    visited = set()
    merge_chains = []

    def find_chain(start_idx):
        """DFS"""
        chain = set()
        stack = [start_idx]
        while stack:
            idx = stack.pop()
            if idx in visited:
                continue
            visited.add(idx)
            chain.add(idx)
            # 
            for neighbor in merge_graph.get(idx, []):
                if neighbor not in visited:
                    stack.append(neighbor)
            # 
            for other_idx, neighbors in merge_graph.items():
                if idx in neighbors and other_idx not in visited:
                    stack.append(other_idx)
        return sorted(chain)

    for idx in merge_graph.keys():
        if idx not in visited:
            chain = find_chain(idx)
            merge_chains.append(chain)

    for i, chain in enumerate(merge_chains):
        pass


    # 
    merge_chains.sort(key=lambda chain: min(chain), reverse=True)

    for chain in merge_chains:
        if len(chain) < 2:
            continue

        # 
        chain_tasks = [tasks[idx] for idx in chain if idx < len(tasks)]

        if len(chain_tasks) < 2:
            continue

        # 
        min_idx = min(chain)
        max_idx = max(chain)


        # 
        for idx in chain:
            if idx >= len(tasks):
                continue
            task = tasks[idx]
            start = task.get('op_start_id', 0)
            end = task.get('op_end_id', 0)

        # LLMsummarysummary
        first_pair = (chain[0], chain[1]) if len(chain) >= 2 else (chain[0], chain[0])
        llm_summary = merge_info.get(first_pair, '')

        if not llm_summary:
            # summary
            for i in range(len(chain) - 1):
                pair_summary = merge_info.get((chain[i], chain[i+1]), '')
                if pair_summary:
                    llm_summary = pair_summary
                    break

        # ID
        all_starts = [tasks[idx].get('op_start_id', 0) for idx in chain if idx < len(tasks)]
        all_ends = [tasks[idx].get('op_end_id', 0) for idx in chain if idx < len(tasks)]

        merged_start = min(all_starts)
        merged_end = max(all_ends)

        if llm_summary:
            final_summary = llm_summary
        else:
            # summary
            all_summaries = [tasks[idx].get('summary', '') for idx in chain if idx < len(tasks)]
            final_summary = ' '.join(all_summaries).strip()[:200]

        # detail
        all_details = [tasks[idx].get('detail', '') for idx in chain if idx < len(tasks)]
        all_details = [d for d in all_details if d.strip()]
        final_detail = '\n'.join(all_details)

        # 
        merged_task = {
            'op_start_id': merged_start,
            'op_end_id': merged_end,
            'summary': final_summary,
            'detail': final_detail
        }


        # 
        tasks[min_idx] = merged_task

        # 
        for idx in sorted(chain, reverse=True):
            if idx != min_idx and idx < len(tasks):
                tasks.pop(idx)



    # 
    for i, task in enumerate(tasks):
        start = task.get('op_start_id', 0)
        end = task.get('op_end_id', 0)
        span = end - start
        summary = task.get('summary', 'No summary')[:70]


    return tasks


################################################################################
# STEP 4: SPLIT OVERSIZED TASKS ()
################################################################################


def _even_split_fallback(task_ops, task, num_ops, min_ops, max_ops):
    """LLM"""
    # subtasksubtask max_ops * 0.8 operations
    target_size = int(max_ops * 0.8)  # 40
    n_subtasks = max(2, (num_ops + target_size - 1) // target_size)

    # 
    base_size = num_ops // n_subtasks
    remainder = num_ops % n_subtasks

    split_positions = []  # 0-based position indices for slicing task_ops
    pos = 0
    for i in range(n_subtasks - 1):
        size = base_size + (1 if i < remainder else 0)
        pos += size
        if pos < len(task_ops):
            split_positions.append(pos)

    # subtask_infos
    subtask_infos = [{'summary': f'Even split part {i+1}/{n_subtasks}', 'detail': ''} for i in range(n_subtasks)]

    sizes = []
    prev = 0
    for i in range(n_subtasks):
        size = base_size + (1 if i < remainder else 0)
        sizes.append(size)
        prev += size

    print(f"  ✅ Even split: {n_subtasks} subtasks, sizes: {sizes}")

    return {
        'split_positions': split_positions,
        'subtask_infos': subtask_infos
    }


def llm_split_oversized_task(
    client: OpenAI,
    model: str,
    task: Dict,
    task_ops: List[Dict],
    token_stats: Dict = None
) -> Optional[Dict]:
    """
    LLM
    
    {
        'split_positions': [50, 100],  # 0-based position indices for slicing task_ops
        'subtask_infos': [
            {'summary': '...', 'detail': '...'},
            {'summary': '...', 'detail': '...'},
            {'summary': '...', 'detail': '...'}
        ]
    }
    """

    # 
    num_ops = len(task_ops)
    MIN_OPS_PER_SUBTASK = 10
    MAX_OPS_PER_SUBTASK = 50

    # prompt - 
    # subtask
    min_subtasks = max(2, (num_ops + MAX_OPS_PER_SUBTASK - 1) // MAX_OPS_PER_SUBTASK)
    max_subtasks = num_ops // MIN_OPS_PER_SUBTASK

    prompt = f"""You MUST split this task into smaller subtasks.

########## HIGHEST PRIORITY RULE - READ THIS FIRST ##########
Each subtask MUST have between {MIN_OPS_PER_SUBTASK} and {MAX_OPS_PER_SUBTASK} operations.
This rule has ABSOLUTE PRIORITY over everything else.
Semantic boundaries are IRRELEVANT if they violate this rule.
If you must choose between a clean semantic boundary and satisfying the {MIN_OPS_PER_SUBTASK}-{MAX_OPS_PER_SUBTASK} constraint, ALWAYS choose the constraint.
ANY response where a subtask has <{MIN_OPS_PER_SUBTASK} or >{MAX_OPS_PER_SUBTASK} operations is INVALID and will be REJECTED.
##############################################################

This task has {num_ops} operations (numbered 1 to {num_ops}).
You MUST create between {min_subtasks} and {max_subtasks} subtasks.

Current task summary: {task.get('summary', 'N/A')}

STEP-BY-STEP INSTRUCTIONS:
1. Calculate: {num_ops} / {MAX_OPS_PER_SUBTASK} = {num_ops / MAX_OPS_PER_SUBTASK:.1f}, so you need at least {min_subtasks} subtasks.
2. Choose split points so that EVERY subtask has {MIN_OPS_PER_SUBTASK}-{MAX_OPS_PER_SUBTASK} operations.
3. VERIFY by counting: for each subtask, compute (end_position - start_position + 1). It MUST be between {MIN_OPS_PER_SUBTASK} and {MAX_OPS_PER_SUBTASK}.
4. If any subtask violates the constraint, ADJUST your split points until ALL subtasks are valid.

########## SUMMARY & DETAIL FORMAT ##########
"summary" — one sentence: [target object] + [specific function/change].
  GOOD: "Add process health check and auto-restart to monitor_mysql.sh"
  BAD:  "Edit monitor_mysql.sh for reliability, test it, and update batch script"
  - Name the concrete file, function, class, or component.
  - State WHAT was done functionally, not HOW.
  - Do NOT chain multiple files/actions with "and"; pick the primary goal.

"detail" — structured workflow using ONLY these operators:
  Edit(<file/function>): <what specifically changed>
  User_Request: <what the user explicitly asked or intended>
  Execute(<command>): <what was run and the outcome>
  Search(<scope>): <what was looked up>
  - Omit non-semantic actions: Open, Close, Select, Cursor, scroll, navigate.
  - Omit line numbers.
  - Be specific about WHAT changed.
  - Keep chronological order; one operator per logical step.
  - Number each step starting from 1.
  Example:
    1. Edit(utils.py/parse_config): add timeout parameter with default 30s
    2. Execute(python test.py): run unit tests, all passed
    3. Edit(utils.py/parse_config): fix typo in docstring
##############################################

Response format (JSON):
{{
  "split_points": [position_1, position_2, ...],
  "reason": "Verification: subtask1 has X ops, subtask2 has Y ops, ... All within {MIN_OPS_PER_SUBTASK}-{MAX_OPS_PER_SUBTASK}.",
  "subtasks": [
    {{
      "range": "1-position_1",
      "summary": "[target] + [specific function/change]",
      "detail": "1. Edit(...): ...\\n2. Execute(...): ...\\n..."
    }},
    {{
      "range": "position_1+1 - position_2",
      "summary": "[target] + [specific function/change]",
      "detail": "1. Edit(...): ...\\n2. Execute(...): ...\\n..."
    }}
  ]
}}

RULES:
1. split_points are 1-based position numbers in the operations list (NOT operation IDs).
2. All summary and detail fields MUST be in English.
3. In "reason", you MUST list the operation count for EVERY subtask to prove compliance.
4. REMINDER: {MIN_OPS_PER_SUBTASK} <= operations per subtask <= {MAX_OPS_PER_SUBTASK}. NO EXCEPTIONS.

Operations list:
"""

    CHAR_LIMIT = 350000
    TOKEN_LIMIT = 90000
    LIGHT_COMPRESS_TRIGGER = 0.70

    def _build_ops_block(use_light_compression):
        block = ""
        for i, op in enumerate(task_ops):
            op_id = op.get('id')
            op_type = op.get('operationType')
            if use_light_compression:
                details = light_compress_readable_details(op)
            else:
                details = op.get('readableDetails', '')
            block += f"\n[{i+1}] Operation ID: {op_id}\n"
            block += f"Type: {op_type}\n"
            block += f"Details:\n{details}\n"
            block += "-" * 80 + "\n"
        return block

    # prompt
    prompt_mode = "full"
    prompt_full = prompt + _build_ops_block(use_light_compression=False)
    prompt_char_count, token_count = estimate_prompt_size(prompt_full)
    usage_ratio = max(prompt_char_count / CHAR_LIMIT, token_count / TOKEN_LIMIT)

    # 70%
    if usage_ratio >= LIGHT_COMPRESS_TRIGGER:
        prompt_mode = "light_compressed"
        prompt_full = prompt + _build_ops_block(use_light_compression=True)
        prompt_char_count, token_count = estimate_prompt_size(prompt_full)
        usage_ratio = max(prompt_char_count / CHAR_LIMIT, token_count / TOKEN_LIMIT)

    prompt = prompt_full

    # 
    skip_reason_detail = None
    if prompt_char_count > CHAR_LIMIT and token_count > TOKEN_LIMIT:
        skip_reason_detail = f"both limits exceeded ({prompt_char_count:,} chars > {CHAR_LIMIT:,}, {token_count:,} tokens > {TOKEN_LIMIT:,})"
    elif prompt_char_count > CHAR_LIMIT:
        skip_reason_detail = f"character limit exceeded ({prompt_char_count:,} chars > {CHAR_LIMIT:,})"
    elif token_count > TOKEN_LIMIT:
        skip_reason_detail = f"token limit exceeded ({token_count:,} tokens > {TOKEN_LIMIT:,})"

    if skip_reason_detail:
        print(f"  ⏭️  Prompt too long ({prompt_mode}): {skip_reason_detail}")
        print(f"  🔧 Falling back to even split...")
        return _even_split_fallback(task_ops, task, num_ops, MIN_OPS_PER_SUBTASK, MAX_OPS_PER_SUBTASK)

    print(
        f"  📊 Prompt size ({prompt_mode}): "
        f"{token_count:,} tokens ({token_count*100//TOKEN_LIMIT}%), "
        f"{prompt_char_count:,} chars ({prompt_char_count*100//CHAR_LIMIT}%)"
    )

    # Debug: prompt

    # LLM
    MAX_RETRY_ATTEMPTS = 3

    for attempt in range(MAX_RETRY_ATTEMPTS):
        try:
            if attempt > 0:
                print(f"  🔄 Retry attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}")

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are an expert at analyzing software development workflows and identifying natural task boundaries."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
            )

            raw_content = response.choices[0].message.content or ""
            #  JSON json_object 
            result = None
            # 
            try:
                result = json.loads(raw_content)
            except json.JSONDecodeError:
                pass
            #  markdown code block
            if result is None:
                blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n\s*```', raw_content, re.DOTALL)
                for block in blocks:
                    try:
                        result = json.loads(block)
                        break
                    except json.JSONDecodeError:
                        continue
            #  JSON 
            if result is None:
                m = re.search(r'\{.*\}', raw_content, re.DOTALL)
                if m:
                    try:
                        result = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass
            if result is None:
                raise ValueError(f"Failed to parse JSON from response: {raw_content[:200]!r}")

            # token
            if token_stats is not None:
                usage = extract_token_usage(response)
                token_stats['llm_calls'] += 1
                token_stats['total_input_tokens'] += usage['input_tokens']
                token_stats['total_output_tokens'] += usage['output_tokens']
                token_stats['total_tokens'] += usage['total_tokens']

            split_positions_raw = result.get('split_points', [])
            reason = result.get('reason', '')
            subtasks = result.get('subtasks', [])

            if not split_positions_raw:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    print(f"  ⚠ No split_points returned, retrying...")
                    continue
                return None

            # 1-based0-basedtask_ops
            split_positions = []
            for pos in split_positions_raw:
                # pos1-based0-based
                # pos=5050task_ops[:50]task_ops[50:]
                if 0 < pos < len(task_ops):
                    split_positions.append(pos)

            if not split_positions:
                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    print(f"  ⚠ Invalid split_positions, retrying...")
                    continue
                return None

            # ===== 10-50 =====
            # split_positions1-basedoperation
            # operation IDsID
            sorted_positions = sorted(split_positions_raw)
            boundaries = [0] + sorted_positions + [num_ops]

            validation_failed = False
            for i in range(len(boundaries) - 1):
                range_ops = boundaries[i + 1] - boundaries[i]
                if range_ops < MIN_OPS_PER_SUBTASK or range_ops > MAX_OPS_PER_SUBTASK:
                    if attempt < MAX_RETRY_ATTEMPTS - 1:
                        print(f"  ❌ Validation failed: subtask {i+1} has {range_ops} ops (must be {MIN_OPS_PER_SUBTASK}-{MAX_OPS_PER_SUBTASK})")
                        validation_failed = True
                        break
                    else:
                        print(f"  ❌ Validation failed after {MAX_RETRY_ATTEMPTS} attempts: subtask {i+1} has {range_ops} ops")
                        print(f"  🔧 Falling back to even split...")
                        return _even_split_fallback(task_ops, task, num_ops, MIN_OPS_PER_SUBTASK, MAX_OPS_PER_SUBTASK)

            if validation_failed:
                continue  # 

            # 
            if attempt > 0:
                print(f"  ✅ Validation passed on attempt {attempt + 1}")
            else:
                print(f"  ✅ Validation passed")

            # subtask_infossummarydetail
            subtask_infos = []
            if subtasks:
                for i, st in enumerate(subtasks):
                    subtask_infos.append({
                        'summary': st.get('summary', ''),
                        'detail': st.get('detail', '')
                    })

            return {
                'split_positions': split_positions,
                'subtask_infos': subtask_infos
            }

        except Exception as e:
            error_msg = str(e)
            if attempt < MAX_RETRY_ATTEMPTS - 1:
                print(f"  ⚠ Error on attempt {attempt + 1}: {error_msg}, retrying...")
                continue
            else:
                print(f"  ❌ Error after {MAX_RETRY_ATTEMPTS} attempts: {error_msg}")
                print(f"  🔧 Falling back to even split...")
                return _even_split_fallback(task_ops, task, num_ops, MIN_OPS_PER_SUBTASK, MAX_OPS_PER_SUBTASK)

    # 
    print(f"  🔧 Falling back to even split...")
    return _even_split_fallback(task_ops, task, num_ops, MIN_OPS_PER_SUBTASK, MAX_OPS_PER_SUBTASK)


def split_oversized_tasks(
    tasks: List[Dict],
    ops: List[Dict],
    client: OpenAI,
    model: str,
    threshold: int = 80,
    token_stats: Dict = None
) -> List[Dict]:
    """
    
    """

    # 
    to_split = []
    for i, task in enumerate(tasks):
        op_count = task.get('operation_count', 0)
        if op_count > threshold:
            print(f"  🔨 Will split task {i} with {op_count} operations")
            to_split.append((i, task, op_count))

    if not to_split:
        return tasks

    print(f"📋 Step 4: Splitting {len(to_split)} oversized tasks (threshold={threshold})...")

    for i, task, op_count in to_split:
        start = task.get('op_start_id', 0)
        end = task.get('op_end_id', 0)

    # 
    new_tasks = []
    split_count = 0
    skipped_long_tasks = []  # 

    for task_idx, task, op_count in to_split:
        start_id = task.get('op_start_id', 0)
        end_id = task.get('op_end_id', 0)


        # operations
        task_ops = [op for op in ops if start_id <= op.get('id', -1) <= end_id]
        task_ops.sort(key=lambda x: x.get('id', 0))


        # LLM
        split_result = llm_split_oversized_task(client, model, task, task_ops, token_stats)

        if not split_result:
            print(f"⚠ Warning: Failed to split task with {op_count} operations (LLM call failed)")
            new_tasks.append(task)
            continue

        # 
        if 'skip_reason' in split_result:
            skip_reason = split_result.get('skip_reason')
            num_ops_skipped = split_result.get('num_ops', op_count)
            error_message = split_result.get('error_message', '')

            skip_info = {
                'task_index': task_idx,
                'op_range': f"[{start_id}-{end_id}]",
                'operation_count': op_count,
                'skip_reason': skip_reason
            }

            # 
            if error_message:
                skip_info['error_message'] = error_message
                print(f"  📝 Recorded task with {op_count} operations (Error: {error_message[:100]})")
            else:
                print(f"  📝 Recorded task with {op_count} operations for manual processing")

            skipped_long_tasks.append(skip_info)
            new_tasks.append(task)  # 
            continue

        split_positions = split_result.get('split_positions', [])
        subtask_infos = split_result.get('subtask_infos', [])

        if not split_positions:
            print(f"⚠ Warning: Failed to split task with {op_count} operations (no split positions returned)")
            new_tasks.append(task)
            continue

        # positiontask_ops
        sorted_positions = sorted(split_positions)
        # boundaries: [0, pos1, pos2, ..., len(task_ops)]
        boundaries = [0] + sorted_positions + [len(task_ops)]

        for i in range(len(boundaries) - 1):
            chunk_start = boundaries[i]
            chunk_end = boundaries[i + 1]
            chunk_ops = task_ops[chunk_start:chunk_end]
            range_op_count = len(chunk_ops)

            # operationID
            range_start_id = chunk_ops[0].get('id') if chunk_ops else 0
            range_end_id = chunk_ops[-1].get('id') if chunk_ops else 0

            # LLMsummarydetail
            if i < len(subtask_infos):
                llm_summary = subtask_infos[i].get('summary', '')
                llm_detail = subtask_infos[i].get('detail', '')
            else:
                llm_summary = ''
                llm_detail = ''

            # LLM
            if not llm_summary:
                llm_summary = f"{task.get('summary', 'N/A')} (part {i+1}/{len(boundaries)-1})"
            if not llm_detail:
                llm_detail = f"Part {i+1} of the original task."

            subtask = {
                'op_start_id': range_start_id,
                'op_end_id': range_end_id,
                'summary': llm_summary,
                'detail': llm_detail,
                'operation_count': range_op_count,
                'id_span': range_end_id - range_start_id
            }

            # 
            if range_op_count > threshold:
                print(f"  ⚠ Warning: Subtask {i+1} still has {range_op_count} operations (>{threshold}) after split")

            new_tasks.append(subtask)

        # operation
        subtask_sizes = [boundaries[i+1] - boundaries[i] for i in range(len(boundaries) - 1)]
        subtask_ops_str = ', '.join([str(s) for s in subtask_sizes])
        print(f"  ✅ Split task with {op_count} ops into {len(subtask_sizes)} subtasks: [{subtask_ops_str}]")
        split_count += 1

    # 
    for i, task in enumerate(tasks):
        if not any(idx == i for idx, _, _ in to_split):
            new_tasks.append(task)

    # op_start_id
    new_tasks.sort(key=lambda x: x.get('op_start_id', 0))

    # 50operations
    oversized_remaining = [t for t in new_tasks if t.get('operation_count', 0) > 50]
    if oversized_remaining:
        for t in oversized_remaining:
            print(f"⚠ Warning: Task still has {t.get('operation_count')} operations after split attempt")

    # token_stats
    if token_stats is not None and skipped_long_tasks:
        token_stats['skipped_long_tasks'] = skipped_long_tasks
        print(f"📝 Total {len(skipped_long_tasks)} tasks skipped due to length, recorded for manual processing")

    return new_tasks


################################################################################
# STEP 3: COUNT OPERATIONS ()
################################################################################

def count_and_report_operations(tasks: List[Dict], ops: List[Dict]) -> List[Dict]:
    """
    
    """

    if len(ops) > 0:
        pass


    for i, task in enumerate(tasks):
        start_id = task.get('op_start_id', 0)
        end_id = task.get('op_end_id', 0)
        id_span = end_id - start_id

        # 
        actual_count = count_operations_in_range(start_id, end_id, ops)

        # 
        task['operation_count'] = actual_count
        task['id_span'] = id_span

        summary = task.get('summary', 'No summary')[:70]

        if id_span > 0:
            coverage = (actual_count / id_span) * 100


    total_actual = sum(task.get('operation_count', 0) for task in tasks)
    total_span = sum(task.get('id_span', 0) for task in tasks)

    if total_span > 0:
        pass


    # 
    sorted_tasks = sorted(enumerate(tasks), key=lambda x: x[1].get('operation_count', 0), reverse=True)

    for rank, (original_idx, task) in enumerate(sorted_tasks, 1):
        op_count = task.get('operation_count', 0)
        id_span = task.get('id_span', 0)
        start_id = task.get('op_start_id', 0)
        end_id = task.get('op_end_id', 0)



    return tasks


################################################################################
# MAIN PROCESSING
################################################################################

def process_session(
    aggregated_file: Path,
    aggregated_dir: Path,
    original_ops_dir: Path,
    output_dir: Path,
    client: OpenAI,
    model: str
) -> Optional[Dict]:
    """session"""

    session_id = aggregated_file.stem.replace('_aggregated', '')

    # sessiondebug
    import sys
    try:
        real_agg_file = aggregated_file.resolve()
        real_agg_dir = aggregated_dir.resolve()
        relative_path = real_agg_file.relative_to(real_agg_dir)
        debug_log = output_dir / relative_path.parent / f"{session_id}_debug.log"
    except ValueError:
        # 
        debug_log = output_dir / f"{session_id}_debug.log"

    debug_log.parent.mkdir(parents=True, exist_ok=True)

    # stdout
    class TeeOutput:
        def __init__(self, *files):
            self.files = files
        def write(self, data):
            for f in self.files:
                f.write(data)
                f.flush()
        def flush(self):
            for f in self.files:
                f.flush()

    original_stdout = sys.stdout
    log_file = open(debug_log, 'w', encoding='utf-8')
    sys.stdout = TeeOutput(original_stdout, log_file)

    try:
        # Token
        token_stats = {
            'total_input_tokens': 0,
            'total_output_tokens': 0,
            'total_tokens': 0,
            'llm_calls': 0
        }

        # aggregated
        aggregated_data = load_json(aggregated_file)
        tasks = aggregated_data.get('tasks', [])

        # taskop_start_idop_end_idraw_text
        invalid_count = 0
        valid_tasks = []
        for t in tasks:
            if 'raw_text' in t and 'op_start_id' not in t:
                invalid_count += 1
                continue
            if 'op_start_id' not in t or 'op_end_id' not in t:
                invalid_count += 1
                continue
            valid_tasks.append(t)
        if invalid_count > 0:
            print(f"  ⚠ Filtered {invalid_count} invalid tasks (missing op_start_id/op_end_id or raw_text)")
        tasks = valid_tasks

        # tasks
        if not tasks:
            print(f"⚠ No tasks, saving original data")

            # 
            try:
                real_agg_file = aggregated_file.resolve()
                real_agg_dir = aggregated_dir.resolve()
                relative_path = real_agg_file.relative_to(real_agg_dir)
                output_file = output_dir / relative_path.parent / f"{session_id}_refined.json"
            except ValueError:
                output_file = output_dir / f"{session_id}_refined.json"

            # aggregatedprocessing_info
            result_data = aggregated_data.copy()
            result_data['processing_info'] = {
                'initial_count': 0,
                'final_count': 0,
                'reduction_pct': 0,
                'step3_tasks': [],
                'final_tasks': [],
                'token_usage': {
                    'llm_calls': 0,
                    'total_input_tokens': 0,
                    'total_output_tokens': 0,
                    'total_tokens': 0
                }
            }
            save_json(result_data, output_file)
            return result_data

        # operations
        # cleaned_data/.../operations/.../YYYY-MM-DD/session_XXX.json
        matching_files = list(original_ops_dir.rglob(f"**/{session_id}.json"))

        if not matching_files:
            print(f"⚠ Original ops file not found for session: {session_id}")
            ops = []
        elif len(matching_files) > 1:
            # 
            print(f"⚠ Multiple files found for session {session_id}, attempting to match by date path...")

            # aggregated_fileYYYY-MM-DD
            import re
            agg_path_str = str(aggregated_file)
            date_pattern = r'(\d{4}-\d{2}-\d{2})'
            date_match = re.search(date_pattern, agg_path_str)

            selected_file = None
            if date_match:
                date_str = date_match.group(1)
                print(f"  Aggregated file date: {date_str}")

                # operations
                for f in matching_files:
                    if date_str in str(f):
                        selected_file = f
                        print(f"  ✓ Selected file with matching date: {f.relative_to(original_ops_dir)}")
                        break

            # 
            if not selected_file:
                selected_file = matching_files[0]
                print(f"  ⚠ No date match found, using first file: {selected_file.relative_to(original_ops_dir)}")
                print(f"  Available files:")
                for f in matching_files:
                    print(f"    - {f.relative_to(original_ops_dir)}")

            original_ops_file = selected_file
            original_data = load_json(original_ops_file)
            ops = original_data.get('operations', [])
        else:
            original_ops_file = matching_files[0]
            original_data = load_json(original_ops_file)
            ops = original_data.get('operations', [])


        initial_count = len(tasks)

        # STEP 1: Granularity Filtering (→)
        tasks = execute_granularity_filtering_simplified(tasks, ops)

        # STEP 2: Single-iteration Merge ()
        tasks = merge_tasks_single_iteration(
        tasks, ops, client, model,
        max_time_gap=20,  # 20
        max_id_gap=10,
        min_confidence=0.7,
        token_stats=token_stats
        )

        # STEP 3: Count Operations ()
        tasks = count_and_report_operations(tasks, ops)

        # Step 3operation counts
        print(f"DEBUG: After Step 3, operation counts: {[t.get('operation_count', 0) for t in tasks]}")

        # Step 3tasks
        step3_tasks_info = [
        {
            'task_index': i,
            'op_range': f"[{t.get('op_start_id')}-{t.get('op_end_id')}]",
            'operation_count': t.get('operation_count', 0)
        }
        for i, t in enumerate(tasks)
        ]

        # STEP 4: Split Oversized Tasks ()
        print(f"DEBUG: Before Step 4, entering split_oversized_tasks with {len(tasks)} tasks")
        tasks = split_oversized_tasks(tasks, ops, client, model, threshold=50, token_stats=token_stats)
        print(f"DEBUG: After Step 4, returned {len(tasks)} tasks")

        # tasks
        final_tasks_info = [
        {
            'task_index': i,
            'op_range': f"[{t.get('op_start_id')}-{t.get('op_end_id')}]",
            'operation_count': t.get('operation_count', 0)
        }
        for i, t in enumerate(tasks)
        ]

        #  session ID
        print(f"{session_id}: {initial_count}→{len(tasks)} tasks, tokens: {token_stats['total_tokens']}")


        final_count = len(tasks)
        reduction = ((initial_count - final_count) / initial_count * 100) if initial_count > 0 else 0

        # 
        result_data = {
        'session_id': session_id,
        'tasks': tasks,
        'processing_info': {
            'initial_count': initial_count,
            'final_count': final_count,
            'reduction_pct': round(reduction, 2),
            'step3_tasks': step3_tasks_info,
            'final_tasks': final_tasks_info,
            'token_usage': {
                'llm_calls': token_stats['llm_calls'],
                'total_input_tokens': token_stats['total_input_tokens'],
                'total_output_tokens': token_stats['total_output_tokens'],
                'total_tokens': token_stats['total_tokens']
            },
            'skipped_long_tasks': token_stats.get('skipped_long_tasks', [])
        }
        }

        # 
        # 
        try:
            real_agg_file = aggregated_file.resolve()
            real_agg_dir = aggregated_dir.resolve()
            relative_path = real_agg_file.relative_to(real_agg_dir)
            #  session_xxx_aggregated.json  session_xxx_refined.json
            output_file = output_dir / relative_path.parent / f"{session_id}_refined.json"
        except ValueError:
            # 
            output_file = output_dir / f"{session_id}_refined.json"

        save_json(result_data, output_file)

        return result_data

    finally:
        # stdout
        sys.stdout = original_stdout
        log_file.close()


def main():
    parser = argparse.ArgumentParser(description='Merge and refine tasks (Simplified approach)')
    parser.add_argument('--aggregated-dir', type=str, required=True,
                        help='Directory containing aggregated results')
    parser.add_argument('--original-ops-dir', type=str, required=True,
                        help='Directory containing original operation files')
    parser.add_argument('--output-dir', type=str, required=True,
                        help='Output directory for refined results')
    parser.add_argument('--single-file', type=str,
                        help='Process only this specific aggregated file')
    parser.add_argument('--api-key', type=str,
                        help='DeepSeek API key')
    parser.add_argument('--endpoint', type=str, default=os.environ.get('OPENAI_API_BASE', ''),
                        help='API endpoint (from OPENAI_API_BASE env var)')
    parser.add_argument('--model', type=str, default='deepseek-v3.2',
                        help='Model name (default: deepseek-v3.2)')
    parser.add_argument('--call-llm', action='store_true',
                        help='Enable LLM calls (default: dry-run without LLM)')

    args = parser.parse_args()

    # 
    aggregated_dir = Path(args.aggregated_dir).resolve()
    original_ops_dir = Path(args.original_ops_dir).resolve()
    output_dir = Path(args.output_dir)

    # LLM client
    if args.call_llm:
        if not args.api_key:
            print("Error: --api-key required when --call-llm is enabled")
            sys.exit(1)

        client = OpenAI(
            api_key=args.api_key,
            base_url=args.endpoint
        )
    else:
        print("⚠ Dry-run mode: LLM calls disabled")
        client = None


    # 
    if args.single_file:
        aggregated_files = [Path(args.single_file)]
    else:
        aggregated_files = list(aggregated_dir.rglob('*_aggregated.json'))


    # 
    processed = 0
    skipped = 0
    total_initial = 0
    total_final = 0

    for i, agg_file in enumerate(aggregated_files, 1):
        pass

        if not args.call_llm:
            print("⚠ Skipping (dry-run mode)")
            skipped += 1
            continue

        result = process_session(agg_file, aggregated_dir, original_ops_dir, output_dir, client, args.model)

        if result:
            processed += 1
            total_initial += result['processing_info']['initial_count']
            total_final += result['processing_info']['final_count']
        else:
            skipped += 1

    # 
    if total_initial > 0:
        reduction = (total_initial - total_final) / total_initial * 100


if __name__ == '__main__':
    main()
