#!/usr/bin/env python3
"""
 session (classify_output)  scene.yaml


  - /
  - 
   gap

:
  #  session
  python3 synthetic_data/convert_sessions_to_scenarios.py \\
      --session classify_output/xxx/.../session_yyy_classified.json \\
      --output synthetic_data/scenarios/agent_data/scene_N.yaml

  #  session  scene
  python3 synthetic_data/convert_sessions_to_scenarios.py \\
      --classify-dir classify_output \\
      --output-dir synthetic_data/scenarios/agent_data \\
      --min-positives 1
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

import requests
import yaml

# ===== API  =====
DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DEFAULT_ENDPOINT = os.environ.get("OPENAI_API_BASE", "")
DEFAULT_MODEL = "deepseek-v3.2"

# agent_ops are filled by run_synthetic_pipeline.py for each generation mode.

# ===== Prompt =====
EXTRACTION_PROMPT = """<Task>
You are tasked to generate a realistic IDE coding scenario for simulating developer operations.
The simulation will ONLY produce three types of operations: open_file, edit_text, and search.
</Task>

<Rule>
- Make sure each detail is consistent with the previous details.
- Focus on concrete coding activities, NOT abstract descriptions.
- Do NOT generate user profiles, personas, or biographical information.
- All example operations must be one of: open_file, edit_text, search.
</Rule>

## Scene Description Generation

The following signals are extracted from a real developer session. Use them to ground the scene in reality.

Scene: {project_name}
Operation type distribution: {op_types}
Files touched during session (sample):
{files_touched}
Session duration: {duration_min} minutes

Context: Detail the project type, tech stack, directory structure, and key files involved.
Coding Tasks: List 2-4 concrete coding tasks the developer will perform (e.g., "fix a bug in auth middleware", "add pagination to API endpoint").

Now generate the entities (files, modules, configs) involved in the scene.

Now generate at least 10 example operations. Each operation must be fine-grained and specific:

- [open_file] Include 10-20 lines of realistic code that would be visible in the editor. Example:
  [open_file] src/auth/middleware.ts line 15-35:
  export async function authMiddleware(req, res, next) {{
    const token = req.headers.authorization?.split(' ')[1];
    if (!token) {{ return res.status(401).json({{ error: 'No token' }}); }}
    ...
  }}

- [edit_text] Include exact line numbers, old code, and new code. Example:
  [edit_text] src/auth/middleware.ts line 18: changed `const token = req.headers.authorization?.split(' ')[1];` to `const token = extractBearerToken(req.headers);` and added `import {{ extractBearerToken }} from '../utils/token';` at line 2.

- [search] Include the exact query and results. Example:
  [search] query="extractBearerToken" scope=workspace, found 3 results: src/utils/token.ts:5, src/auth/middleware.ts:2, tests/auth.test.ts:12

---
1. Operations should form a coherent coding workflow (open → read → edit → search → edit ...).
2. Each operation must include concrete code content — NOT abstract descriptions like "edits the file to add a method".
3. Do NOT include terminal commands, git operations, browser actions, or any operation outside open_file/edit_text/search.
---

Now summarize and structure all the content above in JSON syntax (JSON only, no markdown):
{{
  "environment": {{
    "theme": "a short term to describe the scene",
    "description": "project description, tech stack, directory structure",
    "entities": ["list of files and modules involved"],
    "operations_example": ["list of example operations in the formats above"]
  }},
  "user": {{
    "goal": "The concrete coding tasks the user will perform"
  }}
}}

Generate exactly ONE scene config as a single JSON object (not an array). Make sure it is consistent and logically correct."""


def call_llm(prompt: str, api_key: str, endpoint: str, model: str,
             max_retries: int = 3, max_tokens: int = 4096) -> Optional[str]:
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.7,
        'max_tokens': max_tokens,
    }
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f'{endpoint}chat/completions',
                headers=headers, json=payload, timeout=120,
            )
            resp.raise_for_status()
            return resp.json()['choices'][0]['message']['content']
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  [ERROR] LLM failed: {e}", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
    return None


def parse_json_response(response: str) -> Optional[Dict]:
    if not response:
        return None
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n\s*```', response, re.DOTALL)
    for block in blocks:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    m = re.search(r'\{.*\}', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def extract_op_types(operations: List[Dict]) -> str:
    """ operation """
    from collections import Counter
    types = Counter(op.get('operationType', 'unknown') for op in operations)
    return ', '.join(f"{t}({n})" for t, n in types.most_common(8))


def extract_files_touched(operations: List[Dict], max_files: int = 30) -> str:
    """ operations  readableDetails """
    seen = []
    seen_set = set()
    for op in operations:
        details = op.get('readableDetails', '')
        paths = re.findall(r'<path>(.*?)</path>', details)
        for p in paths:
            #  file://  URL 
            p = re.sub(r'^file:///[a-zA-Z]%3A/', '', p)
            p = re.sub(r'^file:///', '', p)
            p = p.replace('%3A', ':')
            # 
            parts = Path(p).parts
            #  4 
            rel = str(Path(*parts[-4:])) if len(parts) >= 4 else p
            if rel not in seen_set:
                seen_set.add(rel)
                seen.append(rel)
            if len(seen) >= max_files:
                break
        if len(seen) >= max_files:
            break
    return '\n'.join(seen) if seen else '(no file paths found)'


def load_classified_session(filepath: str) -> Optional[Dict]:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"  [WARN] Failed to load {filepath}: {e}")
        return None


def convert_sessions_to_scenarios_yaml(
    session_path: str,
    output_path: str,
    api_key: str,
    endpoint: str,
    model: str,
    scene_index: int = 0,
) -> bool:
    """ session  scene.yaml"""
    data = load_classified_session(session_path)
    if not data:
        return False

    metadata = data.get('metadata', {})
    operations = data.get('operations', [])

    project_name = metadata.get('project_name', 'unknown project')
    op_types = extract_op_types(operations)
    files_touched = extract_files_touched(operations)

    #  session 
    timestamps = [op.get('timestamp', 0) for op in operations if op.get('timestamp')]
    if len(timestamps) >= 2:
        first, last = min(timestamps), max(timestamps)
        if isinstance(first, (int, float)):
            duration_min = round((last - first) / 60000, 1)
        else:
            #  "MM-DD HH:MM:SS.mmm"
            from datetime import datetime
            try:
                fmt = "%m-%d %H:%M:%S.%f" if '.' in str(first) else "%m-%d %H:%M:%S"
                t0 = datetime.strptime(str(first), fmt)
                t1 = datetime.strptime(str(last), fmt)
                duration_min = round((t1 - t0).total_seconds() / 60, 1)
            except Exception:
                duration_min = 0
    else:
        duration_min = 0

    #  LLM  scene 
    prompt = EXTRACTION_PROMPT.format(
        project_name=project_name,
        op_types=op_types,
        files_touched=files_touched,
        duration_min=duration_min,
    )

    print(f"  Calling LLM for {Path(session_path).name} ...")
    response = call_llm(prompt, api_key, endpoint, model)
    parsed = parse_json_response(response)

    if not parsed:
        print(f"  [ERROR] Failed to parse LLM response")
        return False

    #  scene.yaml build_scene_configs.py 
    # LLM  {environment: {...}, user: {...}}
    env = parsed.get('environment', parsed)  # fallback: flat dict
    user = parsed.get('user', {})
    theme = env.get('theme', 'Software Development')

    out_file = output_path.replace('.yaml', '.jsonl')
    scene_config = {
        'eventSink': {'out_file': out_file},
        'environment': {
            'theme': theme,
            'description': env.get('description', ''),
            'entities': env.get('entities', []),
            'agent_ops': [],  # Filled by run_synthetic_pipeline.py for each mode.
            'operations_example': env.get('operations_example', []),
        },
        'user': {
            'theme': theme,
            'goal': user.get('goal', ''),
        },
        'agent': {},
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(scene_config, f, allow_unicode=True, default_flow_style=False)

    print(f"  Saved: {output_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description='Generate scene.yaml from real sessions')
    #  session 
    parser.add_argument('--session', type=str, default=None,
                        help='Path to a single classified session JSON')
    parser.add_argument('--output', type=str, default=None,
                        help='Output scene.yaml path (single mode)')
    # 
    parser.add_argument('--classify-dir', type=str, default='classify_output',
                        help='classify_output directory (batch mode)')
    parser.add_argument('--output-dir', type=str,
                        default='synthetic_data/scenarios/agent_data',
                        help='Output directory for scene yaml files (batch mode)')
    parser.add_argument('--min-positives', type=int, default=1,
                        help='Minimum positive examples required to process a session')
    parser.add_argument('--max-scenes', type=int, default=None,
                        help='Maximum number of scenes to generate')
    parser.add_argument('--start-index', type=int, default=0,
                        help='Starting scene index for file naming')
    parser.add_argument('--resume', action='store_true',
                        help='Skip scenes whose yaml already exists')
    parser.add_argument('--workers', type=int, default=8,
                        help='Number of parallel workers (default: 8)')
    # LLM 
    parser.add_argument('--api-key', default=DEFAULT_API_KEY)
    parser.add_argument('--endpoint', default=DEFAULT_ENDPOINT)
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args()

    #  session 
    if args.session:
        output = args.output or 'scene_from_session.yaml'
        success = convert_sessions_to_scenarios_yaml(
            args.session, output,
            args.api_key, args.endpoint, args.model,
        )
        sys.exit(0 if success else 1)

    # 
    session_files = sorted(
        glob.glob(os.path.join(args.classify_dir, '**', '*_classified.json'),
                  recursive=True)
    )
    print(f"Found {len(session_files)} classified session files")

    #  session session 
    eligible = []
    for f in session_files:
        try:
            data = json.load(open(f, 'r', encoding='utf-8'))
            if len(data.get('positive', [])) >= args.min_positives:
                eligible.append(f)
        except Exception:
            continue

    print(f"Eligible sessions (>={args.min_positives} positives): {len(eligible)}")

    if args.max_scenes:
        eligible = eligible[:args.max_scenes]
        print(f"Limiting to {args.max_scenes} scenes")

    os.makedirs(args.output_dir, exist_ok=True)

    def make_output_path(session_path: str) -> str:
        """{output_dir}/{date}_{session_id}.yaml
        classify_output/{hash}/operations/{project}/{date}/{session_id}_classified.json
        """
        p = Path(session_path)
        # session_id  _classified 
        session_id = p.stem.replace('_classified', '')
        # date 
        date_part = p.parent.name
        # fallback  unknown
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_part):
            date_part = 'unknown'
        filename = f"{date_part}_{session_id}.yaml"
        return os.path.join(args.output_dir, filename)

    def process_one(session_path: str) -> tuple[bool, bool]:
        """Returns (success, skipped)."""
        output_path = make_output_path(session_path)
        out_name = Path(output_path).name
        if args.resume and os.path.exists(output_path):
            print(f"  skip (exists): {out_name}")
            return True, True
        print(f"  {Path(session_path).name} → {out_name}")
        ok = convert_sessions_to_scenarios_yaml(
            session_path, output_path,
            args.api_key, args.endpoint, args.model,
        )
        return ok, False

    success_count = 0
    skipped_count = 0
    workers = min(args.workers, len(eligible)) if eligible else 1

    print(f"Running with {workers} parallel workers ...")
    try:
        from tqdm import tqdm
        pbar = tqdm(total=len(eligible), unit='scene')
    except ImportError:
        pbar = None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_one, sp): sp for sp in eligible}
        for future in as_completed(futures):
            try:
                ok, skipped = future.result()
                if ok:
                    success_count += 1
                if skipped:
                    skipped_count += 1
            except Exception as e:
                print(f"  [ERROR] {futures[future]}: {e}", file=sys.stderr)
            if pbar is not None:
                pbar.set_postfix(ok=success_count, skip=skipped_count)
                pbar.update(1)

    if pbar is not None:
        pbar.close()

    print(f"\n{'='*50}")
    print(f"Done. Generated {success_count}/{len(eligible)} scene files "
          f"({skipped_count} skipped by resume).")
    print(f"Output: {args.output_dir}")


if __name__ == '__main__':
    main()
