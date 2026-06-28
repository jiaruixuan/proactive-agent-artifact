#!/bin/bash
# Step 3: Build agent training set using RM for filtering
# Prerequisites:
#   1. RM server running: bash start_reward_model_server.sh
#   2. private.toml configured with activerm and activeagent models

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

RM_PORT=${1:-8002}
RM_URL="http://localhost:$RM_PORT"

# Check RM server is up
echo "[Check] Testing RM server at $RM_URL ..."
if ! curl -s "$RM_URL/v1/models" > /dev/null 2>&1; then
    echo "ERROR: RM server not reachable at $RM_URL"
    echo "Start it first: bash start_reward_model_server.sh"
    exit 1
fi
echo "  RM server OK"

# Check private.toml has activerm config
if ! grep -q 'activerm' private.toml; then
    echo "ERROR: 'activerm' not found in private.toml"
    echo "Please add the activerm configuration (see README)"
    exit 1
fi
echo "  private.toml OK"

echo ""
echo "Starting agent training set build ..."
echo "  activeagent model: ${ACTIVEAGENT_MODEL:-activeagent (default)}"
echo "  activerm model:    ${ACTIVERM_MODEL:-activerm (default)}"
echo ""

unset ALL_PROXY all_proxy HTTPS_PROXY https_proxy HTTP_PROXY http_proxy
PYTHONPATH="$ROOT_DIR:$SCRIPT_DIR" python scenarios/build_agent_training_set.py

echo ""
echo "Agent training set build complete."
echo "Output: scenarios/agent_data/agent_trainset.json"
