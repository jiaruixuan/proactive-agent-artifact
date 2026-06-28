#!/usr/bin/env python3
"""
Evaluate a trained LoRA model on the test set.

Loads the base model + LoRA adapter locally, runs inference on test samples,
and saves results in the same format as run_chatbot_eval.py for judge compatibility.

Usage:
  # Single GPU
  python eval/run_trained_model_eval.py --checkpoint train/outputs/qwen3-8b-real/checkpoint-234

  # Specify base model
  python eval/run_trained_model_eval.py \
      --checkpoint train/outputs/qwen3-8b-real/checkpoint-234 \
      --base-model models

  # With optional split filter for local classified data
  python eval/run_trained_model_eval.py \
      --checkpoint train/outputs/qwen3-8b-real/checkpoint-234 \
      --split-file path/to/splits.json --split test
"""

import json
import os
import re
import sys
from pathlib import Path

import fire
import torch
from tqdm import tqdm
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

# ===== Paths =====
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "eval"))

from run_chatbot_eval import (
    SYSTEM, SYSTEM_HIT_K, USER_TMPL,
    collect_sessions_with_positives, sample_from_sessions,
    op_to_observation, extract_pred, extract_pred_multi,
)

CLASSIFY_DIR = REPO_ROOT / "classify_output"
OUTPUT_DIR = Path(__file__).parent / "predictions"
WINDOW_SIZE = 50


def load_model(base_model: str, checkpoint: str, device: str = "cuda"):
    """Load base model + LoRA adapter."""
    print(f"Loading tokenizer from {base_model}...")
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model from {base_model}...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.bfloat16,
        device_map=device,
        trust_remote_code=True,
    )

    print(f"Loading LoRA adapter from {checkpoint}...")
    model = PeftModel.from_pretrained(model, checkpoint)
    model.eval()

    return model, tokenizer


def generate_response(model, tokenizer, messages, temperature=0.0, max_new_tokens=2048):
    """Generate a response from the model."""
    text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = tokenizer(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        if temperature <= 0:
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        else:
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                pad_token_id=tokenizer.pad_token_id,
            )

    # Decode only the generated tokens (exclude input)
    generated = outputs[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(generated, skip_special_tokens=True)


def process_sample(model, tokenizer, item, pass_k=1, hit_k=1):
    """Process a single sample, same logic as run_chatbot_eval.py."""
    if "ops_window" in item:
        # Pre-built window from JSONL (already observation format)
        obs_list = item["ops_window"]
    else:
        # Raw ops from classify_output
        ops = item["ops"]
        idx = item["trigger_op_idx"]
        window = ops[max(0, idx - WINDOW_SIZE + 1): idx + 1]
        obs_list = [op_to_observation(o) for o in window]
    user_content = USER_TMPL.replace("[placeholder]", json.dumps(obs_list))

    agent_responses = []
    raw_responses = []

    try:
        if hit_k > 1:
            system = SYSTEM_HIT_K.format(k=hit_k)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ]
            raw = generate_response(model, tokenizer, messages, temperature=0.0)
            raw_responses = [raw]
            agent_responses = extract_pred_multi(raw)

        elif pass_k > 1:
            for _ in range(pass_k):
                try:
                    messages = [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_content},
                    ]
                    raw = generate_response(model, tokenizer, messages, temperature=0.7)
                    raw_responses.append(raw)
                    agent_responses.append(extract_pred(raw))
                except Exception as e:
                    print(f"  [{item['session_id']}] pass@k error: {e}")
                    agent_responses.append(None)
        else:
            messages = [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_content},
            ]
            raw = generate_response(model, tokenizer, messages, temperature=0.0)
            raw_responses = [raw]
            agent_responses = [extract_pred(raw)]

    except Exception as e:
        print(f"  [{item['session_id']}] op {item['trigger_op_id']} error: {e}")
        if not agent_responses:
            agent_responses = [None]

    agent_response = next((r for r in agent_responses if r is not None), None)

    result = {
        "sample_id": f"{item['session_id']}_op{item['trigger_op_id']}",
        "session_id": item["session_id"],
        "trigger_op_id": item["trigger_op_id"],
        "model": "trained_lora",
        "ground_truth": item["ground_truth"],
        "window_size": len(obs_list),
        "agent_response": agent_response,
        "raw_response": raw_responses[0] if raw_responses else None,
    }

    if pass_k > 1 or hit_k > 1:
        result["agent_responses"] = agent_responses
        result["raw_responses"] = raw_responses
        result["eval_mode"] = "hit_k" if hit_k > 1 else "pass_k"
        result["k"] = hit_k if hit_k > 1 else pass_k

    return result


def load_samples_from_jsonl(jsonl_path: str) -> list:
    """Load test samples from HF dataset JSONL file."""
    samples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            samples.append({
                "session_id": row["session_id"],
                "trigger_op_id": row["trigger_op_id"],
                "trigger_op_idx": len(row["window_ops"]) - 1,  # last op in window
                "ground_truth": {
                    "task": row["task"],
                    "workflow": row["workflow"],
                },
                "ops_window": row["window_ops"],  # pre-built window
            })
    return samples


def main(
    checkpoint: str,
    base_model: str = None,
    max_sessions: int = 0,
    seed: int = 0,
    classify_dir: str = None,
    hf_data: str = None,
    release_data_dir: str = None,
    outfile: str = None,
    pass_k: int = 1,
    hit_k: int = 1,
    split_file: str = None,
    split: str = "test",
    device: str = "cuda",
):
    """Evaluate trained LoRA model on test set.

    Args:
        checkpoint:  Path to LoRA checkpoint directory
        base_model:  Path to base model (auto-detected from checkpoint if not set)
        max_sessions: 0 = all sessions
        seed:        Random seed for sampling
        classify_dir: Path to classify_output/ (local mode)
        hf_data:     Path to HF dataset dir or JSONL file (e.g. hf_dataset/test.jsonl)
        release_data_dir: Path to public release dataset directory
        outfile:     Output file path
        pass_k:      pass@k independent calls
        hit_k:       hit@k candidates per call
        split_file:  Optional split JSON file for classify_dir mode
        split:       Which split (test/val)
        device:      cuda or cpu
    """
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.exists():
        print(f"Error: checkpoint not found: {checkpoint}")
        return

    # Auto-detect base model from adapter_config.json
    if base_model is None:
        adapter_config = checkpoint_path / "adapter_config.json"
        if adapter_config.exists():
            cfg = json.loads(adapter_config.read_text())
            base_model = cfg.get("base_model_name_or_path", "")
        if not base_model:
            print("Error: cannot detect base model. Use --base-model to specify.")
            return
    print(f"Base model  : {base_model}")
    print(f"Checkpoint  : {checkpoint}")

    # Load model
    model, tokenizer = load_model(base_model, checkpoint, device)

    # Collect samples
    if release_data_dir:
        from proactive_agent.release_data import load_release_samples

        print(f"Loading release data from {release_data_dir}...")
        samples = load_release_samples(release_data_dir, split=split)
        n_sessions = len(set(s["session_id"] for s in samples))
        print(f"Loaded {len(samples)} release samples from {n_sessions} sessions")
    elif hf_data:
        # ── HuggingFace JSONL mode ──
        hf_path = Path(hf_data)
        if hf_path.is_dir():
            # Auto-detect: hf_dataset/test.jsonl or hf_dataset/val.jsonl
            jsonl_file = hf_path / f"{split}.jsonl"
            if not jsonl_file.exists():
                print(f"Error: {jsonl_file} not found")
                return
        else:
            jsonl_file = hf_path
        print(f"Loading samples from {jsonl_file}...")
        samples = load_samples_from_jsonl(str(jsonl_file))
        n_sessions = len(set(s["session_id"] for s in samples))
        print(f"Loaded {len(samples)} samples from {n_sessions} sessions")
    else:
        # ── Local classify_output mode ──
        classify_path = Path(classify_dir) if classify_dir else CLASSIFY_DIR

        allowed_session_ids = None
        if split_file:
            _split_data = json.loads(Path(split_file).read_text(encoding="utf-8"))
            _entries = _split_data.get(split, {}).get("data", [])
            allowed_session_ids = {e["session_id"].replace("session_", "") for e in _entries}
            print(f"Using split '{split}': {len(allowed_session_ids)} sessions")

        sessions = collect_sessions_with_positives(classify_path, allowed_session_ids)
        print(f"Found {len(sessions)} sessions with positives")

        if max_sessions == 0:
            samples = [s for session in sessions for s in session]
        else:
            samples = sample_from_sessions(sessions, max_sessions, seed)
        n_sessions = len(sessions) if max_sessions == 0 else min(max_sessions, len(sessions))
        print(f"Selected {n_sessions} sessions, {len(samples)} samples")

    # Output path
    ckpt_name = checkpoint_path.name
    model_tag = f"trained_{ckpt_name}_chatbot"
    out_dir = OUTPUT_DIR / model_tag
    out_dir.mkdir(parents=True, exist_ok=True)

    eval_mode = "pass_k" if pass_k > 1 else ("hit_k" if hit_k > 1 else "single")
    k_val = max(pass_k, hit_k)
    k_suffix = f"_{eval_mode}{k_val}" if k_val > 1 else ""

    if outfile is None:
        outfile = str(out_dir / f"samples_s{n_sessions}_seed{seed}{k_suffix}.json")

    outfile_path = Path(outfile)

    # Resume
    results = []
    done_ids = set()
    if outfile_path.exists():
        try:
            existing = json.loads(outfile_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                results = existing
                done_ids = {r["sample_id"] for r in results if r.get("agent_response") is not None}
                print(f"Resume: {len(done_ids)} completed samples")
        except Exception:
            pass

    remaining = [s for s in samples if f"{s['session_id']}_op{s['trigger_op_id']}" not in done_ids]
    if not remaining:
        print(f"All {len(samples)} samples completed.")
        return
    if done_ids:
        print(f"Remaining: {len(remaining)}/{len(samples)}")

    # Run inference
    for item in tqdm(remaining, desc="Evaluating", ncols=100):
        result = process_sample(model, tokenizer, item, pass_k=pass_k, hit_k=hit_k)
        results.append(result)

        # Incremental save every 10 samples
        if len(results) % 10 == 0:
            outfile_path.write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding="utf-8")

    # Final save
    outfile_path.write_text(json.dumps(results, ensure_ascii=False, indent=4), encoding="utf-8")

    proposed = sum(1 for r in results if r["agent_response"] is not None)
    print(f"\nDone: {len(results)} samples, {proposed} proposals, saved to {outfile}")
    print(f"\nTo judge: python eval/judge_predictions.py --infile {outfile}")


if __name__ == "__main__":
    fire.Fire(main)
