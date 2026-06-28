#!/usr/bin/env python3
"""
Find evaluation samples that have both positive labels and repository snapshots.

The script scans `classify_output/` for classified sessions with positive tasks,
maps each operation workspace to the corresponding repository folder under
`data/`, and reports which sessions are ready for agent evaluation.

Usage:
  python3 find_valid_sessions.py
  python3 find_valid_sessions.py --classify-dir /path/to/classify_output --data-dir /path/to/data
  python3 find_valid_sessions.py --verbose
"""

import json
import os
import re
from pathlib import Path

import fire


def ops_folder_to_repo_folder(ops_folder_name: str) -> str:
    """Map an operations workspace folder name to a repository folder name."""
    if re.match(r"^[a-zA-Z]___", ops_folder_name):
        return re.sub(r"___", "____", ops_folder_name, count=1)
    return "__" + ops_folder_name


def count_files(path: Path) -> int:
    """Count files under a directory."""
    count = 0
    try:
        for _root, _dirs, files in os.walk(path):
            count += len(files)
    except Exception:
        pass
    return count


def _auto_detect_dir(name: str, script_dir: Path) -> str | None:
    for candidate in [
        script_dir.parent / name,
        script_dir.parent.parent / name,
        Path(name),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


def main(
    classify_dir: str = None,
    data_dir: str = None,
    verbose: bool = False,
):
    script_dir = Path(__file__).parent
    classify_dir = classify_dir or _auto_detect_dir("classify_output", script_dir)
    data_dir = data_dir or _auto_detect_dir("data", script_dir)

    if not classify_dir or not Path(classify_dir).exists():
        print("Error: classify_output directory not found. Pass --classify-dir.")
        return
    if not data_dir or not Path(data_dir).exists():
        print("Error: data directory not found. Pass --data-dir.")
        return

    classify_path = Path(classify_dir)
    data_path = Path(data_dir)
    print(f"classify_dir: {classify_path}")
    print(f"data_dir:     {data_path}")
    print()

    valid_samples = []
    empty_repo_samples = []
    missing_repo_samples = []
    missing_hash_samples = []

    for f in sorted(classify_path.rglob("*_classified.json")):
        try:
            session_data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        positives = session_data.get("positive", [])
        valid_pos = [p for p in positives if p.get("trigger_after_op_id") is not None]
        if not valid_pos:
            continue

        rel = f.relative_to(classify_path)
        parts = rel.parts
        if len(parts) < 4:
            continue

        user_hash = parts[0]
        project_folder = parts[2]
        repo_folder = ops_folder_to_repo_folder(project_folder)

        sample_info = {
            "hash": user_hash,
            "project": project_folder,
            "repo_folder": repo_folder,
            "file": str(rel),
            "session_id": session_data.get("session_id", ""),
            "tasks": [
                (p["trigger_after_op_id"], p.get("summary", "")[:80])
                for p in valid_pos
            ],
            "task_count": len(valid_pos),
        }

        user_data_dir = data_path / user_hash
        if not user_data_dir.exists():
            missing_hash_samples.append(sample_info)
            continue

        repo_path = user_data_dir / repo_folder
        if not repo_path.exists():
            sample_info["actual_dirs"] = [
                d.name for d in user_data_dir.iterdir()
                if d.is_dir() and d.name != "operations"
            ]
            missing_repo_samples.append(sample_info)
            continue

        n_files = count_files(repo_path)
        sample_info["file_count"] = n_files
        sample_info["repo_path"] = str(repo_path)

        if n_files == 0:
            empty_repo_samples.append(sample_info)
        else:
            valid_samples.append(sample_info)

    total_valid_tasks = sum(s["task_count"] for s in valid_samples)
    total_empty_tasks = sum(s["task_count"] for s in empty_repo_samples)
    total_missing_tasks = sum(s["task_count"] for s in missing_repo_samples)
    total_nohash_tasks = sum(s["task_count"] for s in missing_hash_samples)

    print("=" * 60)
    print("Scan Results")
    print("=" * 60)
    print(f"Valid repositories:        {len(valid_samples)} sessions, {total_valid_tasks} tasks")
    print(f"Empty repositories:        {len(empty_repo_samples)} sessions, {total_empty_tasks} tasks")
    print(f"Missing repositories:      {len(missing_repo_samples)} sessions, {total_missing_tasks} tasks")
    print(f"Missing user data folders: {len(missing_hash_samples)} sessions, {total_nohash_tasks} tasks")
    print("=" * 60)

    if valid_samples and verbose:
        print("\nValid Samples")
        print("-" * 60)
        for sample in valid_samples:
            print(f"\nHash:     {sample['hash']}")
            print(f"Project:  {sample['project']}")
            print(f"Repo:     {sample['repo_path']} ({sample['file_count']} files)")
            print(f"Session:  {sample['session_id']}")
            print(f"Tasks ({sample['task_count']}):")
            for tid, summary in sample["tasks"]:
                print(f"  - [op {tid}] {summary}")

    if empty_repo_samples and verbose:
        print("\nEmpty Repositories")
        print("-" * 60)
        for sample in empty_repo_samples:
            repo_path = data_path / sample["hash"] / sample["repo_folder"]
            print(f"\nHash:     {sample['hash']}")
            print(f"Project:  {sample['project']}")
            print(f"Repo:     {repo_path}")
            print(f"Session:  {sample['session_id']}")
            print(f"Tasks ({sample['task_count']}):")
            for tid, summary in sample["tasks"]:
                print(f"  - [op {tid}] {summary}")

    if missing_repo_samples and verbose:
        print("\nMissing Repositories")
        print("-" * 60)
        for sample in missing_repo_samples:
            print(f"\nHash:     {sample['hash']}")
            print(f"Expected: data/{sample['hash']}/{sample['repo_folder']}/")
            print(f"Actual:   {sample.get('actual_dirs', [])}")

    print("\nNext Step")
    print("=" * 60)
    if valid_samples:
        print(f"{total_valid_tasks} tasks are ready. Example:")
        print("  Add an agent harness that consumes these repository-backed samples.")
    elif empty_repo_samples:
        sample = empty_repo_samples[0]
        repo_path = data_path / sample["hash"] / sample["repo_folder"]
        print("At least one repository folder exists but has no files.")
        print(f"Populate this folder before repository-aware evaluation: {repo_path}/")
    else:
        unique_hashes = {s["hash"] for s in missing_hash_samples}
        print(f"Missing data folders for {len(unique_hashes)} users.")
        if missing_hash_samples:
            sample = missing_hash_samples[0]
            print("Create a repository snapshot directory such as:")
            print(f"  mkdir -p {data_path}/{sample['hash']}/{sample['repo_folder']}/")


if __name__ == "__main__":
    fire.Fire(main)
