#!/usr/bin/env python3
"""Build the public release dataset layout from a raw staging export."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "eval"))

from proactive_agent.data_utils import op_to_observation, resolve_repo_path


EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "coverage",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "Thumbs.db",
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "access_token.php",
    "credentials.json",
    "credentials.yaml",
    "credentials.yml",
    "secret.json",
    "secret.yaml",
    "secret.yml",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize_text_for_release(text: str) -> str:
    """Remove obvious local path and account identifiers from released traces."""
    if not text:
        return text

    text = unquote(text)
    text = re.sub(r"file:///[A-Za-z]:/[^<>\s'\"]+", _replace_file_uri_path, text)
    text = re.sub(r"(?<![A-Za-z])[A-Za-z]:[\\/][^<>\s'\"]+", _replace_local_path, text)
    text = re.sub(r"/home/[A-Za-z0-9._-]+/[^<>\s'\"]*", _replace_local_path, text)
    text = re.sub(r"/Users/[A-Za-z0-9._-]+/[^<>\s'\"]*", _replace_local_path, text)
    text = re.sub(r"/home/[A-Za-z0-9._-]+", "/home/user", text)
    text = re.sub(r"/Users/[A-Za-z0-9._-]+", "/Users/user", text)
    replacements = [
        (r"Users__[^_\\/]+__", "Users__user__"),
        (r"\\Users\\[^\\/\s<>'\"]+", r"\\Users\\user"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    secret_replacements = [
        (r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>"),
        (r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)(\s*[:=]\s*['\"]?)[A-Za-z0-9._~+/=@:-]{8,}", r"\1\2<redacted>"),
        (r"(?i)([A-Z0-9_]*ACCESS_KEY_ID)(\s*=\s*['\"]?)[A-Z0-9]{16,}", r"\1\2<redacted>"),
        (r"(?i)([A-Z0-9_]*SECRET_ACCESS_KEY)(\s*=\s*['\"]?)[A-Za-z0-9/+=]{20,}", r"\1\2<redacted>"),
        (r"AKIA[0-9A-Z]{16}", "<redacted-aws-key>"),
        (r"(?i)(OPENAI_API_KEY|OPENAI_API_BASE|JUDGE_API_KEY|JUDGE_API_BASE|ANTHROPIC_AUTH_TOKEN)(\s*=\s*['\"]?)[^\s'\"<>]+", r"\1\2<redacted>"),
        (r"(?<!\w)-p[0-9A-Za-z_@#$%^&*+=.,:;/-]{4,}", r"-p<redacted>"),
    ]
    for pattern, repl in secret_replacements:
        text = re.sub(pattern, repl, text)
    json_secret_replacements = [
        (r'("?(?:access_token|refresh_token|session_secret|api_key|secret|password)"?\s*:\s*")[^"]+"', r'\1<redacted>"'),
    ]
    for pattern, repl in json_secret_replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    text = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "<redacted-private-key>",
        text,
        flags=re.DOTALL,
    )
    return text


def _replace_file_uri_path(match: re.Match) -> str:
    return "file:///<local-path>/" + _safe_basename(match.group(0))


def _replace_local_path(match: re.Match) -> str:
    return "<local-path>/" + _safe_basename(match.group(0))


def _safe_basename(path_text: str) -> str:
    normalized = path_text.replace("\\", "/").rstrip("/>),.;:")
    base = normalized.rsplit("/", 1)[-1]
    if not base or len(base) > 80:
        return "file"
    if re.fullmatch(r"[0-9]+_.+", base):
        return base.rsplit("_", 1)[-1]
    return base


def anonymize_observation(op: dict) -> dict:
    obs = op_to_observation(op)
    obs["event"] = normalize_text_for_release(obs.get("event", ""))
    return obs


def should_skip(path: Path) -> bool:
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    return any(part in EXCLUDED_DIR_NAMES for part in path.parts)


def copy_context_repo(src: Path, dst: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    dst.mkdir(parents=True, exist_ok=True)

    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if should_skip(rel):
            if path.is_dir():
                continue
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_release_file(path, target)
        file_count += 1
        try:
            byte_count += target.stat().st_size
        except OSError:
            pass

    return file_count, byte_count


def copy_release_file(src: Path, dst: Path) -> None:
    """Copy a file, redacting obvious sensitive text when it is text-like."""
    try:
        data = src.read_bytes()
    except OSError:
        return
    if b"\0" in data[:4096]:
        shutil.copy2(src, dst)
        return

    text = None
    encoding = "utf-8"
    for candidate in ("utf-8", "gb18030"):
        try:
            text = data.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        shutil.copy2(src, dst)
        return

    redacted = normalize_text_for_release(text)
    if redacted == text:
        shutil.copy2(src, dst)
    else:
        dst.write_text(redacted, encoding=encoding, errors="ignore")
        try:
            shutil.copystat(src, dst)
        except OSError:
            pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_dataset(input_dir: Path, output_dir: Path, split: str, copy_repos: bool) -> dict:
    gt_path = input_dir / "ground_truth" / f"{split}.jsonl"
    trace_path = input_dir / "ide_traces" / f"{split}.jsonl"
    index_path = input_dir / "metadata" / "samples.jsonl"
    classify_dir = input_dir / "classify_output"
    raw_data_dir = input_dir / "data"

    gt_by_id = {row["sample_id"]: row for row in read_jsonl(gt_path)}
    trace_by_id = {row["sample_id"]: row for row in read_jsonl(trace_path)}
    index_by_id = {row["sample_id"]: row for row in read_jsonl(index_path)}
    sample_ids = sorted(set(gt_by_id) & set(trace_by_id) & set(index_by_id))

    repo_id_by_path: dict[Path, str] = {}
    repo_rows: list[dict] = []
    data_rows: list[dict] = []
    gt_rows: list[dict] = []
    trace_rows: list[dict] = []
    meta_rows: list[dict] = []

    for sample_id in sample_ids:
        gt = gt_by_id[sample_id]
        trace = trace_by_id[sample_id]
        index = index_by_id[sample_id]
        positive = gt.get("positive", {})
        classified_path = classify_dir / index["classified_relpath"]
        repo_path = resolve_repo_path(classified_path, classify_dir, raw_data_dir)
        if repo_path is None:
            continue
        repo_path = repo_path.resolve()

        if repo_path not in repo_id_by_path:
            repo_id = f"repo_{len(repo_id_by_path):06d}"
            repo_id_by_path[repo_path] = repo_id
            repo_relpath = f"context_repos/{repo_id}"
            file_count = 0
            byte_count = 0
            if copy_repos:
                file_count, byte_count = copy_context_repo(repo_path, output_dir / repo_relpath)
            repo_rows.append({
                "context_repo_id": repo_id,
                "context_repo_relpath": repo_relpath,
                "file_count": file_count,
                "bytes": byte_count,
            })

        repo_id = repo_id_by_path[repo_path]
        repo_relpath = f"context_repos/{repo_id}"
        window_ops = [anonymize_observation(op) for op in trace.get("window_ops", [])]
        task = normalize_text_for_release(positive.get("summary", "").strip())
        workflow = normalize_text_for_release(positive.get("detail", "").strip())

        data_row = {
            "sample_id": sample_id,
            "session_id": gt["session_id"],
            "source": "real",
            "trigger_op_id": gt["trigger_op_id"],
            "window_ops": window_ops,
            "task": task,
            "workflow": workflow,
            "context_repo_id": repo_id,
            "context_repo_relpath": repo_relpath,
        }
        data_rows.append(data_row)
        gt_rows.append({
            "sample_id": sample_id,
            "session_id": gt["session_id"],
            "trigger_op_id": gt["trigger_op_id"],
            "task": task,
            "workflow": workflow,
            "confidence": positive.get("confidence"),
        })
        trace_rows.append({
            "sample_id": sample_id,
            "session_id": gt["session_id"],
            "trigger_op_id": gt["trigger_op_id"],
            "window_ops": window_ops,
        })
        meta_rows.append({
            "sample_id": sample_id,
            "context_repo_id": repo_id,
            "context_repo_relpath": repo_relpath,
            "split": split,
        })

    write_jsonl(output_dir / "data" / f"{split}.jsonl", data_rows)
    write_jsonl(output_dir / "ground_truth" / f"{split}.jsonl", gt_rows)
    write_jsonl(output_dir / "ide_traces" / f"{split}.jsonl", trace_rows)
    write_jsonl(output_dir / "metadata" / "samples.jsonl", meta_rows)
    write_jsonl(output_dir / "metadata" / "repositories.jsonl", repo_rows)

    checksums = []
    for relpath in [
        f"data/{split}.jsonl",
        f"ground_truth/{split}.jsonl",
        f"ide_traces/{split}.jsonl",
        "metadata/samples.jsonl",
        "metadata/repositories.jsonl",
    ]:
        path = output_dir / relpath
        checksums.append({
            "path": relpath,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        })
    write_jsonl(output_dir / "metadata" / "checksums.jsonl", checksums)

    summary = {
        "split": split,
        "samples": len(data_rows),
        "sessions": len({row["session_id"] for row in data_rows}),
        "context_repositories": len(repo_rows),
        "copied_context_repositories": copy_repos,
    }
    (output_dir / "metadata" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_readme(output_dir, summary, split)
    return summary


def write_readme(output_dir: Path, summary: dict, split: str) -> None:
    readme = f"""---
configs:
- config_name: default
  data_files:
  - split: {split}
    path: data/{split}.jsonl
---

# Proactive Agent Release Dataset

This dataset contains proactive IDE-assistant evaluation samples.

## Layout

- `data/{split}.jsonl`: canonical Hugging Face-compatible task table.
- `ground_truth/{split}.jsonl`: labels only.
- `ide_traces/{split}.jsonl`: IDE trace windows only.
- `context_repos/`: anonymized context repository snapshots.
- `metadata/`: sample-to-repository mapping, repository manifest, checksums, and summary.

## Counts

- Samples: {summary["samples"]}
- Sessions: {summary["sessions"]}
- Context repositories: {summary["context_repositories"]}

## Fields

- `sample_id`: stable sample identifier.
- `session_id`: stable anonymous session identifier.
- `trigger_op_id`: operation id at which the proactive task should be proposed.
- `window_ops`: recent IDE observations in `{{time, event}}` format.
- `task`: ground-truth proactive task summary.
- `workflow`: ground-truth workflow.
- `context_repo_relpath`: relative path to the context repository snapshot.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a public release dataset layout.")
    parser.add_argument("--input-dir", default="local_release_staging")
    parser.add_argument("--output-dir", default="release_dataset")
    parser.add_argument("--split", default="test")
    parser.add_argument("--no-copy-context-repos", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    if output_dir.exists():
        if not args.overwrite:
            raise SystemExit(f"Output directory exists: {output_dir}. Pass --overwrite to replace it.")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = build_dataset(
        input_dir=input_dir,
        output_dir=output_dir,
        split=args.split,
        copy_repos=not args.no_copy_context_repos,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
