"""Utilities for loading the public release evaluation set."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .data_utils import op_to_observation, resolve_repo_path


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _resolve_release_file(release_dir: Path, *parts: str) -> Path:
    path = release_dir.joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"Release data file not found: {path}")
    return path


def load_release_samples(release_dir: str | Path, split: str = "test") -> list[dict]:
    """Load release samples by merging ground truth, IDE traces, and repo paths.

    Supports two layouts:
    - compact layout with ``test.jsonl``, ``samples/test.jsonl``, or the
      public ``test_nonsensitive_10.jsonl`` sample set
    - split layout with ``ground_truth/``, ``ide_traces/``, and ``metadata/``

    Returned samples are compatible with chatbot and agent-style eval scripts.
    ``ops_window`` is normalized to ``{time, event}`` observations for prompts,
    while ``ops`` preserves raw operations when they are available.
    """
    release_path = Path(release_dir)

    compact_candidates = [
        release_path / f"{split}.jsonl",
        release_path / "data" / f"{split}.jsonl",
        release_path / "samples" / f"{split}.jsonl",
    ]
    if split == "test":
        compact_candidates.append(release_path / "test_nonsensitive_10.jsonl")
    for compact_path in compact_candidates:
        if compact_path.exists():
            return [_normalize_compact_row(row, release_path) for row in _read_jsonl(compact_path)]

    gt_path = _first_existing(
        release_path,
        ("ground_truth", f"{split}.jsonl"),
    )
    trace_path = _first_existing(
        release_path,
        ("ide_traces", f"{split}.jsonl"),
        ("ide_trace", f"{split}.jsonl"),
    )
    index_path = _first_existing(
        release_path,
        ("metadata", "samples.jsonl"),
    )

    gt_by_id = {row["sample_id"]: row for row in _read_jsonl(gt_path)}
    trace_by_id = {row["sample_id"]: row for row in _read_jsonl(trace_path)}
    index_by_id = {row["sample_id"]: row for row in _read_jsonl(index_path)}

    sample_ids = sorted(set(gt_by_id) & set(trace_by_id) & set(index_by_id))
    samples = []
    for sample_id in sample_ids:
        gt = gt_by_id[sample_id]
        trace = trace_by_id[sample_id]
        index = index_by_id[sample_id]
        raw_window, normalized_obs = _normalize_trace_window(
            trace.get("raw_window_ops") or trace.get("ops") or [],
            trace.get("window_ops") or [],
        )
        positive = gt.get("positive", {})
        task = gt.get("task", positive.get("summary", ""))
        workflow = gt.get("workflow", positive.get("detail", ""))
        repo_path = _resolve_repo_from_release_index(index, release_path)
        samples.append({
            "sample_id": sample_id,
            "session_id": gt["session_id"],
            "trigger_op_id": gt["trigger_op_id"],
            "trigger_op_idx": len(raw_window or normalized_obs) - 1,
            "ground_truth": {
                "task": task,
                "workflow": workflow,
            },
            "ops": raw_window,
            "ops_window": normalized_obs,
            "repo_path": str(repo_path) if repo_path else "",
            "classified_relpath": index.get("classified_relpath", ""),
            "context_repo_relpath": index.get("context_repo_relpath", ""),
        })
    return samples


def group_samples_by_session(samples: Iterable[dict]) -> list[list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        grouped[sample["session_id"]].append(sample)
    return list(grouped.values())


def _first_existing(release_path: Path, *candidates: tuple[str, ...]) -> Path:
    for parts in candidates:
        path = release_path.joinpath(*parts)
        if path.exists():
            return path
    expected = ", ".join(str(release_path.joinpath(*parts)) for parts in candidates)
    raise FileNotFoundError(f"Release data file not found. Expected one of: {expected}")


def _normalize_compact_row(row: dict, release_path: Path) -> dict:
    raw_window = row.get("raw_window_ops") or row.get("ops") or []
    raw_window, normalized_obs = _normalize_trace_window(raw_window, row.get("window_ops") or [])

    repo_path = row.get("repo_path") or row.get("context_repo_path") or ""
    if not repo_path and row.get("context_repo_relpath"):
        repo_path = str(release_path / row["context_repo_relpath"])
    elif repo_path and not Path(repo_path).is_absolute():
        repo_path = str(release_path / repo_path)

    return {
        "sample_id": row.get("sample_id", f"{row['session_id']}_op{row['trigger_op_id']}"),
        "session_id": row["session_id"],
        "trigger_op_id": row["trigger_op_id"],
        "trigger_op_idx": len(raw_window or normalized_obs) - 1,
        "ground_truth": {
            "task": row.get("task", row.get("ground_truth", {}).get("task", "")),
            "workflow": row.get("workflow", row.get("ground_truth", {}).get("workflow", "")),
        },
        "ops": raw_window,
        "ops_window": normalized_obs,
        "repo_path": repo_path,
        "context_repo_relpath": row.get("context_repo_relpath", ""),
    }


def _normalize_trace_window(raw_window: list, window_ops: list) -> tuple[list, list]:
    """Return ``(raw_ops, observation_window)`` for release trace fields."""
    if _is_observation_window(window_ops):
        return raw_window, window_ops
    if window_ops:
        return window_ops, [op_to_observation(op) for op in window_ops]
    if _is_observation_window(raw_window):
        return [], raw_window
    return raw_window, [op_to_observation(op) for op in raw_window]


def _is_observation_window(window: list) -> bool:
    return bool(window) and isinstance(window[0], dict) and "event" in window[0]


def _resolve_repo_from_release_index(index: dict, release_path: Path) -> Path | None:
    context_relpath = index.get("context_repo_relpath")
    if context_relpath:
        candidate = release_path / context_relpath
        if candidate.exists():
            return candidate

    classify_relpath = index.get("classified_relpath")
    if classify_relpath:
        classified_path = release_path / "classify_output" / classify_relpath
        data_path = release_path / "data"
        if classified_path.exists() and data_path.exists():
            return resolve_repo_path(classified_path, release_path / "classify_output", data_path)

    return None
