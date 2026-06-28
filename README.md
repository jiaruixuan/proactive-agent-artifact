# Proactive Agent

Proactive Agent is a research codebase for building and evaluating proactive IDE assistants. Given a recent window of IDE operations, the system predicts the next useful task and evaluates whether the prediction matches a held-out human task.

## Repository Layout

- `eval/`: chatbot inference, judging, pass@k, hit@k, and data-loading utilities.
- `eval/proactive_agent/`: shared release-data and sample-loading utilities.
- `data_processing/`: preprocessing pipeline for raw IDE operations.
- `synthetic_data/`: synthetic scenario generation and simulator utilities.
- `train/`: supervised fine-tuning scripts.
- `user_data_collection_code/`: anonymized reference code for collecting IDE,
  chat, agent, and inline-completion interaction data.
- `test_nonsensitive_10.jsonl`: small non-sensitive public sample set.

Generated data, private configs, traces, model outputs, and local datasets are ignored by git. Place private data under `data/`, `classify_output/`, `cleaned_data/`, `aggregate_results/`, `refine_results/`, or another local path and pass it with CLI flags.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Configure API access with environment variables instead of committing credentials:

```bash
export OPENAI_API_KEY=<your-api-key>
export OPENAI_API_BASE=<your-openai-compatible-endpoint>
```

## Evaluation

Chatbot-style inference:

```bash
cd eval
python3 run_chatbot_eval.py --model openai/deepseek-v3.2 --max-sessions 3 --seed 0
```

Judge predictions:

```bash
cd eval
python3 judge_predictions.py \
  --infile predictions/<model>/samples_s3_seed0.json \
  --outfile judged_predictions/<model>/samples_s3_seed0.json
```

Public release dataset evaluation:

```bash
python3 eval/run_evaluation.py \
  --mode chatbot \
  --model deepseek-v3.2 \
  --release-data-dir . \
  --split test
```

The anonymous GitHub release includes only `test_nonsensitive_10.jsonl`, a
small Hugging Face-compatible JSONL sample set for smoke tests and code usage
examples. The full test set is not included in this repository.

Each row contains a recent IDE operation window (`window_ops`) and the
corresponding task label (`task`). The current public release does not include
agentic repository-browsing evaluation code or large context repositories.
Repository-aware evaluation requires adding your own context repositories and
agent harness.

Find sessions that have both labels and repository data:

```bash
python3 eval/find_valid_sessions.py --classify-dir classify_output --data-dir data --verbose
```

## Data Processing

The real-data pipeline converts raw IDE operation logs into classified positive/negative samples:

```bash
python3 data_processing/run_preprocessing_pipeline.py \
  --data-dir raw_operations \
  --output-dir pipeline_output \
  --api-key "$OPENAI_API_KEY" \
  --endpoint "$OPENAI_API_BASE" \
  --call-llm
```

The main stages are:

1. Merge noisy operation streams into cleaned sessions.
2. Aggregate operations into coarse task intervals.
3. Refine intervals by filtering, merging, and splitting tasks.
4. Classify refined tasks into proactive positives and negatives.

See `data_processing/README.md` for details.

## Synthetic Data

Generate scenes from classified sessions:

```bash
python3 synthetic_data/convert_sessions_to_scenarios.py \
  --classify-dir classify_output \
  --output-dir synthetic_data/scenarios/agent_data \
  --min-positives 1 \
  --resume
```

Run simulator traces:

```bash
python3 synthetic_data/run_simulation.py \
  --scene-dir synthetic_data/scenarios/agent_data \
  --workers 8
```

Build synthetic training data:

```bash
python3 synthetic_data/build_synthetic_training_data.py \
  --scene-dir synthetic_data/scenarios/agent_data \
  --output-dir synthetic_data/scenarios/synthetic_classified \
  --call-llm
```

## Training

```bash
python3 train/train_sft.py --help
python3 train/train_full_parameter_sft.py --help
```

Use local paths or Hugging Face dataset IDs through CLI arguments. Do not commit model checkpoints, API keys, or private datasets.

## Release Notes

This public version contains anonymized example metadata and synthetic examples only. Replace the placeholder datasets with your own data before running full experiments.

The `user_data_collection_code/` directory is a reference implementation of the
data-collection layer. Its chat, agent, autocomplete, authentication, telemetry,
and upload hooks are host-application adapters; reuse requires replacing those
adapters with the interfaces and backend endpoints of your own IDE assistant.
