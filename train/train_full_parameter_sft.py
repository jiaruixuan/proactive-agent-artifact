#!/usr/bin/env python3
"""
Full-parameter SFT training for Proactive Agent on Qwen3-8B.

Unlike train_sft.py (LoRA), this performs full fine-tuning of all model weights.
Requires DeepSpeed ZeRO-3 for memory efficiency on 4x A100 80GB.

Usage:
  # Real data
  torchrun --nproc_per_node=4 train/train_full_parameter_sft.py \
      --data-source real \
      --model-path models \
      --hf-dataset hf_dataset \
      --output-dir models \
      --deepspeed train/deepspeed_zero3_config.json

  # Synthetic data
  torchrun --nproc_per_node=4 train/train_full_parameter_sft.py \
      --data-source synthetic \
      --model-path models \
      --hf-dataset hf_dataset \
      --output-dir models \
      --deepspeed train/deepspeed_zero3_config.json
"""

import json
import random
import sys
from pathlib import Path

import fire
from datasets import Dataset, load_dataset
from transformers import AutoTokenizer
from trl import SFTConfig, SFTTrainer

# ===== Paths =====
REPO_ROOT = Path(__file__).parent.parent
SYNTHETIC_DIR = REPO_ROOT / "synthetic_data" / "scenarios" / "synthetic_classified"
REAL_DIR = REPO_ROOT / "classify_output"
DEFAULT_MODEL = "models"

# Import shared prompt from eval (single source of truth)
sys.path.insert(0, str(REPO_ROOT / "eval"))
from run_chatbot_eval import SYSTEM
from proactive_agent.data_utils import op_to_observation

WINDOW_SIZE = 50


# ===== Data loading (local) =====

def _load_split_session_ids(split_file: str, split: str) -> set:
    data = json.loads(Path(split_file).read_text(encoding="utf-8"))
    entries = data.get(split, {}).get("data", [])
    return {e["session_id"].replace("session_", "").split("-")[0] for e in entries}


def _build_ops_text(ops: list, trigger_op_idx: int) -> str:
    window = ops[max(0, trigger_op_idx - WINDOW_SIZE + 1): trigger_op_idx + 1]
    return json.dumps([op_to_observation(o) for o in window], ensure_ascii=False, indent=2)


def load_samples_local(classify_dir: Path, excluded_session_ids: set = None) -> list:
    """Load all positive samples from a local classify directory."""
    samples = []
    for f in sorted(classify_dir.rglob("*_classified.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        if excluded_session_ids is not None:
            short_id = data.get("session_id", "").replace("session_", "").split("-")[0]
            if short_id in excluded_session_ids:
                continue

        ops = data.get("operations", [])
        op_index = {op["id"]: i for i, op in enumerate(ops) if "id" in op}

        for pos in data.get("positive", []):
            if pos.get("summary", "").lower().startswith("even split"):
                continue
            tid = pos.get("trigger_after_op_id")
            if tid is None or tid not in op_index:
                continue
            summary = pos.get("summary", "").strip()
            detail = pos.get("detail", "").strip()
            if not summary:
                continue
            samples.append({
                "ops_text": _build_ops_text(ops, op_index[tid]),
                "summary": summary,
                "detail": detail,
            })
    return samples


def _load_samples_for_sessions(classify_dir: Path, session_ids: set) -> list:
    """Load samples belonging to specific session IDs (local)."""
    samples = []
    for f in sorted(classify_dir.rglob("*_classified.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        short_id = data.get("session_id", "").replace("session_", "").split("-")[0]
        if short_id not in session_ids:
            continue
        ops = data.get("operations", [])
        op_index = {op["id"]: i for i, op in enumerate(ops) if "id" in op}
        for pos in data.get("positive", []):
            if pos.get("summary", "").lower().startswith("even split"):
                continue
            tid = pos.get("trigger_after_op_id")
            if tid is None or tid not in op_index:
                continue
            summary = pos.get("summary", "").strip()
            detail = pos.get("detail", "").strip()
            if not summary:
                continue
            samples.append({
                "ops_text": _build_ops_text(ops, op_index[tid]),
                "summary": summary,
                "detail": detail,
            })
    return samples


# ===== Data loading (HuggingFace) =====

def load_samples_hf(hf_dataset: str, split_name: str) -> list:
    """Load samples from HF dataset JSONL file."""
    file_map = {
        "synthetic": "train_synthetic.jsonl",
        "real": "train_real.jsonl",
        "val": "val.jsonl",
        "test": "test.jsonl",
    }
    filename = file_map.get(split_name)
    if filename is None:
        raise ValueError(f"Unknown split_name: {split_name}")

    ds = load_dataset(hf_dataset, data_files=filename, split="train")
    samples = []
    for row in ds:
        ops_text = json.dumps(row["window_ops"], ensure_ascii=False, indent=2)
        samples.append({
            "ops_text": ops_text,
            "summary": row["task"],
            "detail": row["workflow"],
        })
    return samples


def load_samples_release(release_data_dir: str, split_name: str) -> list:
    """Load samples from the public release dataset layout."""
    from proactive_agent.release_data import load_release_samples

    samples = []
    for row in load_release_samples(release_data_dir, split=split_name):
        samples.append({
            "ops_text": json.dumps(row["ops_window"], ensure_ascii=False, indent=2),
            "summary": row["ground_truth"]["task"],
            "detail": row["ground_truth"]["workflow"],
        })
    return samples


# ===== Dataset formatting =====

def build_prompt_completion(sample: dict, tokenizer=None, max_seq_length: int = 0) -> dict:
    """Build a prompt-completion pair for SFTTrainer.

    prompt: [system, user] messages
    completion: [assistant] message

    If max_seq_length > 0 and tokenizer is provided, truncates operations
    from the head (earliest) to fit within max_seq_length.
    """
    ops = json.loads(sample["ops_text"])

    completion = [{"role": "assistant", "content": json.dumps({
        "Proactive Task": {
            "task": f"<task>{sample['summary']}</task>",
            "workflow": f"<workflow>{sample['detail']}</workflow>",
        },
    }, ensure_ascii=False)}]

    def _build_prompt(operations):
        user_content = json.dumps({
            "Instructions": "Analyze the following recent IDE operations and provide a proactive task if the user needs help. The last operation is the most recent one.",
            "Observation": operations,
        }, ensure_ascii=False)
        return [
            {"role": "system", "content": SYSTEM},
            {"role": "user",   "content": user_content},
        ]

    if max_seq_length > 0 and tokenizer is not None:
        # Calculate fixed overhead (system + completion + template)
        fixed_prompt = _build_prompt([])
        fixed_msgs = fixed_prompt + completion
        fixed_text = tokenizer.apply_chat_template(fixed_msgs, tokenize=False)
        fixed_len = len(tokenizer.encode(fixed_text))
        ops_budget = max_seq_length - fixed_len

        # Tokenize each op individually for precise truncation
        op_tokens = [len(tokenizer.encode(json.dumps(op, ensure_ascii=False))) for op in ops]
        total_ops_tokens = sum(op_tokens)

        if total_ops_tokens > ops_budget and len(ops) > 1:
            # Remove ops from head until it fits
            cumsum = 0
            cut_idx = 0
            for i, t in enumerate(op_tokens):
                cumsum += t
                if total_ops_tokens - cumsum <= ops_budget:
                    cut_idx = i + 1
                    break
            ops = ops[cut_idx:]

    return {"prompt": _build_prompt(ops), "completion": completion}


def build_hf_dataset(samples: list, tokenizer=None, max_seq_length: int = 0) -> Dataset:
    """Build dataset with prompt-completion format for SFTTrainer."""
    from tqdm import tqdm
    prompts = []
    completions = []
    for s in tqdm(samples, desc="Formatting samples", ncols=100):
        pc = build_prompt_completion(s, tokenizer, max_seq_length)
        prompts.append(pc["prompt"])
        completions.append(pc["completion"])
    return Dataset.from_dict({"prompt": prompts, "completion": completions})


# ===== Main =====

def main(
    data_source: str = "synthetic",
    model_path: str = DEFAULT_MODEL,
    output_dir: str = None,
    hf_dataset: str = None,
    release_data_dir: str = None,
    train_split: str = "train",
    split_file: str = None,
    val_split: str = "val",
    num_epochs: int = 3,
    per_device_batch_size: int = 1,
    gradient_accumulation_steps: int = 4,
    learning_rate: float = 2e-5,
    max_seq_length: int = 32768,
    seed: int = 42,
    deepspeed: str = "train/deepspeed_zero3_config.json",
    pre_truncate: bool = True,
):
    """Full-parameter SFT on proactive task data.

    Args:
        data_source: "synthetic" or "real"
        model_path:  Path to base model
        output_dir:  Checkpoint output directory
        hf_dataset:  HuggingFace dataset path (local dir or HF hub ID)
        release_data_dir: Public release dataset directory
        train_split: Release dataset split name for training
        split_file:  Optional split JSON file for local validation filtering
        val_split:   Split name for validation ("val")
        num_epochs:  Training epochs
        per_device_batch_size: Batch size per GPU
        gradient_accumulation_steps: Gradient accumulation steps
        learning_rate: Peak LR (2e-5 recommended for full SFT)
        max_seq_length: Max token length per sample
        seed:        Random seed
        pre_truncate: Truncate long ops from head to fit max_seq_length (default: True)
        deepspeed:   Path to DeepSpeed config (ZeRO-3 required for full SFT)
    """
    assert data_source in ("synthetic", "real"), \
        "data_source must be 'synthetic' or 'real'"

    if output_dir is None:
        output_dir = str(REPO_ROOT / "train" / "outputs" / f"qwen3-8b-{data_source}-full")

    print(f"Data source : {data_source}")
    print(f"Model       : {model_path}")
    print(f"Output dir  : {output_dir}")
    if release_data_dir:
        data_mode = "release"
    elif hf_dataset:
        data_mode = "HuggingFace"
    else:
        data_mode = "local"
    print(f"Data mode   : {data_mode}")
    print(f"DeepSpeed   : {deepspeed}")
    print(f"Full SFT    : Yes (no LoRA)")

    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
    tokenizer.truncation_side = "left"  # If SFTTrainer truncates, drop earliest ops not completion
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load data
    if release_data_dir:
        print(f"Loading training data from release dataset: {release_data_dir} ({train_split})...")
        train_samples = load_samples_release(release_data_dir, train_split)
        try:
            print(f"Loading val data from release dataset: {release_data_dir} ({val_split})...")
            val_samples = load_samples_release(release_data_dir, val_split)
        except FileNotFoundError:
            print(f"No release validation split found: {val_split}")
            val_samples = []
    elif hf_dataset:
        print(f"Loading training data from: {hf_dataset} ({data_source})...")
        train_samples = load_samples_hf(hf_dataset, data_source)
        print(f"Loading val data from: {hf_dataset} (val)...")
        val_samples = load_samples_hf(hf_dataset, "val")
    else:
        if split_file:
            val_session_ids = _load_split_session_ids(split_file, val_split)
            print(f"Val sessions: {len(val_session_ids)}")
        else:
            val_session_ids = set()
            print("No split file provided; skipping local validation split.")

        if data_source == "synthetic":
            train_samples = load_samples_local(SYNTHETIC_DIR)
        else:
            train_samples = load_samples_local(REAL_DIR, excluded_session_ids=val_session_ids)

        val_samples = _load_samples_for_sessions(REAL_DIR, val_session_ids) if val_session_ids else []

    random.seed(seed)
    random.shuffle(train_samples)

    print(f"Train samples: {len(train_samples)}")
    print(f"Val samples  : {len(val_samples)}")
    print(f"Pre-truncate : {pre_truncate} (max_seq_length={max_seq_length})")

    trunc_len = max_seq_length if pre_truncate else 0
    train_dataset = build_hf_dataset(train_samples, tokenizer, trunc_len)
    val_dataset = build_hf_dataset(val_samples, tokenizer, trunc_len) if val_samples else None

    # Training config
    sft_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=num_epochs,
        per_device_train_batch_size=per_device_batch_size,
        per_device_eval_batch_size=per_device_batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        weight_decay=0.05,
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=10,
        eval_strategy="epoch" if val_dataset else "no",
        save_strategy="epoch",
        save_total_limit=3,
        load_best_model_at_end=bool(val_dataset),
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        packing=False,
        seed=seed,
        report_to="none",
        dataloader_num_workers=4,
        deepspeed=deepspeed,
        max_length=max_seq_length,
        completion_only_loss=True,
    )

    # Load model with Flash Attention 2
    from transformers import AutoModelForCausalLM
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype="auto",
        attn_implementation="flash_attention_2",
        trust_remote_code=True,
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(output_dir)
    print(f"Model saved to {output_dir}")


if __name__ == "__main__":
    fire.Fire(main)
