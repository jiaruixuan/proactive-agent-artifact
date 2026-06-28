#!/usr/bin/env python3
"""
SFT training for Proactive Agent on Qwen3-8B.

Usage:
  # Local data - synthetic
  python train/train_sft.py --data-source synthetic

  # Local data - real
  python train/train_sft.py --data-source real

  # HuggingFace dataset
  python train/train_sft.py --data-source synthetic --hf-dataset <user>/proactive-agent-data

  # Multi-GPU
  torchrun --nproc_per_node=8 train/train_sft.py --data-source synthetic
"""

import json
import random
import sys
from pathlib import Path

import fire
from datasets import Dataset, load_dataset
from peft import LoraConfig
from transformers import AutoTokenizer
from trl import SFTConfig, SFTTrainer

# ===== Paths =====
REPO_ROOT = Path(__file__).parent.parent
SYNTHETIC_DIR = REPO_ROOT / "synthetic_data" / "scenarios" / "synthetic_classified"
REAL_DIR = REPO_ROOT / "classify_output"
DEFAULT_MODEL = "models"

# Import shared utilities and prompt from eval to stay in sync
sys.path.insert(0, str(REPO_ROOT / "eval"))
from proactive_agent.data_utils import op_to_observation
from run_chatbot_eval import SYSTEM  # single source of truth for the prompt

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
    """Load samples from a HuggingFace dataset JSONL file.

    split_name maps to file: synthetic -> train_synthetic, real -> train_real, val -> val
    """
    file_map = {
        "synthetic": "train_synthetic.jsonl",
        "real": "train_real.jsonl",
        "val": "val.jsonl",
        "test": "test.jsonl",
    }
    filename = file_map.get(split_name)
    if filename is None:
        raise ValueError(f"Unknown split_name: {split_name}, expected one of {list(file_map.keys())}")

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

def format_as_conversation(sample: dict, tokenizer) -> str:
    """Format a sample as a tokenizer chat template string."""
    user_msg = json.dumps({
        "Instructions": "Analyze the following recent IDE operations and provide a proactive task if the user needs help. The last operation is the most recent one.",
        "Observation": json.loads(sample["ops_text"]),
    }, ensure_ascii=False)

    assistant_msg = json.dumps({
        "Proactive Task": {
            "task": sample["summary"],
            "workflow": sample["detail"],
        },
    }, ensure_ascii=False)

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_msg},
        {"role": "assistant", "content": assistant_msg},
    ]
    # enable_thinking=False: disable Qwen3 thinking mode during SFT
    return tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False,
        enable_thinking=False,
    )


def build_hf_dataset(samples: list, tokenizer) -> Dataset:
    texts = [format_as_conversation(s, tokenizer) for s in samples]
    return Dataset.from_dict({"text": texts})


# ===== Main =====

def main(
    data_source: str = "synthetic",        # "synthetic" or "real"
    model_path: str = DEFAULT_MODEL,
    output_dir: str = None,
    hf_dataset: str = None,                # HuggingFace dataset ID, e.g. "user/proactive-agent-data"
    release_data_dir: str = None,          # Public release dataset directory
    train_split: str = "train",
    split_file: str = None,
    val_split: str = "val",
    num_epochs: int = 3,
    per_device_batch_size: int = 2,
    gradient_accumulation_steps: int = 4,
    learning_rate: float = 2e-4,
    max_seq_length: int = 8192,
    lora_rank: int = 64,
    lora_alpha: int = 128,
    lora_dropout: float = 0.05,
    seed: int = 42,
):
    """Train Qwen3-8B with SFT on proactive task data.

    Args:
        data_source: "synthetic" or "real"
        model_path:  Path to base model
        output_dir:  Checkpoint output directory
        hf_dataset:  HuggingFace dataset ID (if set, loads from HF instead of local)
        release_data_dir: Public release dataset directory
        train_split: Release dataset split name for training
        split_file:  Optional split JSON file for local validation filtering
        val_split:   Split name for validation ("val")
        num_epochs:  Training epochs
        per_device_batch_size: Batch size per GPU
        gradient_accumulation_steps: Gradient accumulation
        learning_rate: Peak LR
        max_seq_length: Max token length per sample
        lora_rank:   LoRA rank
        lora_alpha:  LoRA alpha
        lora_dropout: LoRA dropout
        seed:        Random seed
    """
    assert data_source in ("synthetic", "real"), \
        "data_source must be 'synthetic' or 'real'"

    if output_dir is None:
        output_dir = str(REPO_ROOT / "train" / "outputs" / f"qwen3-8b-{data_source}")

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

    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
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
        # ── HuggingFace mode ──
        print(f"Loading training data from HF: {hf_dataset} ({data_source})...")
        train_samples = load_samples_hf(hf_dataset, data_source)
        print(f"Loading val data from HF: {hf_dataset} (val)...")
        val_samples = load_samples_hf(hf_dataset, "val")
    else:
        # Local mode
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

    train_dataset = build_hf_dataset(train_samples, tokenizer)
    val_dataset   = build_hf_dataset(val_samples,   tokenizer) if val_samples else None

    # LoRA config
    peft_config = LoraConfig(
        r=lora_rank,
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )

    # Training config
    sft_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=num_epochs,
        per_device_train_batch_size=per_device_batch_size,
        per_device_eval_batch_size=per_device_batch_size,
        gradient_accumulation_steps=gradient_accumulation_steps,
        learning_rate=learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=10,
        eval_strategy="epoch" if val_dataset else "no",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=False,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        dataset_text_field="text",
        packing=False,
        seed=seed,
        report_to="none",
        dataloader_num_workers=4,
    )

    # max_length in SFTConfig (trl 0.29.0)
    sft_config.max_length = max_seq_length

    trainer = SFTTrainer(
        model=model_path,
        args=sft_config,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        peft_config=peft_config,
        processing_class=tokenizer,
    )

    trainer.train()
    trainer.save_model(output_dir)
    print(f"Model saved to {output_dir}")


if __name__ == "__main__":
    fire.Fire(main)
