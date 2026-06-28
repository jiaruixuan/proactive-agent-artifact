# Data Processing Pipeline

This directory contains the real-data preprocessing pipeline for Proactive Agent.
The pipeline converts raw IDE operation logs into task-level training and evaluation examples.

## Inputs and Outputs

Expected inputs:

- `raw_operations/`: raw session JSON files.
- `data/`: repository snapshots for agent evaluation.

Generated outputs:

- `cleaned_data/`: cleaned operation streams.
- `aggregate_results/`: coarse task intervals.
- `refine_results/`: refined task intervals.
- `classify_output/`: final positive/negative examples.

All generated directories are git-ignored.

## Stages

### Stage 0: Merge Operations

```bash
python3 data_processing/clean_operation_batches.py
```

This stage normalizes raw edit/cursor/event records and writes cleaned sessions.

### Stage 1: Aggregate Tasks

```bash
python3 data_processing/aggregate_task_windows.py \
  --input_root cleaned_data \
  --output_root aggregate_results \
  --call-llm \
  --api_key "$OPENAI_API_KEY" \
  --endpoint "$OPENAI_API_BASE"
```

This stage slides a fixed-size window over operations and asks an LLM to propose non-overlapping task intervals.

### Stage 2: Refine Tasks

```bash
python3 data_processing/refine_tasks_parallel.py
```

This stage filters coarse intervals, merges adjacent related intervals, counts actual operations, and splits oversized tasks.

### Stage 3: Classify Tasks

```bash
python3 data_processing/classify_proactive_tasks.py \
  --refined-dir refine_results \
  --cleaned-dir cleaned_data \
  --output-dir classify_output \
  --call-llm
```

This stage labels tasks as proactive positives when they are predictable, valuable, and actionable from prior context.

## Export

```bash
python3 data_processing/export_huggingface_dataset.py \
  --synthetic-dir synthetic_data/scenarios/synthetic_classified \
  --real-dir classify_output \
  --output-dir hf_dataset
```

The export script writes JSONL files compatible with Hugging Face datasets. Pass `--split-file path/to/splits.json` only when you want to export held-out test/validation splits from local classified data.

## Privacy

Do not commit raw sessions, repository snapshots, traces, generated labels, private API configs, or model checkpoints. Keep all local data under ignored directories or pass paths explicitly with CLI flags.
