#!/usr/bin/env python3
"""
Proactive Agent Eval - Trigger-Only Version

Evaluation setup:
- Sample sessions with positive tasks from `classify_output`.
- For each sample, use the last `WINDOW_SIZE` operations before the trigger.
- Call the model once unless pass@k or hit@k is requested.
- Report trigger-level true positives / false negatives only.

Output:
  predictions/<model>/samples_s{N}_seed{S}.json

Usage:
  # Default model
  python run_chatbot_eval.py

  # OpenAI-compatible endpoint
  python run_chatbot_eval.py --model gpt-4o \
                        --endpoint https://api.openai.com/v1/chat/completions \
                        --api-key YOUR_API_KEY

  # Smoke test
  python run_chatbot_eval.py --max-sessions 3
"""

import re
import os
import json
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_load_dotenv(_Path(__file__).parent.parent / ".env", override=False)
import random
import asyncio
import requests
from pathlib import Path
from typing import List, Dict, Optional

from tqdm.asyncio import tqdm_asyncio as asyctqdm

# ===== Default API config =====
DEFAULT_API_KEY  = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
# Append /chat/completions for endpoints that provide only the API base URL.
if DEFAULT_ENDPOINT and not DEFAULT_ENDPOINT.rstrip("/").endswith("/chat/completions"):
    DEFAULT_ENDPOINT = DEFAULT_ENDPOINT.rstrip("/") + "/chat/completions"
DEFAULT_MODEL    = "deepseek-v3.2"

# ===== Data config =====
CLASSIFY_DIR = Path(__file__).parent.parent / "classify_output"
DATA_DIR     = Path(__file__).parent.parent / "data"
OUTPUT_DIR   = Path(__file__).parent / "predictions"
MAX_SESSIONS = 0  # 0 = no limit, use all sessions
WINDOW_SIZE  = 50

sem = asyncio.Semaphore(8)  # overridden by --workers in main()

# Runtime config injected by main() and used by _sync_call.
_api_key  = DEFAULT_API_KEY
_endpoint = DEFAULT_ENDPOINT
_model    = DEFAULT_MODEL

# ===== Prompt =====
SYSTEM = """\
<Role> You are a helpful assistant that provides proactive suggestions to the user working in an IDE. </Role>

<Task> Understand what the user is doing based on their recent IDE operations and anticipate their needs. You must always propose a proactive task based on the operations. </Task>

<Format> Respond in the following JSON format:
{
    "Proactive Task": {
        "task": "A concise one-sentence summary of the task you propose.",
        "workflow": "Numbered workflow steps (see WorkflowFormat below)."
    }
}
</Format>

<WorkflowFormat>
The "workflow" field must use ONLY these operators:
  Edit(file/function): what was changed
  Execute(command): what was run and the outcome
  Search(file or keyword): what was searched for
  Ask_Agent: request assistance from another agent

Rules:
  - Be specific about actual file and function names in operator arguments.
  - Omit non-semantic actions (Open, Close, Select, Cursor movements).
  - Keep chronological order; one operator per logical step.
  - Number each step starting from 1.
</WorkflowFormat>

<Rules>
- You must always propose a task. Never return null.
- Ensure the proposed task is relevant to the recent operations.
- Focus on the user's current needs and predict helpful tasks.
- Consider the timing and sequence of operations.
- Deduce the user's purpose based on operation history.
</Rules>

<Example>
{"Proactive Task": {"task": "Fix homepage navigation bar disappearance by restoring header content", "workflow": "1. Ask_Agent: homepage navigation bar disappeared\\n2. Edit(index.html/header): restore navigation bar with links to index.html, form.html, user.html"}}
</Example>"""

# ===== hit@k prompt =====
SYSTEM_HIT_K = """\
<Role> You are a helpful assistant that provides proactive suggestions to the user working in an IDE. </Role>

<Task> Understand what the user is doing based on their recent IDE operations and anticipate their needs. Propose up to {k} different candidate tasks that the user might need help with, ranked by likelihood. </Task>

<Format> Respond in the following JSON format:
{{
    "Proactive Tasks": [
        {{
            "task": "Most likely task.",
            "workflow": "Numbered workflow steps (see WorkflowFormat below)."
        }},
        {{
            "task": "Second most likely task.",
            "workflow": "Numbered workflow steps (see WorkflowFormat below)."
        }}
    ]
}}
You must always propose exactly {k} candidate tasks. Never return an empty list.
</Format>

<WorkflowFormat>
The "workflow" field must use ONLY these operators:
  Edit(file/function): what was changed
  Execute(command): what was run and the outcome
  Search(file or keyword): what was searched for
  Ask_Agent: request assistance from another agent

Rules:
  - Be specific about actual file and function names in operator arguments.
  - Omit non-semantic actions (Open, Close, Select, Cursor movements).
  - Keep chronological order; one operator per logical step.
  - Number each step starting from 1.
</WorkflowFormat>

<Rules>
- Propose up to {k} distinct candidate tasks, ranked from most to least likely.
- Each candidate should represent a DIFFERENT interpretation or scope of what the user might need.
- Ensure each proposed task is relevant to the recent operations.
- Focus on the user's current needs and predict helpful tasks.
- Consider the timing and sequence of operations.
- You must always propose exactly {k} candidate tasks. Never return an empty list.
</Rules>

<Example>
{{"Proactive Tasks": [{{"task": "Update test files and CMakeLists for TLR renaming", "workflow": "1. Edit(t_identity.cpp): rename MTYPE, ITYPE from DTLR to TLR\\n2. Edit(CMakeLists.txt): add t_decode_tlr target and clean up formatting"}}]}}
</Example>"""

USER_TMPL = json.dumps({
    "Instructions": "Analyze the following recent IDE operations and provide a proactive task if the user needs help. The last operation is the most recent one.",
    "Observation": "[placeholder]"
})


# ===== Data sampling =====

def load_samples_from_jsonl(jsonl_path: str) -> List[List[dict]]:
    """Load test samples from HF dataset JSONL, grouped by session."""
    from collections import defaultdict
    session_map = defaultdict(list)
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            session_map[row["session_id"]].append({
                "session_id": row["session_id"],
                "trigger_op_id": row["trigger_op_id"],
                "trigger_op_idx": len(row["window_ops"]) - 1,
                "ground_truth": {
                    "task": row["task"],
                    "workflow": row["workflow"],
                },
                "ops_window": row["window_ops"],  # pre-built observations
            })
    return list(session_map.values())


def collect_sessions_with_positives(classify_dir: Path, allowed_session_ids=None) -> List[List[dict]]:
    """
    Collect sessions that contain at least one positive task.

    Returns a list where each element contains all positive-task samples for one
    session.
    """
    sessions = []
    for f in sorted(classify_dir.rglob("*_classified.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            positives = data.get("positive", [])
            if not positives:
                continue
            if allowed_session_ids is not None:
                short_id = data.get("session_id", "").replace("session_", "").split("-")[0]
                if short_id not in allowed_session_ids:
                    continue
            ops = data.get("operations", [])
            op_index = {op["id"]: i for i, op in enumerate(ops) if "id" in op}
            samples = []
            for pos in positives:
                tid = pos.get("trigger_after_op_id")
                if tid is None or tid not in op_index:
                    continue
                if pos.get("summary", "").lower().startswith("even split"):
                    continue
                samples.append({
                    "session_id":     data["session_id"],
                    "trigger_op_id":  tid,
                    "trigger_op_idx": op_index[tid],
                    "ground_truth": {
                        "task":     pos.get("summary", ""),
                        "workflow": pos.get("detail", ""),
                    },
                    "ops": ops,
                })
            if samples:
                sessions.append(samples)
        except Exception:
            continue
    return sessions


def sample_from_sessions(sessions: List[List[dict]], n_sessions: int, seed: int) -> List[dict]:
    """Sample sessions and flatten their positive-task samples."""
    rng = random.Random(seed)
    chosen = rng.sample(sessions, min(n_sessions, len(sessions)))
    return [sample for session in chosen for sample in session]


# ===== Utilities =====

def op_to_observation(op: dict, max_detail_chars: int = 2000) -> dict:
    details = op.get('readableDetails', '')
    if len(details) > max_detail_chars:
        details = details[:max_detail_chars] + f"... [truncated, {len(details)} chars total]"
    return {
        "time":  str(op.get("timestamp", 0) / 1000),
        "event": f"[{op.get('operationType', 'unknown')}]\n{details}"
    }


# ===== API call =====

def _sync_call(messages: List[Dict], temperature: float = 0.0) -> str:
    max_tok_key = "max_completion_tokens" if "gpt" in _model.lower() else "max_tokens"
    max_tok_val =  8192
    body = {"model": _model, "messages": messages, "temperature": temperature, max_tok_key: max_tok_val}
    # enable_thinking is DeepSeek-specific; skip for other models
    model_lower = _model.lower()
    if "deepseek" in model_lower or "glm" in model_lower or "qwen" in model_lower or "minimax" in model_lower:
        body["enable_thinking"] = False
    # Fine-tuned models: stop at </workflow> tag and add repetition penalty
    if "sft" in model_lower:
        body["stop"] = ["</workflow>"]
    resp = requests.post(
        _endpoint,
        headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=300,
    )
    if resp.status_code == 429:
        raise requests.HTTPError(response=resp)
    if resp.status_code >= 400:
        print(f"  API error {resp.status_code}: {resp.text[:500]}")
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def call_api(messages: List[Dict], temperature: float = 0.0) -> str:
    loop = asyncio.get_event_loop()
    async with sem:
        for attempt in range(8):
            try:
                return await loop.run_in_executor(
                    None, lambda: _sync_call(messages, temperature)
                )
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 10 * (2 ** min(attempt, 4))  # 10s, 20s, 40s, 80s, 160s...
                    print(f"  429 rate limit (attempt {attempt+1}/8), {wait}s ")
                    await asyncio.sleep(wait)
                else:
                    raise
        raise RuntimeError("API call failed after 8 rate-limit retries")


def extract_pred(s: str) -> Optional[dict]:
    """Parse an LLM response into a Proactive Task dict, or None."""
    try:
        # Strip thinking tags (e.g. Qwen3 thinking mode)
        s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL).strip()
        if "```json" in s:
            m = re.findall(r"```json\n(.*?)\n```", s, re.DOTALL)
            if m:
                s = m[0]
        # Try parsing directly first
        try:
            ret = json.loads(s)
        except json.JSONDecodeError:
            # Fix common issues: trailing extra braces, missing closing brace
            # Try stripping trailing braces one by one
            fixed = s.rstrip()
            while fixed.endswith("}") and fixed.count("}") > fixed.count("{"):
                fixed = fixed[:-1].rstrip()
            if fixed != s:
                ret = json.loads(fixed)
            else:
                raise
        pt = ret.get("Proactive Task")
        if pt in (None, "null", ""):
            return None
        if isinstance(pt, dict):
            # Strip <task></task> and <workflow></workflow> tags from trained models
            if "task" in pt:
                pt["task"] = re.sub(r"</?task>", "", pt["task"]).strip()
            if "workflow" in pt:
                pt["workflow"] = re.sub(r"</?workflow>", "", pt["workflow"]).strip()
            return pt
        return {"task": str(pt), "workflow": ""}
    except Exception:
        return None


def extract_pred_multi(s: str) -> list:
    """Parse a hit@k response into a list of task candidates."""
    try:
        s = re.sub(r"<think>.*?</think>", "", s, flags=re.DOTALL).strip()
        if "```json" in s:
            m = re.findall(r"```json\n(.*?)\n```", s, re.DOTALL)
            if m:
                s = m[0]
        ret = json.loads(s)
        pts = ret.get("Proactive Tasks", [])
        if not pts:
            return [None]
        results = []
        for pt in pts:
            if isinstance(pt, dict) and pt.get("task"):
                results.append({"task": pt["task"], "workflow": pt.get("workflow", "")})
        return results if results else [None]
    except Exception:
        return [None]


# ===== Single-sample inference =====

async def process_sample(item: dict, pbar, pass_k: int = 1, hit_k: int = 1) -> dict:
    """
    Run inference for one positive-task sample.

    - pass_k > 1: run k independent calls with temperature > 0.
    - hit_k > 1: run one call that asks for k candidates.
    - otherwise: run one call and return one candidate.
    """
    if "ops_window" in item:
        obs_list = item["ops_window"]
        window_size = len(obs_list)
    else:
        ops     = item["ops"]
        idx     = item["trigger_op_idx"]
        window  = ops[max(0, idx - WINDOW_SIZE + 1): idx + 1]
        obs_list = [op_to_observation(o) for o in window]
        window_size = len(window)
    user_content = USER_TMPL.replace("[placeholder]", json.dumps(obs_list))

    agent_responses = []
    raw_responses   = []

    try:
        if hit_k > 1:
            # hit@k: one call returns k candidates.
            system = SYSTEM_HIT_K.format(k=hit_k)
            messages = [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_content},
            ]
            raw = await call_api(messages, temperature=0.0)
            raw_responses = [raw]
            agent_responses = extract_pred_multi(raw)

        elif pass_k > 1:
            # pass@k: k independent calls with per-call error handling.
            for _ in range(pass_k):
                try:
                    messages = [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user",   "content": user_content},
                    ]
                    raw = await call_api(messages, temperature=0.7)
                    raw_responses.append(raw)
                    agent_responses.append(extract_pred(raw))
                except Exception as e:
                    print(f"  [{item['session_id']}] op {item['trigger_op_id']} pass@k call failed: {e}")
                    agent_responses.append(None)

        else:
            # Single-candidate mode.
            messages = [
                {"role": "system", "content": SYSTEM},
                {"role": "user",   "content": user_content},
            ]
            raw = await call_api(messages, temperature=0.0)
            raw_responses = [raw]
            agent_responses = [extract_pred(raw)]

    except Exception as e:
        print(f"  [{item['session_id']}] op {item['trigger_op_id']} API call failed: {e}")
        if not agent_responses:
            agent_responses = [None]

    # Backward-compatible scalar response: first non-null candidate, or None.
    agent_response = next((r for r in agent_responses if r is not None), None)

    result = {
        "sample_id":      f"{item['session_id']}_op{item['trigger_op_id']}",
        "session_id":     item["session_id"],
        "trigger_op_id":  item["trigger_op_id"],
        "model":          _model,
        "ground_truth":   item["ground_truth"],
        "window_size":    window_size,
        "agent_response": agent_response,
        "raw_response":   raw_responses[0] if raw_responses else None,
    }
    # Store the full candidate list for pass@k / hit@k modes.
    if pass_k > 1 or hit_k > 1:
        result["agent_responses"] = agent_responses
        result["raw_responses"]   = raw_responses
        result["eval_mode"]       = "hit_k" if hit_k > 1 else "pass_k"
        result["k"]               = hit_k if hit_k > 1 else pass_k

    return result


# ===== Agent-aligned max_samples mode =====

def collect_agent_aligned_samples(
    classify_dir: Path,
    data_dir: Path,
    max_samples: int,
    max_sessions: int,
    seed: int,
    window_size: int,
    allowed_session_ids=None,
) -> tuple[list[dict], int]:
    """
    Select samples that have repository snapshots, matching the historical
    agent-aligned sampling mode.

    Returns:
        (samples, n_sessions_chosen)
    """
    from proactive_agent.data_utils import collect_valid_samples

    all_samples = collect_valid_samples(classify_dir, data_dir, window_size, allowed_session_ids)
    print(f"Found {len(all_samples)} valid samples with repository snapshots")

    sessions: dict = {}
    for s in all_samples:
        sessions.setdefault(s["session_id"], []).append(s)
    session_list = list(sessions.values())

    rng = random.Random(seed)
    rng.shuffle(session_list)

    samples: list[dict] = []
    n_sessions_chosen = 0
    for session in session_list:
        if max_sessions > 0 and n_sessions_chosen >= max_sessions:
            break
        samples.extend(session)
        n_sessions_chosen += 1
        if max_samples > 0 and len(samples) >= max_samples:
            samples = samples[:max_samples]
            break

    return samples, n_sessions_chosen


# ===== Main =====

async def main(
    max_sessions: int = MAX_SESSIONS,
    max_samples:  int = 0,           # >0: agent-aligned sample count mode
    seed:         int = 0,
    model:        str = DEFAULT_MODEL,
    endpoint:     str = DEFAULT_ENDPOINT,
    api_key:      str = DEFAULT_API_KEY,
    data_dir:     str = None,
    classify_dir: str = None,
    hf_data:      str = None,        # HF dataset JSONL
    release_data_dir: str = None,    # Public release dataset directory
    outfile:      str = None,
    pass_k:       int = 1,           # >1: independent calls per sample (pass@k)
    hit_k:        int = 1,           # >1: one call returns k candidates (hit@k)
    split_file:   str = None,        # optional split JSON path
    split:        str = "test",      # split name, e.g. test or val
    workers:      int = 8,           # asyncio semaphore
):
    if pass_k > 1 and hit_k > 1:
        print("Error: pass_k and hit_k cannot both be greater than 1.")
        return

    # Inject runtime API config.
    global _api_key, _endpoint, _model, sem
    sem = asyncio.Semaphore(workers)
    _api_key  = api_key
    _endpoint = endpoint
    _model    = model

    classify_path = Path(classify_dir) if classify_dir else CLASSIFY_DIR
    data_path     = Path(data_dir)     if data_dir     else DATA_DIR

    eval_mode = "pass_k" if pass_k > 1 else ("hit_k" if hit_k > 1 else "single")
    k_val = max(pass_k, hit_k)
    print(f"Model: {model}  (eval_mode={eval_mode}, k={k_val})")

    # Load split filter if specified
    allowed_session_ids = None
    if split_file:
        import json as _json
        _split_data = _json.loads(Path(split_file).read_text(encoding="utf-8"))
        _entries = _split_data.get(split, {}).get("data", [])
        allowed_session_ids = {e["session_id"].replace("session_", "") for e in _entries}
        print(f"Using split '{split}' from {split_file}: {len(allowed_session_ids)} sessions")

    if release_data_dir:
        from proactive_agent.release_data import group_samples_by_session, load_release_samples

        print(f"Loading release data from: {release_data_dir}")
        release_samples = load_release_samples(release_data_dir, split=split)
        sessions = group_samples_by_session(release_samples)
        print(f"Found {len(sessions)} sessions with {len(release_samples)} release samples")
        if max_sessions == 0:
            samples = [s for session in sessions for s in session]
            n_sessions_chosen = len(sessions)
        else:
            samples = sample_from_sessions(sessions, max_sessions, seed)
            n_sessions_chosen = min(max_sessions, len(sessions))
        print(f"Selected {n_sessions_chosen} sessions, {len(samples)} samples (seed={seed})")
        k_suffix = f"_{eval_mode}{k_val}" if k_val > 1 else ""
        outfile_default = f"samples_s{n_sessions_chosen}_seed{seed}{k_suffix}.json"

    elif hf_data:
        # HF dataset JSONL mode.
        hf_path = Path(hf_data)
        if hf_path.is_dir():
            jsonl_file = str(hf_path / f"{split}.jsonl")
        else:
            jsonl_file = str(hf_path)
        print(f"Loading samples from JSONL: {jsonl_file}")
        sessions = load_samples_from_jsonl(jsonl_file)
        print(f"Found {len(sessions)} sessions with positive samples")
        if max_sessions == 0:
            samples = [s for session in sessions for s in session]
            n_sessions_chosen = len(sessions)
        else:
            samples = sample_from_sessions(sessions, max_sessions, seed)
            n_sessions_chosen = min(max_sessions, len(sessions))
        print(f"Selected {n_sessions_chosen} sessions, {len(samples)} samples (seed={seed})")
        k_suffix = f"_{eval_mode}{k_val}" if k_val > 1 else ""
        outfile_default = f"samples_s{n_sessions_chosen}_seed{seed}{k_suffix}.json"
    elif max_samples > 0:
        # Agent-aligned sampling mode.
        print(f"[max_samples mode] Agent-aligned sampling (max_samples={max_samples}, seed={seed})...")
        samples, n_sessions_chosen = collect_agent_aligned_samples(
            classify_path, data_path, max_samples, 0, seed, WINDOW_SIZE,
            allowed_session_ids=allowed_session_ids,
        )
        print(f"Selected {n_sessions_chosen} sessions, {len(samples)} samples (seed={seed})")
        k_suffix = f"_{eval_mode}{k_val}" if k_val > 1 else ""
        outfile_default = f"chatbot_n{len(samples)}_s{n_sessions_chosen}_seed{seed}{k_suffix}.json"
    else:
        print("Scanning classify_output for sessions with positive samples...")
        sessions = collect_sessions_with_positives(classify_path, allowed_session_ids)
        print(f"Found {len(sessions)} sessions with positive samples")
        if max_sessions == 0:
            samples = [s for session in sessions for s in session]
            n_sessions_chosen = len(sessions)
        else:
            samples = sample_from_sessions(sessions, max_sessions, seed)
            n_sessions_chosen = min(max_sessions, len(sessions))
        print(f"Selected {n_sessions_chosen} sessions, {len(samples)} positive-task samples (seed={seed})")
        k_suffix = f"_{eval_mode}{k_val}" if k_val > 1 else ""
        outfile_default = f"samples_s{n_sessions_chosen}_seed{seed}{k_suffix}.json"

    # Save under a model-specific output directory by default.
    model_tag = model.replace("/", "_").replace(":", "_")
    model_tag +="_chatbot"
    out_dir   = OUTPUT_DIR / model_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    if outfile is None:
        outfile = str(out_dir / outfile_default)

    import tqdm as _tqdm
    outfile_path = Path(outfile)
    results = []
    done_ids = set()
    truncated_ids = set()

    def _is_truncated(r):
        """Return True when a null prediction is caused by truncated raw JSON."""
        if r.get("agent_response") is not None:
            return False
        raw = r.get("raw_response") or ""
        if not raw or len(raw) < 50:
            return False
        try:
            text = raw.strip()
            if text.startswith("```json"):
                text = text.removeprefix("```json").strip()
            if text.endswith("```"):
                text = text.removesuffix("```").strip()
            json.loads(text)
            return False
        except (json.JSONDecodeError, ValueError):
            return True

    # ── Resume ──
    if outfile_path.exists():
        try:
            existing = json.loads(outfile_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                for r in existing:
                    sid = r.get("sample_id", "")
                    if _is_truncated(r):
                        truncated_ids.add(sid)
                    else:
                        done_ids.add(sid)
                results = [r for r in existing if r.get("sample_id", "") not in truncated_ids]
                print(f"Resume: {len(done_ids)} completed, {len(truncated_ids)} truncated samples to rerun")
        except Exception:
            pass

    remaining = [s for s in samples
                 if f"{s['session_id']}_op{s['trigger_op_id']}" not in done_ids]
    if not remaining:
        print(f"All {len(samples)} samples are already complete.")
        return
    if done_ids or truncated_ids:
        print(f"Remaining: {len(remaining)}/{len(samples)} samples")

    pbar = _tqdm.tqdm(total=len(remaining), ncols=100, desc="inference")

    lock = asyncio.Lock()

    async def run_one(s):
        result = await process_sample(s, None, pass_k=pass_k, hit_k=hit_k)
        async with lock:
            results.append(result)
        pbar.update(1)
        return result

    if pass_k > 1:
        # pass@k
        batch_size = workers * 2
        for i in range(0, len(remaining), batch_size):
            batch = remaining[i: i + batch_size]
            await asyncio.gather(*[run_one(s) for s in batch])
            outfile_path.write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding="utf-8")
    else:
        # Single / hit@k: schedule all samples and update progress as each finishes.
        await asyncio.gather(*[run_one(s) for s in remaining])
        outfile_path.write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding="utf-8")

    pbar.close()

    proposed = sum(1 for r in results if r["agent_response"] is not None)
    print(f"\nDone: {len(results)} samples, {proposed} with proposals. Saved to {outfile}")


if __name__ == "__main__":
    import fire
    fire.Fire(lambda **kw: asyncio.run(main(**kw)))
