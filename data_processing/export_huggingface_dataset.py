#!/usr/bin/env python3
"""
Export classified data to HuggingFace dataset format.

Produces JSONL files organized by split:
  hf_dataset/
    train_synthetic.jsonl   # 
    train_real.jsonl         #  test/val sessions
    test.jsonl               # 
    val.jsonl                # 

Each row:
{
    "session_id": "session_xxx",
    "source": "synthetic" | "real",
    "trigger_op_id": 12345,
    "window_ops": [ {time, event}, ... ],   # WINDOW_SIZE ops before trigger
    "task": "one-sentence summary",
    "workflow": "numbered operator steps",
    "confidence": 0.9,
    "reasoning": "why this is a proactive task"
}

Usage:
    python data_processing/export_huggingface_dataset.py
    python data_processing/export_huggingface_dataset.py --output-dir hf_dataset --window-size 50
"""

import json
import argparse
from pathlib import Path

WINDOW_SIZE = 50


def op_to_observation(op: dict, max_detail_chars: int = 2000) -> dict:
    details = op.get('readableDetails', '')
    if len(details) > max_detail_chars:
        details = details[:max_detail_chars] + f"... [truncated, {len(details)} chars total]"
    return {
        "time": str(op.get("timestamp", "")),
        "event": f"[{op.get('operationType', 'unknown')}]\n{details}"
    }


def load_split_ids(split_file: str) -> tuple[set, set]:
    """Return (test_ids, val_ids) as short session id sets."""
    data = json.loads(Path(split_file).read_text(encoding="utf-8"))
    test_ids = {e["session_id"].replace("session_", "") for e in data.get("test", {}).get("data", [])}
    val_ids = {e["session_id"].replace("session_", "") for e in data.get("val", {}).get("data", [])}
    return test_ids, val_ids


def extract_samples(classify_dir: Path, source: str, window_size: int) -> list[dict]:
    """Extract all positive samples from classified JSON files."""
    samples = []
    for f in sorted(classify_dir.rglob("*_classified.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        ops = data.get("operations", [])
        op_index = {op["id"]: i for i, op in enumerate(ops) if "id" in op}
        session_id = data.get("session_id", "")

        for pos in data.get("positive", []):
            summary = pos.get("summary", "")
            if not summary or summary.lower().startswith("even split"):
                continue

            tid = pos.get("trigger_after_op_id")
            if tid is None or tid not in op_index:
                continue

            trigger_idx = op_index[tid]
            window = ops[max(0, trigger_idx - window_size + 1): trigger_idx + 1]
            window_obs = [op_to_observation(o) for o in window]

            samples.append({
                "session_id": session_id,
                "source": source,
                "trigger_op_id": tid,
                "window_ops": window_obs,
                "task": summary,
                "workflow": pos.get("detail", ""),
                "reasoning": pos.get("reasoning", ""),
            })

    return samples


def save_jsonl(samples: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"  {path.name}: {len(samples)} samples")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic-dir", default="synthetic_data/scenarios/synthetic_classified")
    parser.add_argument("--real-dir", default="classify_output")
    parser.add_argument("--split-file", default=None, help="Optional split JSON file for held-out test/validation session IDs")
    parser.add_argument("--output-dir", default="hf_dataset")
    parser.add_argument("--window-size", type=int, default=WINDOW_SIZE)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if args.split_file:
        test_ids, val_ids = load_split_ids(args.split_file)
        print(f"Split: test={len(test_ids)}, val={len(val_ids)} sessions")
    else:
        test_ids, val_ids = set(), set()
        print("No split file provided; exporting all real samples to train_real.jsonl")

    # Extract all samples
    print("Loading synthetic data...")
    syn_samples = extract_samples(Path(args.synthetic_dir), "synthetic", args.window_size)
    print(f"  Total synthetic: {len(syn_samples)}")

    print("Loading real data...")
    real_samples = extract_samples(Path(args.real_dir), "real", args.window_size)
    print(f"  Total real: {len(real_samples)}")

    # Split real data by session_id
    real_train, real_test, real_val = [], [], []
    for s in real_samples:
        short_id = s["session_id"].replace("session_", "").split("-")[0]
        if short_id in test_ids:
            real_test.append(s)
        elif short_id in val_ids:
            real_val.append(s)
        else:
            real_train.append(s)

    print(f"\nSplit results:")
    print(f"  Synthetic train: {len(syn_samples)}")
    print(f"  Real train:      {len(real_train)}")
    print(f"  Test:            {len(real_test)}")
    print(f"  Val:             {len(real_val)}")

    # Save
    print(f"\nSaving to {output_dir}/")
    save_jsonl(syn_samples, output_dir / "train_synthetic.jsonl")
    save_jsonl(real_train, output_dir / "train_real.jsonl")
    save_jsonl(real_test, output_dir / "test.jsonl")
    save_jsonl(real_val, output_dir / "val.jsonl")

    # Dataset card
    card = f"""---
license: apache-2.0
task_categories:
  - text-generation
language:
  - en
tags:
  - proactive-agent
  - ide-assistant
  - code
size_categories:
  - 10K<n<100K
---

# Proactive Agent Dataset

Dataset for training and evaluating proactive IDE assistants.

## Splits

| Split | Samples | Description |
|---|---|---|
| train_synthetic | {len(syn_samples)} | Synthetic data from simulator traces |
| train_real | {len(real_train)} | Real IDE sessions (excluding test/val) |
| test | {len(real_test)} | Test split from real sessions |
| val | {len(real_val)} | Validation split from real sessions |

## Fields

- `session_id`: unique session identifier
- `source`: "synthetic" or "real"
- `trigger_op_id`: operation ID after which the proactive task should trigger
- `window_ops`: list of {{time, event}} observations (last {args.window_size} ops before trigger)
- `task`: one-sentence task summary (ground truth)
- `workflow`: numbered workflow steps using Edit/Execute/Search/User_Request operators
- `reasoning`: why this is a valid proactive task
"""
    (output_dir / "README.md").write_text(card, encoding="utf-8")
    print(f"  README.md: dataset card")
    print("\nDone!")


if __name__ == "__main__":
    main()
