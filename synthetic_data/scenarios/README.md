# Dataset Utilities

This directory contains anonymized seed scenes and helper scripts for synthetic
data generation. It does not include private user data.

## Files

- `seed_tasks.yaml`: small anonymous seed tasks for scene generation.
- `base_scene_configs.yaml`, `generated_scene_configs.yaml`, `reviewed_scene_configs.yaml`, `selected_scene_configs.yaml`: compact example scene configs.
- `build_scene_configs.py`: generate scene YAML files from seed tasks.
- `run_scene_batch.py`: run the simulator for generated scenes.
- `build_agent_training_set.py`: build agent SFT data from generated events.
- `build_reward_training_set.py`: build reward-model data from annotated examples.

## Generate Scenes

Run from the repository root:

```bash
python synthetic_data/scenarios/build_scene_configs.py \
  --seedfile synthetic_data/scenarios/seed_tasks.yaml \
  --savefile synthetic_data/scenarios/generated_scene_configs.yaml
```

Generated scene configs are written under `synthetic_data/scenarios/agent_data/`.

## Generate Events

```bash
python synthetic_data/scenarios/run_scene_batch.py
```

## Build Training Data

```bash
python synthetic_data/scenarios/build_agent_training_set.py
```

For reward-model data, configure the OpenAI-compatible endpoint with
environment variables:

```bash
export OPENAI_API_KEY=<your-api-key>
export OPENAI_API_BASE=http://localhost:8000/v1/
export REWARD_MODEL=<served-model-name>
python synthetic_data/scenarios/build_reward_training_set.py
```

Keep annotation outputs, generated JSONL files, and model artifacts out of git.
