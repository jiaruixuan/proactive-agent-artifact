"""Data loading, repo path mapping, and sample filtering utilities."""

import json
import re
from pathlib import Path
from typing import List, Optional


def ops_folder_to_repo_folder(ops_folder_name: str) -> str:
    """Convert operations folder name to data repo folder name.

    Two cases:
    1. Windows paths (drive letter + ___): d___xxx -> d____xxx (add one _)
    2. Linux/Mac paths (no drive prefix): home__xxx -> __home__xxx (prepend __)
    """
    if re.match(r"^[a-zA-Z]___", ops_folder_name):
        return re.sub(r"___", "____", ops_folder_name, count=1)
    return "__" + ops_folder_name


def repo_has_files(repo_dir: Path) -> bool:
    """Check if a repo directory contains at least one actual file."""
    if not repo_dir.exists():
        return False
    try:
        for p in repo_dir.rglob("*"):
            if p.is_file():
                return True
    except Exception:
        pass
    return False


def resolve_repo_path(classified_path: Path, classify_dir: Path, data_dir: Path) -> Optional[Path]:
    """Given a classified JSON path, resolve the corresponding repo path in data/.

    Returns the repo path if it exists and has files, otherwise None.
    """
    rel = classified_path.relative_to(classify_dir)
    parts = rel.parts
    if len(parts) < 4:
        return None

    user_hash = parts[0]
    project_folder = parts[2]  # under operations/

    user_data_dir = data_dir / user_hash
    if not user_data_dir.exists():
        return None

    repo_folder = ops_folder_to_repo_folder(project_folder)
    repo_path = user_data_dir / repo_folder

    if not repo_path.exists() or not repo_has_files(repo_path):
        return None

    return repo_path


def load_split_session_ids(split_file: str, split: str = "test") -> Optional[set]:
    """Load allowed session short IDs from a dataset split JSON file.

    Returns a set of 8-char session IDs (e.g. {"e4c88b12", ...}), or None if no file given.
    """
    if not split_file:
        return None
    data = json.loads(Path(split_file).read_text(encoding="utf-8"))
    entries = data.get(split, {}).get("data", [])
    # session_id format in split file: "session_e4c88b12" → extract "e4c88b12"
    return {e["session_id"].replace("session_", "") for e in entries}


def collect_valid_samples(
    classify_dir: Path,
    data_dir: Path,
    window_size: int = 50,
    allowed_session_ids: Optional[set] = None,
) -> List[dict]:
    """Collect all positive task samples that have a valid (non-empty) repo.

    Each sample dict contains:
      - session_id, trigger_op_id, trigger_op_idx
      - ground_truth: {task, workflow}
      - ops: full operation list
      - repo_path: Path to the repo on disk
      - classified_path: Path to the classified JSON file
    """
    samples = []
    for f in sorted(classify_dir.rglob("*_classified.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        positives = data.get("positive", [])
        if not positives:
            continue

        # Filter by split if specified
        if allowed_session_ids is not None:
            full_uuid = data.get("session_id", "")
            short_id = full_uuid.replace("session_", "").split("-")[0]
            if short_id not in allowed_session_ids:
                continue

        ops = data.get("operations", [])
        op_index = {op["id"]: i for i, op in enumerate(ops) if "id" in op}

        # Resolve repo path for this session
        repo_path = resolve_repo_path(f, classify_dir, data_dir)
        if repo_path is None:
            continue

        for pos in positives:
            tid = pos.get("trigger_after_op_id")
            if tid is None or tid not in op_index:
                continue
            # Skip noise samples produced by even-split data pipeline
            if pos.get("summary", "").lower().startswith("even split"):
                continue
            samples.append({
                "session_id": data["session_id"],
                "trigger_op_id": tid,
                "trigger_op_idx": op_index[tid],
                "ground_truth": {
                    "task": pos.get("summary", ""),
                    "workflow": pos.get("detail", ""),
                },
                "ops": ops,
                "repo_path": str(repo_path),
                "classified_path": str(f),
            })

    return samples


def op_to_observation(op: dict) -> dict:
    """Convert a single operation to an observation dict (same format as run_chatbot_eval.py)."""
    ts = op.get("timestamp", 0)
    try:
        ts = float(ts) / 1000
    except (TypeError, ValueError):
        ts = 0
    return {
        "time": str(ts),
        "event": f"[{op.get('operationType', 'unknown')}]\n{op.get('readableDetails', '')}",
    }
