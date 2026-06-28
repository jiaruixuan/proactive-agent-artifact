#!/usr/bin/env python3
"""
One-stop eval script: inference → judge → print stats.

Usage:
  # chatbot mode (single call)
  python3 eval/run_evaluation.py --mode chatbot --model deepseek-v3.2

  # chatbot pass@k
  python3 eval/run_evaluation.py --mode chatbot --model deepseek-v3.2 --pass-k 5

  # chatbot hit@k
  python3 eval/run_evaluation.py --mode chatbot --model deepseek-v3.2 --hit-k 5

  # optional split filter for local classified data
  python3 eval/run_evaluation.py --mode chatbot --model deepseek-v3.2 \\
      --split-file path/to/splits.json --split test

Environment variables (required):
  OPENAI_API_KEY   - API key
  OPENAI_API_BASE  - endpoint base URL (from env var)
"""

import argparse
import json
import subprocess
import sys
import os
from pathlib import Path

EVAL_DIR = Path(__file__).parent


def run(cmd: list, desc: str, extra_env: dict = None) -> int:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"  CMD: {' '.join(str(c) for c in cmd)}")
    print(f"{'='*60}\n")
    env = None
    if extra_env:
        env = os.environ.copy()
        env.update(extra_env)
    result = subprocess.run(cmd, cwd=EVAL_DIR.parent, env=env)
    return result.returncode


def find_latest_output(out_dir: Path, pattern: str) -> Path | None:
    """Find the most recently modified file matching pattern in out_dir."""
    candidates = sorted(out_dir.glob(pattern), key=lambda f: f.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main():
    parser = argparse.ArgumentParser(description="Run inference + judge in one step")

    # Mode
    parser.add_argument("--mode", choices=["chatbot"], default="chatbot",
                        help="Inference mode")

    # Inference params
    parser.add_argument("--model", default="deepseek-v3.2", help="Model name")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""),
                        help="API key (overrides OPENAI_API_KEY env var)")
    parser.add_argument("--endpoint", default=os.environ.get("OPENAI_API_BASE", ""),
                        help="API endpoint base URL (overrides OPENAI_API_BASE env var)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-sessions", type=int, default=0, help="0 = all sessions")
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--pass-k", type=int, default=1, help="pass@k: run k independent calls")
    parser.add_argument("--hit-k", type=int, default=1, help="hit@k: ask model for k candidates")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--classify-dir", default=None)
    parser.add_argument("--hf-data", default=None, help="Path to HF dataset JSONL or directory")
    parser.add_argument("--release-data-dir", default=None,
                        help="Path to public release dataset directory")

    # Split filter
    parser.add_argument("--split-file", default=None, help="Optional split JSON file for local classified data")
    parser.add_argument("--split", default="test", help="Which split to use (test/validation)")

    # Judge params
    parser.add_argument("--judge-api-key", default=os.environ.get("JUDGE_API_KEY", ""),
                        help="API key for judge (from JUDGE_API_KEY env var)")
    parser.add_argument("--judge-endpoint", default=os.environ.get("JUDGE_API_BASE", ""),
                        help="Endpoint for judge (from JUDGE_API_BASE env var)")
    parser.add_argument("--judge-model", default="deepseek-v3.2",
                        help="Model for judge")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Only run inference, skip judge step")

    # Output
    parser.add_argument("--outfile", default=None, help="Output file for inference results")
    parser.add_argument("--judged-outfile", default=None, help="Output file for judged results")

    args = parser.parse_args()
    # ── Step 1: Inference ──
    infer_cmd = [sys.executable, "eval/run_chatbot_eval.py",
                 "--model", args.model,
                 "--seed", str(args.seed),
                 "--workers", str(args.workers),
                 "--pass-k", str(args.pass_k),
                 "--hit-k", str(args.hit_k),
                 ]
    if args.max_sessions > 0:
        infer_cmd += ["--max-sessions", str(args.max_sessions)]
    if args.max_samples > 0:
        infer_cmd += ["--max-samples", str(args.max_samples)]
    if args.api_key:
        infer_cmd += ["--api-key", args.api_key]
    if args.endpoint:
        infer_cmd += ["--endpoint", args.endpoint]
    if args.data_dir:
        infer_cmd += ["--data-dir", args.data_dir]
    if args.classify_dir:
        infer_cmd += ["--classify-dir", args.classify_dir]
    if args.hf_data:
        infer_cmd += ["--hf-data", args.hf_data]
    if args.release_data_dir:
        infer_cmd += ["--release-data-dir", args.release_data_dir]
    if args.split_file:
        infer_cmd += ["--split-file", args.split_file]
    if args.hf_data or args.release_data_dir or args.split_file:
        infer_cmd += ["--split", args.split]
    if args.outfile:
        infer_cmd += ["--outfile", args.outfile]

    # Run inference
    rc = run(infer_cmd, "Step 1: chatbot inference")
    if rc != 0:
        print(f"[ERROR] Inference failed (exit code {rc})")
        sys.exit(rc)

    if args.skip_judge:
        print("--skip-judge set, done.")
        return

    # ── Find inference output file ──
    if args.outfile:
        infer_out = Path(args.outfile)
    else:
        base = Path("eval/predictions")
        model_tag = args.model.replace("/", "_").replace(":", "_")
        model_dir = base / f"{model_tag}_chatbot"
        infer_out = find_latest_output(model_dir, "*.json")
        if infer_out is None:
            print(f"[ERROR] Could not find inference output in {model_dir}. Use --outfile to specify explicitly.")
            sys.exit(1)
        print(f"Auto-detected inference output: {infer_out}")

    # ── Step 2: Judge ──
    if args.judged_outfile:
        judged_out = args.judged_outfile
    else:
        judged_dir = infer_out.parent
        judged_out = str(judged_dir / infer_out.name.replace(".json", "_judged_binary.json"))

    judge_cmd = [sys.executable, "eval/judge_predictions.py",
                 "--infile", str(infer_out),
                 "--outfile", judged_out,
                 "--api-key", args.judge_api_key,
                 "--endpoint", args.judge_endpoint,
                 "--model", args.judge_model,
                 ]

    rc = run(judge_cmd, "Step 2: judge")
    if rc != 0:
        print(f"[ERROR] Judge failed (exit code {rc})")
        sys.exit(rc)

    # ── Step 3: Calculate pass@k stats ──
    if args.pass_k > 1:
        calc_cmd = [sys.executable, "eval/calculate_pass_at_k.py",
                    "--infile", judged_out,
                    "--ks", ",".join(str(k) for k in range(1, args.pass_k + 1, 2) if k <= args.pass_k)
                    ]
        # Generate ks: 1,3,5 for pass_k=5; 1,3 for pass_k=3; etc.
        ks = sorted(set([1] + list(range(3, args.pass_k + 1, 2)) + [args.pass_k]))
        calc_cmd = [sys.executable, "eval/calculate_pass_at_k.py",
                    "--infile", judged_out,
                    "--ks", ",".join(str(k) for k in ks),
                    ]
        rc = run(calc_cmd, "Step 3: pass@k stats")

    print(f"\nAll done. Judged results: {judged_out}")


if __name__ == "__main__":
    main()
