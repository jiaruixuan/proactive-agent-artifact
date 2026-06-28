#!/usr/bin/env python3
"""
Extend an existing pass@k result file to a higher k by appending extra calls.

Instead of re-running pass@5 from scratch (759×5 = 3795 calls),
this script reads an existing pass@1 or pass@3 result and only makes
the additional calls needed to reach the target k.

Examples:
  # Extend pass@1 (single) to pass@3 (adds 2 calls per sample)
  python eval/extend_pass_at_k.py \
      --infile eval/predictions/claude-sonnet-4-6_chatbot/samples_s156_seed0.json \
      --target-k 3

  # Extend pass@3 to pass@5 (adds 2 calls per sample)
  python eval/extend_pass_at_k.py \
      --infile eval/predictions/claude-sonnet-4-6_chatbot/samples_s156_seed0_pass_k3.json \
      --target-k 5

  # Specify API config
  python eval/extend_pass_at_k.py \
      --infile result.json --target-k 5 \
      --endpoint https://aihubmix.com/v1/chat/completions \
      --api-key YOUR_API_KEY --model claude-sonnet-4-6

Requires: classify_output/ directory (needs original ops to reconstruct prompts).
"""

import json
import os
import sys
import asyncio
from pathlib import Path

import fire
from tqdm import tqdm

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "eval"))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env", override=False)

from run_chatbot_eval import (
    SYSTEM, USER_TMPL, call_api, extract_pred,
    op_to_observation, collect_sessions_with_positives,
)
import run_chatbot_eval


def _get_existing_responses(sample: dict) -> tuple[list, list]:
    """Extract existing agent_responses and raw_responses from a sample.

    Handles both pass@k format (has agent_responses list) and
    single/pass@1 format (only has agent_response scalar).

    Returns copies so the originals are not mutated during extend.
    """
    if "agent_responses" in sample and isinstance(sample["agent_responses"], list):
        return list(sample["agent_responses"]), list(sample.get("raw_responses") or [])
    else:
        # Single result → wrap in list
        return (
            [sample.get("agent_response")],
            [sample.get("raw_response") or ""],
        )


def _build_sample_map(classify_path: Path) -> dict:
    """Load all samples from classify_output and build a {sample_id: sample} map."""
    sessions = collect_sessions_with_positives(classify_path)
    all_samples = [s for session in sessions for s in session]
    sample_map = {}
    for s in all_samples:
        sid = f"{s['session_id']}_op{s['trigger_op_id']}"
        sample_map[sid] = s
    return sample_map


def _build_user_content(orig_sample: dict) -> str:
    """Reconstruct the user message from original ops."""
    ops = orig_sample["ops"]
    idx = orig_sample["trigger_op_idx"]
    window = ops[max(0, idx - 50 + 1): idx + 1]
    obs_list = [op_to_observation(o) for o in window]
    return USER_TMPL.replace("[placeholder]", json.dumps(obs_list))


async def extend(
    infile: str,
    target_k: int,
    outfile: str = None,
    model: str = None,
    endpoint: str = None,
    api_key: str = None,
    workers: int = 8,
):
    """Extend existing results to a higher pass@k.

    Args:
        infile:    Path to existing result file (pass@1 or pass@k)
        target_k:  Target k value (e.g. 3 or 5)
        outfile:   Output file (default: auto-generated with new k suffix)
        model:     Model name (auto-detected from infile if not set)
        endpoint:  API endpoint (uses env var if not set)
        api_key:   API key (uses env var if not set)
        workers:   Max concurrent API calls
    """
    infile_path = Path(infile)
    if not infile_path.exists():
        print(f"Error: {infile} not found")
        return

    # ── Load existing results ──
    data = json.loads(infile_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(data)} samples from {infile}")

    # Detect current k per sample (use minimum across all samples)
    per_sample_k = [len(_get_existing_responses(s)[0]) for s in data]
    min_k = min(per_sample_k)
    max_k = max(per_sample_k)
    print(f"Current k: min={min_k}, max={max_k} across samples. Target k: {target_k}")

    if min_k >= target_k:
        print(f"All samples already at k>={target_k}, nothing to do.")
        return

    # Count how many samples actually need extra calls
    need_extend = sum(1 for k in per_sample_k if k < target_k)
    total_extra_calls = sum(max(0, target_k - k) for k in per_sample_k)
    print(f"Samples needing extension: {need_extend}/{len(data)}")
    print(f"Total extra API calls: {total_extra_calls}")

    # ── Setup API config ──
    if model is None:
        model = data[0].get("model", os.environ.get("DEFAULT_MODEL", "deepseek-v3.2"))
    run_chatbot_eval._model = model
    run_chatbot_eval._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    run_chatbot_eval._endpoint = endpoint or os.environ.get("OPENAI_API_BASE", "")
    if run_chatbot_eval._endpoint and not run_chatbot_eval._endpoint.rstrip("/").endswith("/chat/completions"):
        run_chatbot_eval._endpoint = run_chatbot_eval._endpoint.rstrip("/") + "/chat/completions"
    run_chatbot_eval.sem = asyncio.Semaphore(workers)

    print(f"Model: {model}")
    print(f"Endpoint: {run_chatbot_eval._endpoint}")

    # ── Output file ──
    if outfile is None:
        stem = infile_path.stem
        # Remove existing _pass_k* or _judged_* suffix
        for suffix in ("_judged_binary",):
            stem = stem.replace(suffix, "")
        if "_pass_k" in stem:
            stem = stem[:stem.index("_pass_k")]
        outfile = str(infile_path.parent / f"{stem}_pass_k{target_k}.json")
    outfile_path = Path(outfile)

    # ── Resume from partial output ──
    if outfile_path.exists() and str(outfile_path) != str(infile_path):
        try:
            existing = json.loads(outfile_path.read_text(encoding="utf-8"))
            if isinstance(existing, list) and len(existing) == len(data):
                already_done = sum(
                    1 for r in existing
                    if isinstance(r.get("agent_responses"), list)
                    and len(r["agent_responses"]) >= target_k
                )
                if already_done == len(data):
                    print(f"All samples already extended to k={target_k} in {outfile}")
                    return
                if already_done > 0:
                    print(f"Resume: {already_done}/{len(data)} already extended")
                    data = existing
        except Exception:
            pass

    # ── Load classify_output to reconstruct prompts ──
    classify_path = REPO_ROOT / "classify_output"
    if not classify_path.exists():
        print("Error: classify_output/ not found. Cannot reconstruct prompts.")
        print("This script must be run on a server with classify_output/ available.")
        return

    print("Loading classify_output to reconstruct prompts...")
    sample_map = _build_sample_map(classify_path)
    matched = sum(1 for d in data if d["sample_id"] in sample_map)
    unmatched = len(data) - matched
    print(f"Matched {matched}/{len(data)} samples" + (f" ({unmatched} unmatched)" if unmatched else ""))

    if matched == 0:
        print("Error: no samples matched. Check classify_output directory.")
        return

    # ── Extend ──
    lock = asyncio.Lock()
    pbar = tqdm(total=need_extend, desc=f"Extending to pass@{target_k}", ncols=100)

    async def extend_one(idx):
        sample = data[idx]
        sid = sample["sample_id"]

        responses, raws = _get_existing_responses(sample)

        # Skip if already at target_k
        if len(responses) >= target_k:
            return

        calls_needed = target_k - len(responses)

        # Reconstruct prompt
        orig = sample_map.get(sid)
        if orig is None:
            # Can't reconstruct, pad with None
            responses.extend([None] * calls_needed)
            raws.extend([""] * calls_needed)
        else:
            user_content = _build_user_content(orig)
            for _ in range(calls_needed):
                try:
                    messages = [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_content},
                    ]
                    raw = await call_api(messages, temperature=0.7)
                    raws.append(raw)
                    responses.append(extract_pred(raw))
                except Exception as e:
                    print(f"  [{sid}] extra call failed: {e}")
                    responses.append(None)
                    raws.append("")

        # Write back to sample
        sample["agent_responses"] = responses
        sample["raw_responses"] = raws
        sample["agent_response"] = next((r for r in responses if r is not None), None)
        sample["eval_mode"] = "pass_k"
        sample["k"] = target_k

        pbar.update(1)

    # Process in batches to avoid overwhelming the event loop
    batch_size = workers * 2
    for i in range(0, len(data), batch_size):
        batch_indices = list(range(i, min(i + batch_size, len(data))))
        await asyncio.gather(*[extend_one(idx) for idx in batch_indices])
        # Incremental save after each batch
        outfile_path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

    pbar.close()

    # Final save
    outfile_path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

    # Verify
    final_ks = [len(s.get("agent_responses", [])) for s in data]
    all_at_target = all(k >= target_k for k in final_ks)
    proposed = sum(1 for r in data if r.get("agent_response") is not None)

    print(f"\nDone: {len(data)} samples, target k={target_k}")
    print(f"  All at target: {'YES' if all_at_target else 'NO (some failed)'}")
    print(f"  Proposals (any non-null): {proposed}")
    print(f"  Saved to: {outfile}")
    print(f"\nTo judge: python eval/judge_predictions.py --infile {outfile}")


if __name__ == "__main__":
    fire.Fire(lambda **kw: asyncio.run(extend(**kw)))
