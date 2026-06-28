#!/usr/bin/env python3
"""
 training data pipeline —  run_preprocessing_pipeline.py 

Step 2 scene_*.jsonl
  Step 1: Aggregate —  + LLM  aggregate_task_windows.py
  Step 2: Refine    —  refine_task_intervals.py
  Step 3: Classify  — LLM / classify_proactive_tasks.py


  -  scene_*.jsonl session_*.jsonVSCode
  -  event  ID pipeline  operation 

 classify_output 
  {session_id, metadata, operations, positive: [...], negative: [...], classification_info}

:
  python3 synthetic_data/build_synthetic_training_data.py --scene-dir synthetic_data/scenarios/agent_data \\
      --output-dir synthetic_data/scenarios/synthetic_classified --call-llm

  # 
  python3 synthetic_data/build_synthetic_training_data.py --single-scene 0 --call-llm
"""

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

# Make data_processing importable
sys.path.insert(0, str(Path(__file__).parent.parent / "data_processing"))

from aggregate_task_windows import (
    sliding_windows, format_ops_block,
    call_llm as agg_call_llm,
    extract_message_content, parse_content_to_tasks,
    PROMPT_TEMPLATE,
)
from refine_task_intervals import (
    execute_granularity_filtering_simplified,
    merge_tasks_single_iteration,
    count_and_report_operations,
    split_oversized_tasks,
)
from classify_proactive_tasks import (
    get_context_ops, classify_task_with_llm, format_operations_for_prompt,
    DETAIL_GENERATION_PROMPT, POS_CONFIDENCE_THRESHOLD, NEG_CONFIDENCE_THRESHOLD,
    CONTEXT_OPS_BEFORE,
)
from classify_proactive_tasks import call_llm as cls_call_llm

# ===== API  =====
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"

# =====  =====
DEFAULT_WINDOW_SIZE = 50
DEFAULT_WINDOW_STRIDE = 50

# =====  =====
INCLUDE_TAGS = {'events', 'activity', 'env.status'}

# ===== Operation  =====
import re

def classify_operation(content: str) -> str:
    """ event content  operation open_file/edit_text/search"""
    content_lower = content.lower()
    if re.search(r'\[open_file\]|opens?\s+.*file|opens?\s+.*\.(?:py|js|ts|java|go|rs|c|cpp|h|html|css|json|yaml|yml|md|toml)', content_lower):
        return 'open_file'
    if re.search(r'\[edit_text\]|edits?\s|modif|chang|add.*line|remov.*line|insert|delet|updat.*code|refactor|writ.*code|implement', content_lower):
        return 'edit_text'
    if re.search(r'\[search\]|search|find|grep|look.*for|locate|query', content_lower):
        return 'search'
    #  edit_text
    return 'edit_text'


# ============================================================================
#  → pipeline  operation 
# ============================================================================

def load_scene(filepath: str) -> List[Dict]:
    """ scene jsonl """
    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def events_to_operations(records: List[Dict]) -> List[Dict]:
    """
     scene records  pipeline  operation 
    -  events env  IDE 
    -  activity user agent 
    - Initial Environment Settings
    - Entity Updated.
    -  ID
    -  operationType  open_file / edit_text / search
    """
    ops = []
    op_id = 0
    for r in records:
        tags = set(r.get('tags', []))

        #  events env 
        if 'events' not in tags:
            continue

        content = r.get('content', '')

        # 
        if '! Initial Environment Settings !' in content:
            continue
        #  entity 
        if content.strip().startswith('Entity Updated.'):
            continue
        # 
        if len(content.strip()) < 20:
            continue

        op_type = classify_operation(content)

        ops.append({
            'id': op_id,
            'operationType': op_type,
            'timestamp': r.get('time', ''),
            'readableDetails': content,
        })
        op_id += 1
    return ops


# ============================================================================
# Step 1: Aggregate aggregate_task_windows.py 
# ============================================================================

def step_aggregate(
    ops: List[Dict],
    llm_config: Dict,
    window_size: int,
    window_stride: int,
) -> List[Dict]:
    """ + LLM → tasks with op_start_id/op_end_id"""
    client = None
    rate_state = {'next_allowed_ts': 0.0}
    if llm_config.get('call_llm') and llm_config.get('api_key'):
        from openai import OpenAI
        client = OpenAI(api_key=llm_config['api_key'], base_url=llm_config['endpoint'])

    tasks: List[Dict] = []
    max_detail_chars = llm_config.get('max_detail_chars', 1200)
    max_prompt_chars = llm_config.get('max_prompt_chars', 350000)
    window_count = 0

    for idx, (w_ops, _start) in enumerate(
            sliding_windows(ops, window_size, window_stride), 1):
        window_count += 1
        block = format_ops_block(w_ops, max_detail_chars)
        prompt = PROMPT_TEMPLATE.format(operations_block=block)

        if max_prompt_chars > 0 and len(prompt) > max_prompt_chars:
            local_cap = max(80, max_detail_chars // 2)
            block = format_ops_block(w_ops, local_cap)
            prompt = PROMPT_TEMPLATE.format(operations_block=block)
            while len(prompt) > max_prompt_chars and len(w_ops) > 1:
                w_ops = w_ops[1:]
                block = format_ops_block(w_ops, local_cap)
                prompt = PROMPT_TEMPLATE.format(operations_block=block)

        resp = agg_call_llm(
            client, prompt, llm_config['model'],
            llm_config.get('min_request_interval', 1.2),
            rate_state, 3, 0.5, 4.0, True, True,
        ) if client else None

        if resp and 'error' in resp:
            print(f"  [aggregate] LLM error window {idx}: "
                  f"{resp.get('error_type')}: {resp.get('error')}")

        content = extract_message_content(resp)
        window_tasks = parse_content_to_tasks(content)
        tasks.extend(window_tasks)

    tasks.sort(key=lambda t: t.get('op_start_id', float('inf')))
    print(f"  [aggregate] {window_count} windows → {len(tasks)} tasks")
    return tasks


# ============================================================================
# Step 2: Refine refine_task_intervals.py 
# ============================================================================

def step_refine(
    tasks: List[Dict],
    ops: List[Dict],
    llm_config: Dict,
) -> List[Dict]:
    """"""
    client = None
    if llm_config.get('call_llm') and llm_config.get('api_key'):
        from openai import OpenAI
        client = OpenAI(api_key=llm_config['api_key'], base_url=llm_config['endpoint'])
    model = llm_config['model']

    token_stats = {
        'total_input_tokens': 0, 'total_output_tokens': 0,
        'total_tokens': 0, 'llm_calls': 0,
    }

    # 
    valid_tasks = [t for t in tasks
                   if 'op_start_id' in t and 'op_end_id' in t
                   and 'raw_text' not in t]
    if len(valid_tasks) < len(tasks):
        print(f"  [refine] filtered {len(tasks) - len(valid_tasks)} invalid tasks")
    tasks = valid_tasks
    initial_count = len(tasks)

    if not tasks:
        print("  [refine] no tasks to refine")
        return tasks

    tasks = execute_granularity_filtering_simplified(tasks, ops)
    tasks = merge_tasks_single_iteration(
        tasks, ops, client, model,
        max_time_gap=20, max_id_gap=10,
        min_confidence=0.7, token_stats=token_stats,
    )
    tasks = count_and_report_operations(tasks, ops)
    tasks = split_oversized_tasks(
        tasks, ops, client, model,
        threshold=50, token_stats=token_stats,
    )

    print(f"  [refine] {initial_count} → {len(tasks)} tasks, "
          f"LLM calls: {token_stats['llm_calls']}, "
          f"tokens: {token_stats['total_tokens']}")
    return tasks


# ============================================================================
# Step 3: Classify classify_proactive_tasks.py 
# ============================================================================

def step_classify(
    tasks: List[Dict],
    ops: List[Dict],
    llm_config: Dict,
    session_id: str,
    metadata: Dict,
) -> Dict:
    """
    LLM /
     analyze_task  edit_length 
     LLM 
    """
    ops_by_id = {op['id']: op for op in ops}
    all_op_ids = sorted(ops_by_id.keys())

    token_stats = {
        'llm_calls': 0, 'total_input_tokens': 0,
        'total_output_tokens': 0, 'total_tokens': 0,
    }
    positives = []
    negatives = []

    if llm_config.get('call_llm') and llm_config.get('api_key'):
        #  detail
        for task in tasks:
            if not task.get('detail'):
                _, task_ops = get_context_ops(task, ops_by_id, all_op_ids, 0)
                try:
                    task_text = format_operations_for_prompt(task_ops, compressed=True)
                    prompt = DETAIL_GENERATION_PROMPT.format(
                        task_summary=task.get('summary', ''),
                        op_start_id=task.get('op_start_id'),
                        op_end_id=task.get('op_end_id'),
                        task_operations=task_text,
                    )
                    detail = cls_call_llm(
                        prompt, llm_config['api_key'], llm_config['endpoint'],
                        llm_config['model'], token_stats=token_stats,
                    )
                    task['detail'] = detail.strip()
                except Exception as e:
                    print(f"  [classify] detail generation failed: {e}")
                time.sleep(0.5)

        # LLM  'step4'  POSITIVE_PROMPT_TEMPLATE
        for task in tasks:
            before_ops, task_ops = get_context_ops(
                task, ops_by_id, all_op_ids, CONTEXT_OPS_BEFORE)
            try:
                result, prompt_mode = classify_task_with_llm(
                    task, before_ops, task_ops, 'step4',
                    llm_config['api_key'], llm_config['endpoint'],
                    llm_config['model'], token_stats,
                )
                if prompt_mode == 'too_long':
                    continue

                decision = result.get('decision', 'skip')
                confidence = result.get('confidence', 0)
                reasoning = result.get('reasoning', '')

                entry = {
                    'summary': task.get('summary', ''),
                    'detail': task.get('detail', ''),
                    'op_start_id': task['op_start_id'],
                    'op_end_id': task['op_end_id'],
                    'confidence': confidence,
                    'reasoning': reasoning,
                    'trigger_after_op_id': before_ops[-1]['id'] if before_ops else None,
                }

                if decision == 'positive' and confidence >= POS_CONFIDENCE_THRESHOLD:
                    entry['suggested_prompt'] = result.get('suggested_prompt', '')
                    positives.append(entry)
                elif decision == 'negative' and confidence >= NEG_CONFIDENCE_THRESHOLD:
                    negatives.append(entry)

            except RuntimeError:
                raise
            except Exception as e:
                print(f"  [classify] error: {e}")
            time.sleep(0.5)

    print(f"  [classify] positive={len(positives)}, negative={len(negatives)}")

    return {
        'session_id': session_id,
        'metadata': metadata,
        'operations': ops,
        'positive': positives,
        'negative': negatives,
        'classification_info': {
            'total_tasks': len(tasks),
            'positive_count': len(positives),
            'negative_count': len(negatives),
            'token_usage': token_stats,
        },
    }


# ============================================================================
# 
# ============================================================================

def process_scene(
    filepath: str,
    output_dir: str,
    llm_config: Dict,
    window_size: int,
    window_stride: int,
) -> Optional[Dict]:
    """ scene  aggregate → refine → classify"""
    scene_id = Path(filepath).stem  # e.g. "scene_0"
    print(f"\n{'='*50}")
    print(f"Processing: {scene_id}")

    # events → operations
    records = load_scene(filepath)
    ops = events_to_operations(records)
    if not ops:
        print("  No events found, skipping")
        return None
    print(f"  Events (as operations): {len(ops)}")

    metadata = {'scene_id': scene_id, 'source_file': filepath}

    # Step 1: Aggregate
    tasks = step_aggregate(ops, llm_config, window_size, window_stride)

    # Step 2: Refine
    tasks = step_refine(tasks, ops, llm_config)

    # Step 3: Classify
    result = step_classify(tasks, ops, llm_config, session_id=scene_id, metadata=metadata)

    #  classify_output 
    out_path = Path(output_dir) / f"{scene_id}_classified.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  Output: {out_path}")

    return result


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Synthetic data pipeline (aligned with run_preprocessing_pipeline.py)')
    parser.add_argument('--scene-dir', default='synthetic_data/scenarios/agent_data',
                        help='Directory containing scene_*.jsonl files')
    parser.add_argument('--output-dir', default='synthetic_data/scenarios/synthetic_classified',
                        help='Output directory')
    parser.add_argument('--window-size', type=int, default=DEFAULT_WINDOW_SIZE)
    parser.add_argument('--window-stride', type=int, default=DEFAULT_WINDOW_STRIDE)
    parser.add_argument('--single-scene', type=str, default=None,
                        help='Process only a specific scene (by name or index, e.g. "test_session_e528bfd9" or "0")')
    parser.add_argument('--resume', action='store_true',
                        help='Skip scenes whose classified output already exists')
    parser.add_argument('--call-llm', action='store_true',
                        help='Enable LLM API calls')
    parser.add_argument('--api-key', default=DEFAULT_API_KEY)
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--max-detail-chars', type=int, default=1200)
    parser.add_argument('--max-prompt-chars', type=int, default=350000)
    parser.add_argument('--min-request-interval', type=float, default=1.2)
    parser.add_argument('--workers', type=int, default=16,
                        help='Number of parallel workers')
    args = parser.parse_args()

    #  scene 
    if args.single_scene is not None:
        # Try direct name first, then scene_N format
        candidate = os.path.join(args.scene_dir, f'{args.single_scene}.jsonl')
        if not os.path.exists(candidate):
            candidate = os.path.join(args.scene_dir, f'scene_{args.single_scene}.jsonl')
        scene_files = [candidate]
        if not os.path.exists(scene_files[0]):
            print(f"Scene file not found: {candidate}")
            sys.exit(1)
    else:
        scene_files = sorted(glob.glob(os.path.join(args.scene_dir, '*.jsonl')))

    if not scene_files:
        print(f"No scene files found in {args.scene_dir}")
        sys.exit(1)

    print(f"Found {len(scene_files)} scene file(s)")
    print(f"Window: size={args.window_size}, stride={args.window_stride}")
    print(f"Output: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    llm_config = {
        'call_llm': args.call_llm,
        'api_key': args.api_key,
        'endpoint': args.endpoint,
        'model': args.model,
        'max_detail_chars': args.max_detail_chars,
        'max_prompt_chars': args.max_prompt_chars,
        'min_request_interval': args.min_request_interval,
    }

    total_positive = 0
    total_negative = 0
    total_tasks = 0
    skipped = 0

    # 
    todo_files = []
    for filepath in scene_files:
        scene_id = Path(filepath).stem
        out_path = Path(args.output_dir) / f"{scene_id}_classified.json"
        if args.resume and out_path.exists():
            skipped += 1
            continue
        todo_files.append(filepath)

    print(f"To process: {len(todo_files)}, skipped by resume: {skipped}")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tqdm import tqdm

    def _run(filepath):
        return process_scene(
            filepath, args.output_dir, llm_config,
            args.window_size, args.window_stride,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run, f): f for f in todo_files}
        with tqdm(total=len(todo_files), unit='scene') as pbar:
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        info = result['classification_info']
                        total_positive += info['positive_count']
                        total_negative += info['negative_count']
                        total_tasks += info['total_tasks']
                except Exception as e:
                    filepath = futures[future]
                    print(f"\n  [ERROR] {Path(filepath).name}: {e}")
                pbar.set_postfix(pos=total_positive, neg=total_negative)
                pbar.update(1)

    print(f"\n{'='*50}")
    print(f"Done. (skipped {skipped} by resume)")
    print(f"  Tasks:    {total_tasks}")
    print(f"  Positive: {total_positive}")
    print(f"  Negative: {total_negative}")
    print(f"  Output:   {args.output_dir}")


if __name__ == '__main__':
    main()
