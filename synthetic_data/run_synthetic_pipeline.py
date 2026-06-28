#!/usr/bin/env python3
"""
Unified synthetic data generation pipeline with three modes.

Mode 1 (restricted): Only change_view + edit_text, strict XML format templates, no few-shot
Mode 2 (fewshot):    No type restriction, simulator pipeline with few-shot examples in scene
Mode 3 (hierarchical): Task-first generation, all positive samples, with few-shot

Usage:
  python3 synthetic_data/run_synthetic_pipeline.py --mode restricted --classify-dir classify_output --output-dir synthetic_data/scenarios/synthetic_restricted
  python3 synthetic_data/run_synthetic_pipeline.py --mode fewshot --classify-dir classify_output --output-dir synthetic_data/scenarios/synthetic_fewshot
  python3 synthetic_data/run_synthetic_pipeline.py --mode hierarchical --classify-dir classify_output --output-dir synthetic_data/scenarios/synthetic_hierarchical
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from tqdm import tqdm

# Make imports work
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "data_processing"))
sys.path.insert(0, str(REPO_ROOT / "synthetic_data"))

from convert_sessions_to_scenarios import (
    call_llm, parse_json_response, extract_op_types,
    extract_files_touched, load_classified_session,
)
from build_synthetic_training_data import (
    step_aggregate, step_refine, step_classify,
)

# ===== API Config =====
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"


# =============================================================================
# Shared utilities
# =============================================================================

def extract_scene_description(session_data: Dict) -> Dict:
    """Extract scene signals from a real classified session."""
    metadata = session_data.get('metadata', {})
    operations = session_data.get('operations', [])
    project_name = metadata.get('project_name', 'unknown project')
    op_types = extract_op_types(operations)
    files_touched = extract_files_touched(operations)

    timestamps = [op.get('timestamp', 0) for op in operations if op.get('timestamp')]
    duration_min = 0
    if len(timestamps) >= 2:
        try:
            first, last = min(timestamps), max(timestamps)
            if isinstance(first, (int, float)):
                duration_min = round((last - first) / 60000, 1)
            else:
                from datetime import datetime
                fmt = "%m-%d %H:%M:%S.%f" if '.' in str(first) else "%m-%d %H:%M:%S"
                t0 = datetime.strptime(str(first), fmt)
                t1 = datetime.strptime(str(last), fmt)
                duration_min = round((t1 - t0).total_seconds() / 60, 1)
        except Exception:
            duration_min = 0

    return {
        'project_name': project_name,
        'op_types': op_types,
        'files_touched': files_touched,
        'duration_min': duration_min,
    }


def parse_generated_ops(llm_response: str) -> List[Dict]:
    """Parse LLM-generated operations JSON into pipeline-compatible format."""
    # Try to extract JSON array from response
    parsed = parse_json_response(llm_response)
    if parsed is None:
        # Try finding JSON array directly
        m = re.search(r'\[.*\]', llm_response, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(parsed, list):
        return []

    ops = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        event = item.get('event', '')
        # Detect operation type from event content
        if '[change_view]' in event:
            op_type = 'change_view'
        elif '[edit_text]' in event:
            op_type = 'edit_text'
        elif '[cursor_selection]' in event:
            op_type = 'cursor_selection'
        elif '[clipboard_change]' in event:
            op_type = 'clipboard_change'
        else:
            op_type = 'edit_text'  # default

        ops.append({
            'id': i,
            'operationType': op_type,
            'timestamp': item.get('time', ''),
            'readableDetails': event,
        })

    return ops


def save_classified_output(result: Dict, output_dir: str, session_id: str):
    """Save result in _classified.json format."""
    out_path = Path(output_dir) / f"{session_id}_classified.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


# =============================================================================
# Few-shot examples (hardcoded from real training data)
# =============================================================================

FEWSHOT_CHANGE_VIEW = '''{"time": "01-01 10:00:00.000", "event": "[change_view]
<path>workspace/sample-app/src/validator.js</path>
<current_view>
<start_line>10</start_line>
<end_line>22</end_line>
</current_view>
<context_after_operation>
<note>The following text is visible after this view change.</note>
<visible_start_line>10</visible_start_line>
<visible_end_line>22</visible_end_line>
<visible_content>
const schema = object({
  label: string().min(1).max(32).required()
});
module.exports = { schema };
</visible_content>
</context_after_operation>"}'''

FEWSHOT_EDIT_TEXT = '''{"time": "01-01 10:01:12.000", "event": "[edit_text]
<path>workspace/sample-app/README.md</path>
<context_after_operation>
<note>The adjacent text after this operation is shown below.</note>
<context_start_line>2</context_start_line>
<context_end_line>8</context_end_line>
<context_content>
## Setup

```bash
git clone https://example.com/org/sample-app.git
git <FT_delete>checkout</FT_delete><FT_insert>switch</FT_insert> feature-request-id
```
</context_content>
</context_after_operation>"}'''

# Load from file — too long to hardcode
_flow_path = Path(__file__).parent / "few_shot_flow_examples.json"
FEWSHOT_FLOW = _flow_path.read_text(encoding="utf-8") if _flow_path.exists() else "[]"



# =============================================================================
# Mode 1: Restricted (strict XML format templates, change_view + edit_text only)
# =============================================================================

RESTRICTED_SCENE_PROMPT = """<Task>
You are tasked to generate a realistic IDE coding scenario. The scene will be used to simulate developer operations in a simulator environment.
The developer can ONLY perform two types of operations: change_view and edit_text.
</Task>

## Real Session Signals
Project: {project_name}
Operation type distribution: {op_types}
Files touched: {files_touched}
Session duration: {duration_min} minutes

## STRICT FORMAT — change_view:
{{"time": "MM-DD HH:MM:SS.mmm", "event": "[change_view]\\n<path>{{file_path}}</path>\\n<current_view>\\n<start_line>{{N}}</start_line>\\n<end_line>{{M}}</end_line>\\n</current_view>\\n<context_after_operation>\\n<note>The following is the text visible in the viewport after this view change operation.</note>\\n<visible_start_line>{{N}}</visible_start_line>\\n<visible_end_line>{{M}}</visible_end_line>\\n<visible_content>\\n{{actual_code_lines}}\\n</visible_content>\\n</context_after_operation>"}}

## STRICT FORMAT — edit_text:
{{"time": "MM-DD HH:MM:SS.mmm", "event": "[edit_text]\\n<path>{{file_path}}</path>\\n<context_after_operation>\\n<note>The part wrapped with <FT_delete></FT_delete> is replaced by the content wrapped with <FT_insert></FT_insert>.</note>\\n<context_start_line>{{N}}</context_start_line>\\n<context_end_line>{{M}}</context_end_line>\\n<context_content>\\n{{surrounding_code}}<FT_insert>{{new_code}}</FT_insert>{{more_code}}<FT_delete>{{old_code}}</FT_delete>{{rest}}\\n</context_content>\\n</context_after_operation>"}}

## Instructions
1. Generate a scene description with project context, tech stack, and coding tasks
2. agent_ops MUST only contain change_view and edit_text — no other operation types
3. Generate at least 10 operations_example following the EXACT XML formats above
4. change_view should include 10-30 lines of realistic code in visible_content
5. edit_text should show concrete code changes with FT_insert and FT_delete tags

Output as JSON (no markdown):
{{
  "environment": {{
    "theme": "short description",
    "description": "detailed project description",
    "entities": ["list of files and modules"],
    "agent_ops": ["change_view(file_path) -> str", "edit_text(file_path, start_line, end_line, new_content) -> bool"],
    "operations_example": ["list of example operations following the strict formats above"]
  }},
  "user": {{
    "goal": "coding tasks the developer will perform"
  }}
}}"""


def run_restricted(
    session_path: str,
    output_dir: str,
    llm_config: Dict,
) -> Optional[Dict]:
    """Mode 1: Generate scene with restricted ops and run the simulator."""
    data = load_classified_session(session_path)
    if not data:
        return None

    scene = extract_scene_description(data)
    session_id = Path(session_path).stem.replace('_classified', '')

    prompt = RESTRICTED_SCENE_PROMPT.format(
        project_name=scene['project_name'],
        op_types=scene['op_types'],
        files_touched=scene['files_touched'],
        duration_min=scene['duration_min'],
    )

    print(f"  [restricted] Generating scene for {session_id}...")
    response = call_llm(prompt, llm_config['api_key'], llm_config['endpoint'],
                        llm_config['model'], max_retries=3, max_tokens=8192)
    parsed = parse_json_response(response)
    if not parsed:
        print(f"  [restricted] Failed to parse scene")
        return None

    # Build scene.yaml compatible with the simulator
    env = parsed.get('environment', parsed)
    user = parsed.get('user', {})
    theme = env.get('theme', 'Software Development')

    scene_yaml_path = Path(output_dir) / 'temp_scenes' / f'{session_id}.yaml'
    scene_jsonl_path = scene_yaml_path.with_suffix('.jsonl')

    scene_config = {
        'eventSink': {'out_file': str(scene_jsonl_path)},
        'environment': {
            'theme': theme,
            'description': env.get('description', ''),
            'entities': env.get('entities', []),
            'agent_ops': env.get('agent_ops', []),
            'events_example': env.get('operations_example', []),
        },
        'user': {
            'theme': theme,
            'goal': user.get('goal', ''),
        },
        'agent': {},
    }

    scene_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(scene_yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(scene_config, f, allow_unicode=True, default_flow_style=False)

    # Run simulator
    from run_simulation import run_one
    print(f"  [restricted] Running simulator...")
    status = run_one(str(scene_yaml_path), resume=False, verbose=False)
    if status != 'ok':
        print(f"  [restricted] Simulator failed: {status}")
        return None

    # Load simulated events and convert to operations
    from build_synthetic_training_data import load_scene, events_to_operations
    records = load_scene(str(scene_jsonl_path))
    ops = events_to_operations(records)
    if not ops:
        print(f"  [restricted] No operations generated")
        return None

    print(f"  [restricted] Generated {len(ops)} operations")

    # Run aggregate → refine → classify pipeline
    tasks = step_aggregate(ops, llm_config, window_size=50, window_stride=50)
    tasks = step_refine(tasks, ops, llm_config)
    result = step_classify(tasks, ops, llm_config,
                          session_id=f"synthetic_restricted_{session_id}",
                          metadata={'mode': 'restricted', 'source_session': session_id})

    save_classified_output(result, output_dir, f"synthetic_restricted_{session_id}")
    return result


# =============================================================================
# Mode 2: Few-shot (simulator pipeline with few-shot examples in scene)
# =============================================================================

FEWSHOT_SCENE_PROMPT = """<Task>
You are tasked to generate a realistic IDE coding scenario. The scene will be used to simulate developer operations in a simulator environment.
</Task>

## Real Session Signals
Project: {project_name}
Operation type distribution: {op_types}
Files touched: {files_touched}
Session duration: {duration_min} minutes

## Real IDE Operation Examples (follow these exact XML formats)

### change_view example:
{change_view_example}

### edit_text example:
{edit_text_example}

### Operation Flow (showing natural dependencies between operations):
{flow_example}

## Instructions
1. Generate a scene description with project context, tech stack, and coding tasks
2. Generate agent_ops — the available operations for this scenario (you decide what operations are appropriate)
3. Generate at least 10 operations_example following the EXACT XML formats shown above
4. The operations should reference files and code consistent with the project

Output as JSON (no markdown):
{{
  "environment": {{
    "theme": "short description",
    "description": "detailed project description",
    "entities": ["list of files and modules"],
    "agent_ops": ["list of available operations in Python style"],
    "operations_example": ["list of example operations following the formats above"]
  }},
  "user": {{
    "goal": "coding tasks the developer will perform"
  }}
}}"""


def run_fewshot(
    session_path: str,
    output_dir: str,
    llm_config: Dict,
) -> Optional[Dict]:
    """Mode 2: Generate scene with few-shot and run through the simulator."""
    data = load_classified_session(session_path)
    if not data:
        return None

    scene = extract_scene_description(data)
    session_id = Path(session_path).stem.replace('_classified', '')

    prompt = FEWSHOT_SCENE_PROMPT.format(
        project_name=scene['project_name'],
        op_types=scene['op_types'],
        files_touched=scene['files_touched'],
        duration_min=scene['duration_min'],
        change_view_example=FEWSHOT_CHANGE_VIEW,
        edit_text_example=FEWSHOT_EDIT_TEXT,
        flow_example=FEWSHOT_FLOW,
    )

    print(f"  [fewshot] Generating scene for {session_id}...")
    response = call_llm(prompt, llm_config['api_key'], llm_config['endpoint'],
                        llm_config['model'], max_retries=3, max_tokens=8192)
    parsed = parse_json_response(response)
    if not parsed:
        print(f"  [fewshot] Failed to parse scene")
        return None

    # Build scene.yaml compatible with the simulator
    env = parsed.get('environment', parsed)
    user = parsed.get('user', {})
    theme = env.get('theme', 'Software Development')

    scene_yaml_path = Path(output_dir) / 'temp_scenes' / f'{session_id}.yaml'
    scene_jsonl_path = scene_yaml_path.with_suffix('.jsonl')

    scene_config = {
        'eventSink': {'out_file': str(scene_jsonl_path)},
        'environment': {
            'theme': theme,
            'description': env.get('description', ''),
            'entities': env.get('entities', []),
            'agent_ops': env.get('agent_ops', []),
            'events_example': env.get('operations_example', []),
        },
        'user': {
            'theme': theme,
            'goal': user.get('goal', ''),
        },
        'agent': {},
    }

    scene_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(scene_yaml_path, 'w', encoding='utf-8') as f:
        yaml.dump(scene_config, f, allow_unicode=True, default_flow_style=False)

    # Run simulator
    from run_simulation import run_one
    print(f"  [fewshot] Running simulator...")
    status = run_one(str(scene_yaml_path), resume=False, verbose=False)
    if status != 'ok':
        print(f"  [fewshot] Simulator failed: {status}")
        return None

    # Load simulated events and convert to operations
    from build_synthetic_training_data import load_scene, events_to_operations
    records = load_scene(str(scene_jsonl_path))
    ops = events_to_operations(records)
    if not ops:
        print(f"  [fewshot] No operations generated")
        return None

    print(f"  [fewshot] Generated {len(ops)} operations")

    # Run aggregate → refine → classify pipeline
    tasks = step_aggregate(ops, llm_config, window_size=50, window_stride=50)
    tasks = step_refine(tasks, ops, llm_config)
    result = step_classify(tasks, ops, llm_config,
                          session_id=f"synthetic_fewshot_{session_id}",
                          metadata={'mode': 'fewshot', 'source_session': session_id})

    save_classified_output(result, output_dir, f"synthetic_fewshot_{session_id}")
    return result


# =============================================================================
# Mode 3: Hierarchical (task-first, all positive)
# =============================================================================

HIERARCHICAL_PHASE1_PROMPT = """You are generating proactive task descriptions for an AI IDE assistant.

## Scene Description
Project: {project_name}
Files involved: {files_touched}
Session duration: {duration_min} minutes

## Task
Generate {num_tasks} one-sentence proactive task descriptions that an AI coding assistant could suggest to a developer working in this project.

Each task should be:
1. Predictable from the developer's recent IDE activity
2. Valuable — saves time or prevents mistakes
3. Actionable — involves specific files, functions, or changes
4. Concrete — names exact files and functions, not vague descriptions

## Output Format (JSON array, no markdown):
[
  {{"task": "Add null check for database connection in ConnectionPool.getConnection()", "trigger_context": "Developer just added a new query method without error handling"}},
  ...
]"""

HIERARCHICAL_PHASE2_SYSTEM = """You are simulating a developer's IDE operations. You generate ONE operation at a time.

## Task That Will Be Predicted (do NOT mention this in operations — it should be naturally predictable from the operations)
Task: {task_description}
Context: {trigger_context}

## Scene
Project: {project_name}
Files: {files_touched}

## Operation Format Examples (follow these EXACT XML formats):

### change_view:
{change_view_example}

### edit_text:
{edit_text_example}

## Rules:
- Output EXACTLY ONE operation per turn as a JSON object
- Use the EXACT XML format shown above
- You decide what operation types to generate — follow the formats naturally
- Each operation should logically follow from the previous ones
- The sequence should gradually build context that makes the task predictable
- Timestamp format: "MM-DD HH:MM:SS.mmm", increment realistically (1-30s between ops)"""

HIERARCHICAL_PHASE2_STEP = """Generate the next IDE operation (#{step_num} of {total_steps}).{hint}

Previous operations:
{previous_ops}

Output ONE JSON object: {{"time": "...", "event": "[type]\\n..."}}"""


def run_hierarchical(
    session_path: str,
    output_dir: str,
    llm_config: Dict,
) -> Optional[Dict]:
    """Mode 3: Two-phase hierarchical generation. All samples positive."""
    data = load_classified_session(session_path)
    if not data:
        return None

    scene = extract_scene_description(data)
    session_id = Path(session_path).stem.replace('_classified', '')

    # Phase 1: Generate task descriptions
    phase1_prompt = HIERARCHICAL_PHASE1_PROMPT.format(
        project_name=scene['project_name'],
        files_touched=scene['files_touched'],
        duration_min=scene['duration_min'],
        num_tasks=3,
    )

    print(f"  [hierarchical] Phase 1: Generating tasks for {session_id}...")
    response = call_llm(phase1_prompt, llm_config['api_key'], llm_config['endpoint'],
                        llm_config['model'], max_retries=3)
    tasks_parsed = parse_json_response(response)
    if not tasks_parsed or not isinstance(tasks_parsed, list):
        print(f"  [hierarchical] Phase 1 failed")
        return None

    print(f"  [hierarchical] Generated {len(tasks_parsed)} tasks")

    # Phase 2: For each task, generate operation sequence
    all_positives = []
    all_ops = []
    op_id_offset = 0

    for task_info in tasks_parsed:
        task = task_info.get('task', '')
        trigger_context = task_info.get('trigger_context', '')
        if not task:
            continue

        system_prompt = HIERARCHICAL_PHASE2_SYSTEM.format(
            task_description=task,
            trigger_context=trigger_context,
            project_name=scene['project_name'],
            files_touched=scene['files_touched'],
            change_view_example=FEWSHOT_CHANGE_VIEW,
            edit_text_example=FEWSHOT_EDIT_TEXT,
        )

        print(f"  [hierarchical] Phase 2: Generating 20 ops for '{task[:50]}...'")
        total_steps = 20
        generated_ops = []

        for step in range(1, total_steps + 1):
            # Build hint for last few operations
            if step >= total_steps - 2:
                hint = " This is one of the final operations — include signals that make the task predictable."
            elif step == 1:
                hint = " This is the first operation — start by viewing a relevant file."
            else:
                hint = ""

            # Pass all previous operations as context
            if generated_ops:
                prev_summary = json.dumps(generated_ops, ensure_ascii=False, indent=2)
            else:
                prev_summary = "(none — this is the first operation)"

            step_prompt = HIERARCHICAL_PHASE2_STEP.format(
                step_num=step,
                total_steps=total_steps,
                hint=hint,
                previous_ops=prev_summary,
            )

            # Call LLM with system + step prompt
            full_prompt = system_prompt + "\n\n" + step_prompt
            response = call_llm(full_prompt, llm_config['api_key'], llm_config['endpoint'],
                                llm_config['model'], max_retries=2)
            if not response:
                print(f"  [hierarchical] Step {step} failed, skipping")
                continue

            # Parse single operation
            parsed = parse_json_response(response)
            if parsed and isinstance(parsed, dict) and 'event' in parsed:
                generated_ops.append(parsed)
            else:
                # Try wrapping in array and parsing
                ops_parsed = parse_generated_ops(response)
                if ops_parsed:
                    generated_ops.append({"time": ops_parsed[0].get("timestamp", ""), "event": ops_parsed[0].get("readableDetails", "")})

        # Convert to pipeline format
        ops = []
        for i, item in enumerate(generated_ops):
            event = item.get('event', '')
            if '[change_view]' in event:
                op_type = 'change_view'
            elif '[edit_text]' in event:
                op_type = 'edit_text'
            elif '[cursor_selection]' in event:
                op_type = 'cursor_selection'
            elif '[clipboard_change]' in event:
                op_type = 'clipboard_change'
            else:
                op_type = 'edit_text'
            ops.append({
                'id': i,
                'operationType': op_type,
                'timestamp': item.get('time', ''),
                'readableDetails': event,
            })

        if len(ops) < 5:
            print(f"  [hierarchical] Only {len(ops)} ops, skipping this task")
            continue

        print(f"  [hierarchical] Generated {len(ops)} ops")

        # Re-number op IDs
        for op in ops:
            op['id'] = op['id'] + op_id_offset

        last_op_id = ops[-1]['id'] if ops else op_id_offset
        trigger_op_id = last_op_id

        all_positives.append({
            'summary': task,
            'detail': trigger_context,
            'op_start_id': ops[0]['id'],
            'op_end_id': last_op_id,
            'confidence': 1.0,
            'reasoning': 'Hierarchically generated: task specified first, operations generated to lead to it.',
            'trigger_after_op_id': trigger_op_id,
            'suggested_prompt': task,
        })
        all_ops.extend(ops)
        op_id_offset = last_op_id + 1

        time.sleep(1)  # Rate limit

    if not all_positives:
        print(f"  [hierarchical] No valid tasks generated")
        return None

    print(f"  [hierarchical] Total: {len(all_positives)} tasks, {len(all_ops)} operations")

    result = {
        'session_id': f"synthetic_hierarchical_{session_id}",
        'metadata': {'mode': 'hierarchical', 'source_session': session_id},
        'operations': all_ops,
        'positive': all_positives,
        'negative': [],
        'classification_info': {
            'total_tasks': len(all_positives),
            'positive_count': len(all_positives),
            'negative_count': 0,
        },
    }

    save_classified_output(result, output_dir, f"synthetic_hierarchical_{session_id}")
    return result


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Unified synthetic data pipeline')
    parser.add_argument('--mode', required=True, choices=['restricted', 'fewshot', 'hierarchical'],
                        help='Synthesis mode')
    parser.add_argument('--classify-dir', default='classify_output',
                        help='Directory with classified session files')
    parser.add_argument('--output-dir', default=None,
                        help='Output directory (default depends on --mode)')
    parser.add_argument('--max-scenes', type=int, default=None,
                        help='Maximum number of scenes to generate')
    parser.add_argument('--min-positives', type=int, default=1,
                        help='Minimum positives in source session')
    parser.add_argument('--single', type=str, default=None,
                        help='Process single classified session file')
    parser.add_argument('--resume', action='store_true',
                        help='Skip already generated outputs')
    parser.add_argument('--workers', type=int, default=1,
                        help='Parallel workers (default: 1 for sequential)')
    # LLM config
    parser.add_argument('--api-key', default=DEFAULT_API_KEY)
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args()

    mode_dir = {
        'restricted': 'synthetic_restricted',
        'fewshot': 'synthetic_fewshot',
        'hierarchical': 'synthetic_hierarchical',
    }
    if args.output_dir is None:
        args.output_dir = f'synthetic_data/scenarios/{mode_dir[args.mode]}'

    print(f"Mode: {args.mode}")
    print(f"Output: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    llm_config = {
        'call_llm': True,
        'api_key': args.api_key,
        'endpoint': args.endpoint,
        'model': args.model,
        'max_detail_chars': 1200,
        'max_prompt_chars': 350000,
        'min_request_interval': 1.2,
    }

    # Collect session files
    if args.single:
        session_files = [args.single]
    else:
        session_files = sorted(
            glob.glob(os.path.join(args.classify_dir, '**', '*_classified.json'), recursive=True)
        )

    # Filter by min positives
    eligible = []
    for f in session_files:
        try:
            data = json.load(open(f, 'r', encoding='utf-8'))
            if len(data.get('positive', [])) >= args.min_positives:
                eligible.append(f)
        except Exception:
            continue

    print(f"Eligible sessions: {len(eligible)}")
    if args.max_scenes:
        eligible = eligible[:args.max_scenes]

    # Filter by resume
    if args.resume:
        todo = []
        for f in eligible:
            sid = Path(f).stem.replace('_classified', '')
            prefix = f"synth_{mode_suffix[args.mode]}_{sid}"
            out_path = Path(args.output_dir) / f"{prefix}_classified.json"
            if not out_path.exists():
                todo.append(f)
        skipped = len(eligible) - len(todo)
        eligible = todo
        print(f"Skipped by resume: {skipped}, remaining: {len(eligible)}")

    # Select mode function
    mode_fn = {
        'restricted': run_restricted,
        'fewshot': run_fewshot,
        'hierarchical': run_hierarchical,
    }[args.mode]

    # Process
    total_positive = 0
    total_negative = 0
    success = 0

    def process_one(session_path):
        return mode_fn(session_path, args.output_dir, llm_config)

    pbar = tqdm(total=len(eligible), desc=f"synth_{args.mode}", unit='scene')

    if args.workers <= 1:
        for f in eligible:
            try:
                result = process_one(f)
                if result:
                    total_positive += result['classification_info']['positive_count']
                    total_negative += result['classification_info']['negative_count']
                    success += 1
            except Exception as e:
                print(f"\n  [ERROR] {Path(f).name}: {e}")
            pbar.set_postfix(ok=success, pos=total_positive)
            pbar.update(1)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_one, f): f for f in eligible}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        total_positive += result['classification_info']['positive_count']
                        total_negative += result['classification_info']['negative_count']
                        success += 1
                except Exception as e:
                    print(f"\n  [ERROR] {Path(futures[future]).name}: {e}")
                pbar.set_postfix(ok=success, pos=total_positive)
                pbar.update(1)

    pbar.close()
    print(f"\nDone. Success: {success}/{len(eligible)}")
    print(f"  Positive: {total_positive}, Negative: {total_negative}")
    print(f"  Output: {args.output_dir}")


if __name__ == '__main__':
    main()
