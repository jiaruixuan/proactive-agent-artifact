#!/usr/bin/env python3
"""
Calculate pass@1, pass@3, pass@5 from a single pass@5 judged result file.

Usage:
  python eval/calculate_pass_at_k.py --infile eval/predictions/xxx/samples_s141_seed0_pass_k5_judged_binary.json
"""

import json
import argparse
from pathlib import Path


def calc_pass_at_k(data: list, k: int) -> dict:
    """Calculate pass@k: a sample passes if ANY of the first k candidates is correct."""
    total = len(data)
    passed = 0
    has_proposal = 0

    for s in data:
        judgements = s.get("judgements", [])
        if not judgements:
            # Single candidate (pass@1 format)
            j = s.get("judgement", False)
            if k >= 1:
                if j:
                    passed += 1
                if s.get("agent_response") is not None:
                    has_proposal += 1
            continue

        # Take first k judgements
        first_k = judgements[:k]
        responses = s.get("agent_responses", [])[:k]

        # Count proposals in first k
        proposals_in_k = sum(1 for r in responses if r is not None)
        if proposals_in_k > 0:
            has_proposal += 1

        # Pass if any of first k is correct
        if any(j is True for j in first_k):
            passed += 1

    return {
        "k": k,
        "total": total,
        "passed": passed,
        "has_proposal": has_proposal,
        "accuracy": passed / total if total > 0 else 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--infile", required=True, help="Path to judged pass@k result file")
    parser.add_argument("--ks", default="1,3,5", help="Comma-separated k values to compute")
    args = parser.parse_args()

    data = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    ks = [int(k) for k in args.ks.split(",")]

    # Check max k available
    max_available = 1
    for s in data:
        jr = s.get("judgements", [])
        if jr:
            max_available = max(max_available, len(jr))
    print(f"Loaded {len(data)} samples, max candidates per sample: {max_available}")

    model = data[0].get("model", "unknown") if data else "unknown"
    print(f"Model: {model}")
    print()

    print(f"{'k':>5} | {'Passed':>8} | {'Total':>8} | {'Accuracy':>10}")
    print("-" * 40)
    for k in ks:
        if k > max_available:
            print(f"{k:>5} | {'N/A':>8} | {len(data):>8} | {'N/A':>10}  (only {max_available} candidates available)")
            continue
        result = calc_pass_at_k(data, k)
        print(f"{k:>5} | {result['passed']:>8} | {result['total']:>8} | {result['accuracy']:>10.4f}")


if __name__ == "__main__":
    main()
