#!/bin/bash
# Start the Reward Model (activerm) as an OpenAI-compatible API server using vLLM
# Usage: bash start_reward_model_server.sh [GPU_ID] [PORT]

GPU_ID=${1:-2}
PORT=${2:-8002}
MODEL_PATH="${MODEL_PATH:-models/LLaMA-3.1-8B-Active-RM}"
CONDA_ENV="${CONDA_ENV:-proactive}"

if [ ! -d "$MODEL_PATH" ]; then
    echo "ERROR: Model not found at $MODEL_PATH"
    echo "Please download it first:"
    echo "  huggingface-cli download anonymous/proactive-agent-rm --include 'LLaMA-3.1-8B-Active-RM/*' --local-dir $MODEL_PATH"
    exit 1
fi

echo "Starting activerm on GPU $GPU_ID, port $PORT ..."
echo "Model: $MODEL_PATH"

conda run --no-capture-output -n "$CONDA_ENV" \
    env CUDA_VISIBLE_DEVICES=$GPU_ID \
    python -m vllm.entrypoints.openai.api_server \
        --model "$MODEL_PATH" \
        --served-model-name activerm \
        --port "$PORT" \
        --max-model-len 32768 \
        --dtype auto \
        --gpu-memory-utilization 0.9
