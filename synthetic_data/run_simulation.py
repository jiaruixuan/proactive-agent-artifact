#!/usr/bin/env python3
"""
Run simulator scenes and write JSONL event traces.

Usage:
  # Run all scene YAML files in a directory.
  python3 synthetic_data/run_simulation.py \
      --scene-dir synthetic_data/scenarios/from_sessions \
      --workers 8

  # Run one scene YAML file.
  python3 synthetic_data/run_simulation.py \
      --single synthetic_data/scenarios/from_sessions/2026-01-27_session_abc.yaml
"""

import argparse
import glob
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def run_one(yaml_path: str, resume: bool, max_retries: int = 3, verbose: bool = False) -> str:
    """Run one scene and return 'ok', 'skip', or 'error'."""
    out_path = yaml_path.replace('.yaml', '.jsonl')
    if resume and os.path.exists(out_path):
        return 'skip'

    synthetic_data_dir = str(Path(__file__).parent)
    abs_yaml = str(Path(yaml_path).resolve())
    abs_out = str(Path(out_path).resolve())

    for attempt in range(1, max_retries + 1):
        capture = not verbose
        result = subprocess.run(
            [sys.executable, '-m', 'simulator.main', abs_yaml, '--out_file', abs_out],
            capture_output=capture, text=True, cwd=synthetic_data_dir,
            env={**os.environ, 'SETUP_PROACTIVE_AGENT': 'False'},
        )
        if result.returncode == 0:
            return 'ok'
        is_rate_limit = '429' in (result.stderr or '')
        if attempt < max_retries:
            wait = 30 if is_rate_limit else 3
            print(f'  [RETRY {attempt}/{max_retries}] {Path(yaml_path).name}'
                  f'{" (rate limit, wait 30s)" if is_rate_limit else ""}', file=sys.stderr)
            time.sleep(wait)
        else:
            print(f'  [ERROR] {Path(yaml_path).name}:\n{(result.stderr or "")[-200:]}', file=sys.stderr)

    return 'error'


def main():
    parser = argparse.ArgumentParser(description='Batch simulator run')
    parser.add_argument('--scene-dir', type=str,
                        default='synthetic_data/scenarios/from_sessions',
                        help='Directory containing scene *.yaml files')
    parser.add_argument('--single', type=str, default=None,
                        help='Run a single yaml file')
    parser.add_argument('--workers', type=int, default=8,
                        help='Number of parallel workers (default: 8)')
    parser.add_argument('--resume', action='store_true', default=True,
                        help='Skip scenes whose .jsonl already exists (default: True)')
    parser.add_argument('--no-resume', dest='resume', action='store_false',
                        help='Re-run even if .jsonl already exists')
    args = parser.parse_args()

    # 
    if args.single:
        status = run_one(args.single, resume=False, verbose=True)
        print(f'Result: {status}')
        sys.exit(0 if status != 'error' else 1)

    # 
    yaml_files = sorted(glob.glob(os.path.join(args.scene_dir, '*.yaml')))
    if not yaml_files:
        print(f'No yaml files found in {args.scene_dir}')
        sys.exit(1)

    print(f'Found {len(yaml_files)} scene yaml files')
    print(f'Workers: {args.workers}, resume: {args.resume}')

    counts = {'ok': 0, 'skip': 0, 'error': 0}
    workers = min(args.workers, len(yaml_files))

    if HAS_TQDM:
        pbar = tqdm(total=len(yaml_files), unit='scene')
    else:
        pbar = None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_one, f, args.resume): f for f in yaml_files}
        for future in as_completed(futures):
            try:
                status = future.result()
            except Exception as e:
                print(f'  [ERROR] {futures[future]}: {e}', file=sys.stderr)
                status = 'error'
            counts[status] += 1
            if pbar:
                pbar.set_postfix(**counts)
                pbar.update(1)

    if pbar:
        pbar.close()

    print(f'\nDone. ok={counts["ok"]}, skip={counts["skip"]}, error={counts["error"]}')
    print(f'Output: {args.scene_dir}')


if __name__ == '__main__':
    main()
