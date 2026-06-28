#!/usr/bin/env python3
"""Quick test to verify the activerm server is running and responding correctly."""

import json
import requests
import sys

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8002"

# 1. Health check
print(f"[1] Checking server at {BASE_URL} ...")
try:
    r = requests.get(f"{BASE_URL}/v1/models", timeout=5)
    r.raise_for_status()
    models = r.json()
    print(f"    Available models: {[m['id'] for m in models['data']]}")
except Exception as e:
    print(f"    FAILED: {e}")
    print("    Is the server running? Start it with: bash start_reward_model_server.sh")
    sys.exit(1)

# 2. Test inference with a sample reward model query
print("[2] Testing chat completion ...")

SYSTEM = """<Task>
You are a proactive coding assistant that monitors IDE activity. Given observations of user behavior, evaluate whether a proposed proactive task is appropriate.
</Task>

<Format>
You should answer with following JSON format:
{
    "thought": "Your reasoning about whether the task is appropriate.",
    "judgement": "accepted or rejected"
}
</Format>"""

test_message = {
    "Observations (Time Ascending)": [
        {"time": "01-15 10:30:00", "event": "User opened file main.py and started editing function parse_config()"},
        {"time": "01-15 10:32:00", "event": "User added try-except block around JSON parsing code"},
    ],
    "Proposed Task": "Add input validation for the config file path before parsing",
    "Instruction": "Now give your judgement. You should complete the reasoning process in first person."
}

payload = {
    "model": "activerm",
    "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(test_message, ensure_ascii=False)}
    ],
    "temperature": 0.0,
    "max_tokens": 512,
}

try:
    r = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=30)
    r.raise_for_status()
    result = r.json()
    content = result["choices"][0]["message"]["content"]
    print(f"    Raw response: {content[:500]}")

    # Try to parse as JSON
    try:
        parsed = json.loads(content)
        print(f"    Thought:   {parsed.get('thought', 'N/A')[:200]}")
        print(f"    Judgement: {parsed.get('judgement', 'N/A')}")
        print("\n    SUCCESS - RM server is working correctly!")
    except json.JSONDecodeError:
        print(f"    WARNING: Response is not valid JSON, but server is responding.")
        print("    This may be fine - the model might need prompt tuning.")
except Exception as e:
    print(f"    FAILED: {e}")
    sys.exit(1)
