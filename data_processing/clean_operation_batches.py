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

# 
AGGREGATED_DIR = Path("aggregate_results")
CLEANED_DIR = Path("cleaned_data")
OUTPUT_DIR = Path("refine_results")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
MODEL = "deepseek-v3.2"

# - 
TIMEOUT = 600  # 10

def main():
    print("=" * 80)
    print("Batch Processing All Sessions")
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

    print(f"Found {total} sessions to process")
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
    for i, agg_file in enumerate(tqdm(aggregated_files, desc="Processing sessions", unit="session"), 1):
        session_id = agg_file.stem.replace('_aggregated', '')

        tqdm.write(f"[{i}/{total}] {session_id}...", end=' ')

        try:
            # 
            result = subprocess.run([
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
            if result.stdout:
                for line in result.stdout.split('\n'):
                    # DEBUG
                    if any(marker in line for marker in ['DEBUG:', '🔨', '📋', '✅', '⚠']):
                        tqdm.write(f"    {line}")

            # 
            # 
            try:
                real_agg_file = agg_file.resolve()
                relative_path = real_agg_file.relative_to(aggregated_dir)
                output_file = output_dir / relative_path.parent / f"{session_id}_refined.json"
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

                # 
                stats['success'] += 1
                stats['total_input_tokens'] += token_usage.get('total_input_tokens', 0)
                stats['total_output_tokens'] += token_usage.get('total_output_tokens', 0)
                stats['total_tokens'] += token_usage.get('total_tokens', 0)
                stats['total_llm_calls'] += token_usage.get('llm_calls', 0)

                session_info = {
                    'session_id': session_id,
                    'status': 'success',
                    'initial_count': proc_info.get('initial_count', 0),
                    'final_count': proc_info.get('final_count', 0),
                    'reduction_pct': proc_info.get('reduction_pct', 0),
                    'tokens': token_usage.get('total_tokens', 0),
                    'max_ops': max_ops
                }
                if skipped_long:
                    session_info['skipped_long_tasks'] = skipped_long

                stats['sessions'].append(session_info)

                tqdm.write(f"✓ {proc_info.get('initial_count', 0)}→{proc_info.get('final_count', 0)} tasks, max_ops: {max_ops}, {token_usage.get('total_tokens', 0)} tokens")
            else:
                # 
                error_msg = "No output file"
                if result.returncode != 0:
                    error_msg = f"Process failed (code {result.returncode})"
                    if result.stderr:
                        # stderr10
                        stderr_lines = result.stderr.strip().split('\n')
                        error_detail = '\n'.join(stderr_lines[-10:])
                        tqdm.write(f"⊘ Skipped - {error_msg}")
                        tqdm.write(f"  Error:\n{error_detail}")
                    else:
                        tqdm.write(f"⊘ Skipped - {error_msg}")
                else:
                    tqdm.write(f"⊘ Skipped - {error_msg}")

                stats['skipped'] += 1
                stats['sessions'].append({
                    'session_id': session_id,
                    'status': 'skipped',
                    'reason': error_msg
                })

        except subprocess.TimeoutExpired:
            stats['failed'] += 1
            stats['sessions'].append({
                'session_id': session_id,
                'status': 'failed',
                'reason': f'Timeout (>{TIMEOUT}s)'
            })
            tqdm.write(f"✗ Timeout (>{TIMEOUT}s)")
        except Exception as e:
            stats['failed'] += 1
            stats['sessions'].append({
                'session_id': session_id,
                'status': 'failed',
                'reason': str(e)
            })
            tqdm.write(f"✗ Error: {e}")

    # 
    print()
    print("=" * 80)
    print("Batch Processing Complete")
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
    stats_file = output_dir / f"batch_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
