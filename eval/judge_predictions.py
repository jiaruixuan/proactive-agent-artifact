#!/usr/bin/env python3
"""
Judge - Trigger-Only Version

Score samples produced by `run_chatbot_eval.py`.

Logic:
  - agent_response = None -> false negative, no LLM judge call.
  - agent_response != None -> compare prediction with ground truth via LLM judge.
  - accepted -> True, rejected -> False.

Metric:
  Accuracy@trigger = score_sum / total

Usage:
  python judge_predictions.py --infile predictions/samples_n100_seed0.json \
                       --outfile judged_predictions/samples_n100_seed0.json
"""

import asyncio
import json
import os
import re
import requests
import tqdm
import fire
from pathlib import Path
from typing import List, Dict, Optional

# ===== API config injected by main() =====
API_KEY  = ""
ENDPOINT = ""
MODEL    = "deepseek-v3.2"

sem = asyncio.Semaphore(16)

# ===== Judge Prompt (binary) =====
JUDGE_SYSTEM_BINARY = """\
You are an evaluator assessing whether a proactive AI assistant's suggestion matches the expected task.
Respond ONLY with a JSON object in this exact format:
{"judgement": "accepted" or "rejected", "reason": "one sentence explanation"}"""

JUDGE_USER_TMPL_BINARY = """\
Ground Truth (what should have been proposed):
- Task: {gt_task}
- Workflow: {gt_workflow}

Agent's Prediction (what the agent actually proposed):
- Task: {pred_task}
- Workflow: {pred_workflow}

Is the agent's prediction acceptable?
A prediction is acceptable if:
1. The proposed task is semantically aligned with the ground truth task.
2. The workflow covers the key steps from the ground truth — the same core files,
   functions, or operations are addressed — even if minor steps differ or the
   exact wording varies.

Respond with {{"judgement": "accepted"/"rejected", "reason": "..."}}"""

# ===== API call =====

def _sync_call(messages: List[Dict], temperature: float = 0.0) -> str:
    resp = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL, "messages": messages, "temperature": temperature, "max_tokens": 256},
        timeout=60,
    )
    if resp.status_code == 429:
        raise requests.HTTPError(response=resp)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def call_llm(messages: List[Dict]) -> str:
    loop = asyncio.get_event_loop()
    async with sem:
        for attempt in range(5):
            try:
                temperature = 0.0 if attempt == 0 else 0.3
                return await loop.run_in_executor(None, _sync_call, messages, temperature)
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 5 * (2 ** attempt)
                    print(f"\n  429 rate limit (attempt {attempt+1}), {wait}s ")
                    await asyncio.sleep(wait)
                else:
                    raise
    raise RuntimeError("API call failed after 5 rate-limit retries")


def parse_judgement(raw: str):
    """Parse LLM judge output.

    Returns:
        True / False / None (parse error)
    """
    try:
        if "```json" in raw:
            m = re.search(r"```json\s*(.*?)\s*```", raw, re.DOTALL)
            if m:
                raw = m.group(1)
        ret = json.loads(raw)
        label = ret.get("judgement", "").lower()
    except Exception:
        label = raw.lower()

    if "accepted" in label:
        return True
    if "rejected" in label:
        return False
    return None


# ===== Core scoring =====

def _score_value(j) -> float:
    """Convert judgement to a numeric score."""
    if j is True:
        return 1.0
    return 0.0


async def _judge_one_pred(pred: dict, gt: dict):
    """Judge one prediction and return (judgement, reason)."""
    pred_task     = pred.get("task", "")     if isinstance(pred, dict) else str(pred)
    pred_workflow = pred.get("workflow", "") if isinstance(pred, dict) else ""

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_BINARY},
        {"role": "user",   "content": JUDGE_USER_TMPL_BINARY.format(
            gt_task=gt.get("task", ""), gt_workflow=gt.get("workflow", ""),
            pred_task=pred_task, pred_workflow=pred_workflow,
        )},
    ]

    try:
        raw = await call_llm(messages)
        judgement = parse_judgement(raw)
        if judgement is None:
            print(f"\n  [warn] Could not parse judge output: {raw[:100]}")
            judgement = False
        try:
            reason = json.loads(raw).get("reason", "")
        except Exception:
            reason = ""
    except Exception as e:
        print(f"\n  [error] LLM judge failed: {e}")
        judgement = False
        reason    = f"error: {e}"

    return judgement, reason


async def judge_sample(sample: dict, pbar: tqdm.tqdm) -> None:
    """Write judgement and judge_reason fields in place."""
    preds = sample.get("agent_responses")  # list or None
    if preds is None:
        # Backward-compatible single-response format.
        single = sample.get("agent_response")
        preds = [single] if single is not None else []

    valid_preds = [p for p in preds if p is not None]

    if not valid_preds:
        sample["judgement"]    = False
        sample["judge_reason"] = "no proposal (FN)"
        pbar.update(1)
        return

    gt = sample.get("ground_truth", {})

    if len(valid_preds) == 1:
        j, r = await _judge_one_pred(valid_preds[0], gt)
        sample["judgement"]    = j
        sample["judge_reason"] = r
    else:
        results = await asyncio.gather(*[
            _judge_one_pred(p, gt)
            for p in valid_preds
        ])
        judgements = [j for j, r in results]
        reasons    = [r for j, r in results]

        best_idx = max(range(len(judgements)), key=lambda i: _score_value(judgements[i]))
        sample["judgement"]     = judgements[best_idx]
        sample["judge_reason"]  = reasons[best_idx]
        sample["judgements"]    = judgements
        sample["judge_reasons"] = reasons

    pbar.update(1)


async def run_judge(data: list) -> None:
    pbar = tqdm.tqdm(total=len(data), ncols=100, desc="judging")
    await asyncio.gather(*[judge_sample(s, pbar) for s in data])
    pbar.close()


# ===== Statistics =====

def print_stats(data: list) -> None:
    judged = [s for s in data if "judgement" in s]
    total  = len(judged)
    if total == 0:
        print("No judged samples")
        return

    model    = judged[0].get("model", "unknown")
    proposed = sum(1 for s in judged if s.get("agent_response") is not None)

    TP  = sum(1 for s in judged if s["judgement"] is True)
    FN  = total - TP
    acc = TP / total if total > 0 else 0.0

    print(f"\n{'='*45}")
    print(f"  Model:    {model}")
    print(f"  Samples:  {total}")
    print(f"  Proposed: {proposed}  / no proposal: {total - proposed}")
    print(f"  TP:      {TP}")
    print(f"  FN:      {FN}")
    print(f"  Accuracy@trigger: {acc:.4f}  ({TP}/{total})")
    print(f"{'='*45}")


# ===== Entrypoint =====

def main(
    infile:   str = None,
    outfile:  str = None,
    max_samples: int = 0,
    api_key:  str = "",
    endpoint: str = "",
    model:    str = "deepseek-v3.2",
):
    global API_KEY, ENDPOINT, MODEL
    API_KEY  = api_key  or os.environ.get("OPENAI_API_KEY", "")
    ep       = endpoint or os.environ.get("OPENAI_API_BASE", "")
    if ep and not ep.rstrip("/").endswith("/chat/completions"):
        ep = ep.rstrip("/") + "/chat/completions"
    ENDPOINT = ep
    MODEL    = model

    if not infile or not outfile:
        print("Usage: python judge_predictions.py --infile predictions/xxx.json --outfile judged_predictions/xxx.json [--max_samples 20]")
        return

    if os.path.exists(outfile):
        print(f"Warning: {outfile} already exists; exiting.")
        return

    data = json.loads(Path(infile).read_text(encoding="utf-8"))
    if max_samples > 0:
        data = data[:max_samples]
    print(f"Loaded {infile}: {len(data)} samples")

    asyncio.run(run_judge(data))
    print_stats(data)

    out_path = Path(outfile)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")
    print(f"Saved to {outfile}")


if __name__ == "__main__":
    fire.Fire(main)
