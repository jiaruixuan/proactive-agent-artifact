#!/usr/bin/env python3
"""
sessions

"""

import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
import sys
from tqdm import tqdm
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

# 
AGGREGATED_DIR = Path("aggregate_results")
CLEANED_DIR = Path("cleaned_data")
OUTPUT_DIR = Path("refine_results")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
MODEL = "deepseek-v3.2"

# 
TIMEOUT = 1800  # 30

# 
MAX_WORKERS = 8  # CPUAPI


def process_single_session(args):
    """session"""
    agg_file, aggregated_dir, cleaned_dir, output_dir, session_id = args

    result = {
        'session_id': session_id,
        'status': 'unknown',
        'reason': None
    }

    try:
        # 
        proc_result = subprocess.run([
            'python3', 'data_processing/refine_task_intervals.py',
            '--aggregated-dir', str(aggregated_dir),
            '--original-ops-dir', str(cleaned_dir),
            '--output-dir', str(output_dir),
            '--single-file', str(agg_file),
            '--api-key', API_KEY,
            '--endpoint', ENDPOINT,
            '--model', MODEL,
            '--call-llm'
        ], capture_output=True, text=True, timeout=TIMEOUT)

        # 
        session_path = ""
        try:
            real_agg_file = agg_file.resolve()
            relative_path = real_agg_file.relative_to(aggregated_dir)
            output_file = output_dir / relative_path.parent / f"{session_id}_refined.json"
            session_path = str(relative_path.parent)  # 
        except ValueError:
            output_file = output_dir / f"{session_id}_refined.json"

        if output_file.exists():
            # 
            with open(output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            proc_info = data.get('processing_info', {})
            token_usage = proc_info.get('token_usage', {})

            # taskoperation
            final_tasks = proc_info.get('final_tasks', [])
            max_ops = max([t.get('operation_count', 0) for t in final_tasks], default=0)

            # 
            skipped_long = proc_info.get('skipped_long_tasks', [])

            result['status'] = 'success'
            result['session_path'] = session_path  # 
            result['initial_count'] = proc_info.get('initial_count', 0)
            result['final_count'] = proc_info.get('final_count', 0)
            result['reduction_pct'] = proc_info.get('reduction_pct', 0)
            result['tokens'] = token_usage.get('total_tokens', 0)
            result['max_ops'] = max_ops
            result['input_tokens'] = token_usage.get('total_input_tokens', 0)
            result['output_tokens'] = token_usage.get('total_output_tokens', 0)
            result['llm_calls'] = token_usage.get('llm_calls', 0)

            if skipped_long:
                result['skipped_long_tasks'] = skipped_long

            # stderr
            if proc_result.stderr:
                # 
                error_lines = [line for line in proc_result.stderr.split('\n')
                              if any(marker in line for marker in ['⚠', 'ERROR', 'Error', 'WARNING'])]
                if error_lines:
                    result['warnings'] = error_lines[:5]  # 5
        else:
            # 
            error_msg = "No output file"
            if proc_result.returncode != 0:
                error_msg = f"Process failed (code {proc_result.returncode})"

            result['status'] = 'skipped'
            result['reason'] = error_msg

            # stderr
            if proc_result.stderr:
                stderr_lines = proc_result.stderr.strip().split('\n')
                result['stderr'] = stderr_lines[-10:]  # 10

    except subprocess.TimeoutExpired:
        result['status'] = 'failed'
        result['reason'] = f'Timeout (>{TIMEOUT}s)'
    except Exception as e:
        result['status'] = 'failed'
        result['reason'] = str(e)

    return result


def main():
    print("=" * 80)
    print("Parallel Batch Processing")
    print("=" * 80)
    print()

    # 
    aggregated_dir = AGGREGATED_DIR.resolve()
    cleaned_dir = CLEANED_DIR.resolve()
    output_dir = OUTPUT_DIR

    # aggregated files
    print("Finding all aggregated files...")
    aggregated_files = list(aggregated_dir.rglob("*_aggregated.json"))
    total = len(aggregated_files)

    print(f"Found {total} sessions total")

    # worker
    cpu_count = multiprocessing.cpu_count()

    #  session
    tasks = []
    skipped_existing = 0
    for agg_file in aggregated_files:
        session_id = agg_file.stem.replace('_aggregated', '')
        try:
            relative_path = agg_file.resolve().relative_to(aggregated_dir)
            out_file = output_dir / relative_path.parent / f"{session_id}_refined.json"
        except ValueError:
            out_file = output_dir / f"{session_id}_refined.json"
        if out_file.exists():
            skipped_existing += 1
            continue
        tasks.append((agg_file, aggregated_dir, cleaned_dir, output_dir, session_id))

    print(f"Skipped {skipped_existing} sessions with existing output, {len(tasks)} to process")

    if len(tasks) == 0:
        print("All sessions already processed, nothing to do.")
        return

    total = len(tasks)
    actual_workers = min(MAX_WORKERS, cpu_count, total)
    print(f"Using {actual_workers} parallel workers (CPU cores: {cpu_count})")
    print()

    # 
    stats = {
        'total': total,
        'success': 0,
        'failed': 0,
        'skipped': 0,
        'total_input_tokens': 0,
        'total_output_tokens': 0,
        'total_tokens': 0,
        'total_llm_calls': 0,
        'sessions': []
    }

    # 
    with ProcessPoolExecutor(max_workers=actual_workers) as executor:
        # 
        future_to_session = {executor.submit(process_single_session, task): task[4] for task in tasks}

        # tqdm
        with tqdm(total=total, desc="Processing sessions", unit="session") as pbar:
            for future in as_completed(future_to_session):
                session_id = future_to_session[future]

                try:
                    result = future.result()

                    if result['status'] == 'success':
                        stats['success'] += 1
                        stats['total_input_tokens'] += result.get('input_tokens', 0)
                        stats['total_output_tokens'] += result.get('output_tokens', 0)
                        stats['total_tokens'] += result.get('tokens', 0)
                        stats['total_llm_calls'] += result.get('llm_calls', 0)

                        session_info = {
                            'session_id': result['session_id'],
                            'session_path': result.get('session_path', ''),  # 
                            'status': 'success',
                            'initial_count': result.get('initial_count', 0),
                            'final_count': result.get('final_count', 0),
                            'reduction_pct': result.get('reduction_pct', 0),
                            'tokens': result.get('tokens', 0),
                            'max_ops': result.get('max_ops', 0)
                        }

                        if 'skipped_long_tasks' in result:
                            session_info['skipped_long_tasks'] = result['skipped_long_tasks']

                        stats['sessions'].append(session_info)

                        output_msg = f"✓ {session_id}: {result['initial_count']}→{result['final_count']} tasks, max_ops: {result['max_ops']}, {result['tokens']} tokens"

                        # 
                        if 'warnings' in result and result['warnings']:
                            output_msg += f"\n  ⚠ Warnings:"
                            for warning in result['warnings']:
                                output_msg += f"\n    {warning}"

                        tqdm.write(output_msg)

                    elif result['status'] == 'skipped':
                        stats['skipped'] += 1
                        stats['sessions'].append({
                            'session_id': result['session_id'],
                            'status': 'skipped',
                            'reason': result.get('reason', 'Unknown')
                        })

                        output_msg = f"⊘ {session_id}: {result.get('reason', 'Skipped')}"

                        # stderr
                        if 'stderr' in result and result['stderr']:
                            output_msg += "\n  Error details:"
                            for line in result['stderr'][:3]:  # 3
                                output_msg += f"\n    {line}"

                        tqdm.write(output_msg)

                    else:  # failed
                        stats['failed'] += 1
                        stats['sessions'].append({
                            'session_id': result['session_id'],
                            'status': 'failed',
                            'reason': result.get('reason', 'Unknown')
                        })
                        tqdm.write(f"✗ {session_id}: {result.get('reason', 'Failed')}")

                except Exception as e:
                    stats['failed'] += 1
                    stats['sessions'].append({
                        'session_id': session_id,
                        'status': 'failed',
                        'reason': str(e)
                    })
                    tqdm.write(f"✗ {session_id}: {e}")

                pbar.update(1)

    # 
    print()
    print("=" * 80)
    print("Parallel Batch Processing Complete")
    print("=" * 80)
    print(f"Total sessions: {stats['total']}")
    print(f"Successful: {stats['success']}")
    print(f"Failed: {stats['failed']}")
    print(f"Skipped: {stats['skipped']}")
    print()
    print("Token Usage:")
    print(f"  Total LLM calls: {stats['total_llm_calls']}")
    print(f"  Input tokens: {stats['total_input_tokens']:,}")
    print(f"  Output tokens: {stats['total_output_tokens']:,}")
    print(f"  Total tokens: {stats['total_tokens']:,}")
    print("=" * 80)

    # 
    stats_file = output_dir / f"batch_stats_parallel_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print()
    print(f"Statistics saved to: {stats_file}")

    # session
    timeout_sessions = [s for s in stats['sessions'] if 'Timeout' in s.get('reason', '')]
    if timeout_sessions:
        timeout_file = output_dir / f"timeout_sessions_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(timeout_file, 'w', encoding='utf-8') as f:
            f.write(f"# Timeout sessions (>{TIMEOUT}s)\n")
            f.write(f"# Total: {len(timeout_sessions)}\n")
            f.write(f"# You can reprocess these individually with longer timeout\n\n")
            for s in timeout_sessions:
                f.write(f"{s['session_id']}\n")
        print(f"Timeout sessions list saved to: {timeout_file}")
        print(f"  ({len(timeout_sessions)} sessions timed out - consider increasing TIMEOUT or processing individually)")

    # 
    sessions_with_long_tasks = [s for s in stats['sessions'] if s.get('skipped_long_tasks')]
    if sessions_with_long_tasks:
        long_tasks_file = output_dir / f"skipped_long_tasks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        summary = {
            'total_sessions_affected': len(sessions_with_long_tasks),
            'total_tasks_skipped': sum(len(s['skipped_long_tasks']) for s in sessions_with_long_tasks),
            'details': []
        }

        for s in sessions_with_long_tasks:
            for task in s['skipped_long_tasks']:
                summary['details'].append({
                    'session_id': s['session_id'],
                    'task_index': task['task_index'],
                    'op_range': task['op_range'],
                    'operation_count': task['operation_count'],
                    'skip_reason': task['skip_reason']
                })

        with open(long_tasks_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"Skipped long tasks list saved to: {long_tasks_file}")
        print(f"  ({summary['total_tasks_skipped']} tasks in {len(sessions_with_long_tasks)} sessions skipped - need manual processing)")

if __name__ == '__main__':
    main()
