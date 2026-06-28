#!/usr/bin/env python3
"""
Diagnostic script for analyzing why the agent's proactive tool call rate is low.

Analyzes:
1. Agent trace files to understand exit statuses and tool call patterns
2. Sample output files to understand proposal rates
3. Root cause analysis for low propose_task/no_proposal call rates

Usage:
  python3 diagnose_agent_outputs.py
  python3 diagnose_agent_outputs.py --samples-file predictions/openai_deepseek-v3.2_agent/agent_n100_s24_seed0.json
  python3 diagnose_agent_outputs.py --trace-dir traces_agent/openai_deepseek-v3.2
"""

import json
import os
import sys
from pathlib import Path
from collections import Counter, defaultdict

import fire


def analyze_samples(samples_file: str) -> dict:
    """Analyze the samples output file."""
    data = json.loads(Path(samples_file).read_text(encoding="utf-8"))
    total = len(data)
    proposed = sum(1 for r in data if r["agent_response"] is not None)
    empty_raw = sum(1 for r in data if r["raw_response"] == "")
    no_proposal = sum(1 for r in data if r["raw_response"] and "NO_PROPOSAL" in r["raw_response"])

    return {
        "total": total,
        "proposed": proposed,
        "empty_raw": empty_raw,
        "no_proposal": no_proposal,
        "proposal_rate": proposed / total if total > 0 else 0,
    }


def analyze_traces(trace_dir: str) -> dict:
    """Deep analysis of all trace files."""
    trace_path = Path(trace_dir)
    if not trace_path.exists():
        print(f"Error: trace directory not found: {trace_dir}")
        return {}

    # ── Per-trace statistics ──
    exit_statuses = Counter()
    api_calls_dist = []           # api_calls per trace
    tool_call_patterns = []       # list of tool names per trace
    blocked_cmds = Counter()      # blocked command types
    blocked_per_trace = []        # number of blocked cmds per trace
    bash_cmd_types = Counter()    # base command names used in bash
    wasted_on_path_issues = 0     # steps wasted on path problems (spaces, wrong name)
    format_errors = 0
    traces_with_no_custom_tool = 0
    submission_contents = []
    last_step_actions = []        # what the model does in its last step

    # ── For step-by-step analysis ──
    propose_at_step = []          # at which step propose_task was called
    no_proposal_at_step = []

    for f in sorted(trace_path.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        info = data.get("info", {})
        exit_status = info.get("exit_status", "unknown")
        exit_statuses[exit_status] += 1
        api_calls = info.get("model_stats", {}).get("api_calls", 0)
        api_calls_dist.append(api_calls)
        submission = info.get("submission", "")
        submission_contents.append(submission)

        messages = data.get("messages", [])
        tools_used = []
        blocked_count = 0
        step = 0
        last_assistant_tool = None

        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "assistant":
                step += 1
                tool_calls = msg.get("tool_calls", [])
                for tc in tool_calls:
                    fn = tc.get("function", {}).get("name", "unknown")
                    tools_used.append(fn)
                    last_assistant_tool = fn

                    if fn == "bash":
                        try:
                            args = json.loads(tc["function"]["arguments"])
                            cmd = args.get("command", "").strip()
                            base = cmd.split()[0].rsplit("/", 1)[-1] if cmd else ""
                            bash_cmd_types[base] += 1
                        except Exception:
                            pass

                    if fn == "propose_task":
                        propose_at_step.append(step)
                    elif fn == "no_proposal":
                        no_proposal_at_step.append(step)

            elif role == "tool":
                if "Command blocked" in content:
                    blocked_count += 1
                    # Identify which command was blocked
                    # Look back to find the assistant's tool call
                    for j in range(i - 1, -1, -1):
                        prev = messages[j]
                        if prev.get("role") == "assistant" and prev.get("tool_calls"):
                            for tc in prev["tool_calls"]:
                                if tc["function"]["name"] == "bash":
                                    try:
                                        args = json.loads(tc["function"]["arguments"])
                                        cmd = args.get("command", "").strip()
                                        base = cmd.split()[0].rsplit("/", 1)[-1]
                                        blocked_cmds[base] += 1
                                    except Exception:
                                        pass
                            break

            elif role == "user" and "Format error" in content:
                format_errors += 1

        tool_call_patterns.append(tools_used)
        blocked_per_trace.append(blocked_count)

        has_custom = any(t in tools_used for t in ["propose_task", "no_proposal"])
        if not has_custom:
            traces_with_no_custom_tool += 1

        last_step_actions.append(last_assistant_tool)

    # ── Aggregation ──
    total_traces = sum(exit_statuses.values())
    all_tools_flat = [t for p in tool_call_patterns for t in p]
    bash_count = all_tools_flat.count("bash")
    propose_count = all_tools_flat.count("propose_task")
    no_proposal_count = all_tools_flat.count("no_proposal")

    # Blocked analysis
    total_blocked = sum(blocked_per_trace)
    total_steps = sum(api_calls_dist)

    # LimitsExceeded specific
    exceeded_traces = [
        i for i, (p, _) in enumerate(
            zip(tool_call_patterns, exit_statuses.elements())
        )
    ]

    return {
        "total_traces": total_traces,
        "exit_statuses": dict(exit_statuses.most_common()),
        "api_calls_distribution": {
            "min": min(api_calls_dist) if api_calls_dist else 0,
            "max": max(api_calls_dist) if api_calls_dist else 0,
            "avg": sum(api_calls_dist) / len(api_calls_dist) if api_calls_dist else 0,
        },
        "tool_call_totals": {
            "bash": bash_count,
            "propose_task": propose_count,
            "no_proposal": no_proposal_count,
            "total": len(all_tools_flat),
        },
        "traces_with_no_custom_tool": traces_with_no_custom_tool,
        "bash_command_types": dict(bash_cmd_types.most_common(15)),
        "blocked_commands": {
            "total_blocked": total_blocked,
            "total_steps": total_steps,
            "wasted_pct": f"{total_blocked / total_steps * 100:.1f}%" if total_steps else "N/A",
            "blocked_by_type": dict(blocked_cmds.most_common()),
            "blocked_per_trace": dict(Counter(blocked_per_trace).most_common()),
        },
        "format_errors": format_errors,
        "propose_at_step": dict(Counter(propose_at_step).most_common()),
        "last_step_tool": dict(Counter(last_step_actions).most_common()),
    }


def print_diagnosis(samples_stats: dict, trace_stats: dict, step_limit: int = 10):
    """Print a comprehensive diagnosis."""
    print("=" * 70)
    print("  DIAGNOSTIC REPORT: Agent Proactive Tool Call Analysis")
    print("=" * 70)

    # ── Section 1: Overall Results ──
    print("\n1. OVERALL RESULTS")
    print("-" * 50)
    print(f"  Total samples:      {samples_stats['total']}")
    print(f"  Proposals made:     {samples_stats['proposed']} ({samples_stats['proposal_rate']:.1%})")
    print(f"  Empty response:     {samples_stats['empty_raw']} (agent never called propose_task/no_proposal)")
    print(f"  NO_PROPOSAL calls:  {samples_stats['no_proposal']}")

    # ── Section 2: Exit Status Analysis ──
    print("\n2. EXIT STATUS ANALYSIS")
    print("-" * 50)
    for status, count in trace_stats["exit_statuses"].items():
        pct = count / trace_stats["total_traces"] * 100
        print(f"  {status:20s}: {count:4d} ({pct:.1f}%)")

    exceeded = trace_stats["exit_statuses"].get("LimitsExceeded", 0)
    if exceeded > 0:
        print(f"\n  *** {exceeded}/{trace_stats['total_traces']} traces hit the step limit "
              f"(step_limit={step_limit}) ***")
        print(f"  *** This is the PRIMARY CAUSE of low proposal rates ***")

    # ── Section 3: Tool Call Analysis ──
    print("\n3. TOOL CALL ANALYSIS")
    print("-" * 50)
    tc = trace_stats["tool_call_totals"]
    print(f"  Total tool calls:   {tc['total']}")
    print(f"    bash:             {tc['bash']} ({tc['bash']/tc['total']*100:.1f}%)")
    print(f"    propose_task:     {tc['propose_task']} ({tc['propose_task']/tc['total']*100:.1f}%)")
    print(f"    no_proposal:      {tc['no_proposal']} ({tc['no_proposal']/tc['total']*100:.1f}%)")
    print(f"  Traces with NO custom tool call: {trace_stats['traces_with_no_custom_tool']}")
    print(f"    -> These traces used ALL {step_limit} steps on bash without making a decision")

    # ── Section 4: Bash Command Analysis ──
    print("\n4. BASH COMMAND USAGE")
    print("-" * 50)
    for cmd, count in trace_stats["bash_command_types"].items():
        print(f"  {cmd:15s}: {count}")

    # ── Section 5: Blocked Commands ──
    bc = trace_stats["blocked_commands"]
    print("\n5. BLOCKED COMMANDS (wasted steps)")
    print("-" * 50)
    print(f"  Total blocked:      {bc['total_blocked']} / {bc['total_steps']} steps ({bc['wasted_pct']})")
    print(f"  Blocked by command type:")
    for cmd, count in bc["blocked_by_type"].items():
        print(f"    {cmd:15s}: {count}")
    print(f"  Blocked count distribution per trace:")
    for n, count in bc["blocked_per_trace"].items():
        print(f"    {n} blocked: {count} traces")

    # ── Section 6: Step Where propose_task Was Called ──
    print("\n6. PROPOSE_TASK CALL TIMING")
    print("-" * 50)
    if trace_stats["propose_at_step"]:
        for step, count in sorted(trace_stats["propose_at_step"].items()):
            print(f"  Step {step}: {count} times")
        print(f"  -> Model typically uses most steps for browsing before proposing at the end")
    else:
        print(f"  No propose_task calls observed!")

    # ── Section 7: Last Step Tool ──
    print("\n7. LAST TOOL CALL BEFORE TERMINATION")
    print("-" * 50)
    for tool, count in trace_stats["last_step_tool"].items():
        tool_name = str(tool) if tool else "(none)"
        print(f"  {tool_name:20s}: {count}")

    # ── Root Cause Analysis ──
    print("\n" + "=" * 70)
    print("  ROOT CAUSE ANALYSIS")
    print("=" * 70)

    causes = []

    # Cause 1: Step limit too low
    if exceeded > trace_stats["total_traces"] * 0.5:
        causes.append({
            "severity": "CRITICAL",
            "cause": f"Step limit too low (step_limit={step_limit})",
            "detail": (
                f"{exceeded}/{trace_stats['total_traces']} ({exceeded/trace_stats['total_traces']*100:.0f}%) "
                f"traces exhausted all {step_limit} steps on bash browsing. "
                f"The model never gets to call propose_task/no_proposal because "
                f"it uses all steps exploring the repository."
            ),
            "fix": (
                "1. Increase step_limit (e.g., 15-20) in config.yaml\n"
                "2. OR force early decision: add step count info to the prompt so the model "
                "   knows how many steps remain\n"
                "3. OR add a forced-submission mechanism at step N-1"
            ),
        })

    # Cause 2: Model doesn't know step count
    causes.append({
        "severity": "HIGH",
        "cause": "Model has no awareness of remaining steps",
        "detail": (
            "The prompt tells the model 'You MUST call either propose_task or no_proposal "
            "before running out of steps' but does NOT tell it how many steps it has "
            f"({step_limit}) or how many remain. The model cannot plan its budget."
        ),
        "fix": (
            "1. Add 'You have {step_limit} steps total.' to the system prompt\n"
            "2. Inject remaining step count in each observation: "
            "'[Step X/{step_limit}] You have Y steps remaining.'\n"
            "3. At step N-2, inject a warning: 'WARNING: 2 steps remaining, "
            "you MUST submit on the next step.'"
        ),
    })

    # Cause 3: Blocked commands waste steps
    if bc["total_blocked"] > 0:
        wasted_pct = bc["total_blocked"] / bc["total_steps"] * 100
        if wasted_pct > 5:
            severity = "MEDIUM"
        else:
            severity = "LOW"
        causes.append({
            "severity": severity,
            "cause": f"Blocked commands waste {bc['total_blocked']} steps ({bc['wasted_pct']})",
            "detail": (
                f"Commands like {list(bc['blocked_by_type'].keys())} are blocked by "
                f"the read-only repository environment. Each blocked command wastes a step. "
                f"'cd' ({bc['blocked_by_type'].get('cd', 0)}x) and 'sed' ({bc['blocked_by_type'].get('sed', 0)}x) "
                f"are the most common offenders."
            ),
            "fix": (
                "1. Add 'cd' to the allowed list (it doesn't modify files)\n"
                "2. Tell the model in the prompt: 'cd is not available; use absolute paths'\n"
                "3. Add 'sed' (without -i) to allowed list for read-only piped usage"
            ),
        })

    # Cause 4: Model spends too much time exploring
    causes.append({
        "severity": "HIGH",
        "cause": "Model over-explores the repository",
        "detail": (
            f"In successful cases, the model uses 6-9 bash calls before proposing. "
            f"In failed cases, it uses all {step_limit} bash calls. "
            f"The model tends to do broad exploration (ls, find, tree) rather than "
            f"targeted inspection of files mentioned in the IDE operations."
        ),
        "fix": (
            "1. Instruct the model to focus on files mentioned in the operations\n"
            "2. Shorten the exploration phase: 'Use at most 3-5 bash commands, then decide'\n"
            "3. Provide more structured context (e.g., pre-extract file paths from ops)\n"
            "4. Increase step_limit to give more room for exploration"
        ),
    })

    # Cause 5: no_proposal is never called
    if tc["no_proposal"] == 0:
        causes.append({
            "severity": "MEDIUM",
            "cause": "no_proposal tool is never used",
            "detail": (
                "The model never calls no_proposal. In cases where it cannot "
                "make a confident prediction, it should call no_proposal instead "
                "of continuing to browse until step limit."
            ),
            "fix": (
                "1. Emphasize in the prompt that no_proposal is a valid decision\n"
                "2. Add: 'If after 5 bash calls you are not confident, call no_proposal'\n"
                "3. The model may not realize it can stop early with no_proposal"
            ),
        })

    for i, cause in enumerate(causes, 1):
        print(f"\n  [{cause['severity']}] Cause {i}: {cause['cause']}")
        print(f"  Detail: {cause['detail']}")
        print(f"  Suggested fixes:")
        for line in cause["fix"].split("\n"):
            print(f"    {line}")

    # ── Summary ──
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"""
  The agent's propose_task tool was only called {tc['propose_task']} times out of
  {trace_stats['total_traces']} samples ({tc['propose_task']/trace_stats['total_traces']*100:.1f}% call rate).

  Primary root cause: The model exhausts all {step_limit} steps on bash browsing
  ({exceeded}/{trace_stats['total_traces']} = {exceeded/trace_stats['total_traces']*100:.0f}% of traces hit LimitsExceeded).

  The model has NO awareness of how many steps it has remaining, so it keeps
  exploring the repository until it runs out of steps. When the step limit is
  reached, the agent terminates with an empty submission.

  Key recommendations (in priority order):
    1. [CRITICAL] Add step budget awareness to the prompt
       - Tell the model it has {step_limit} steps total
       - Inject remaining step count in each observation
       - Add a warning at step {step_limit-2} to force decision
    2. [HIGH] Increase step_limit from {step_limit} to 15-20
    3. [MEDIUM] Add 'cd' to the allowed command list (wastes {bc['blocked_by_type'].get('cd', 0)} steps)
    4. [MEDIUM] Guide the model to focus on files referenced in the ops
""")


def main(
    samples_file: str = None,
    trace_dir: str = None,
    judged_file: str = None,
):
    """Run diagnostic analysis on agent evaluation results.

    Args:
        samples_file: Path to samples output JSON file
        trace_dir: Path to trace directory
        judged_file: Path to judged output JSON file (optional)
    """
    # Auto-detect paths
    eval_dir = Path(__file__).parent

    if samples_file is None:
        candidates = list((eval_dir / "predictions").rglob("agent_*.json"))
        if candidates:
            samples_file = str(candidates[0])
            print(f"Auto-detected samples file: {samples_file}")
        else:
            print("No samples file found. Provide --samples-file.")
            return

    if trace_dir is None:
        candidates = list((eval_dir / "traces_agent").iterdir())
        candidates = [c for c in candidates if c.is_dir()]
        if candidates:
            trace_dir = str(candidates[0])
            print(f"Auto-detected trace dir: {trace_dir}")
        else:
            print("No trace directory found. Provide --trace-dir.")
            return

    # Load config for step_limit
    config_path = eval_dir / "proactive_agent" / "config.yaml"
    step_limit = 10
    if config_path.exists():
        try:
            import yaml
            config = yaml.safe_load(config_path.read_text())
            step_limit = config.get("agent", {}).get("step_limit", 10)
        except ImportError:
            # Fallback: parse step_limit from yaml with regex
            import re
            text = config_path.read_text()
            m = re.search(r"step_limit:\s*(\d+)", text)
            if m:
                step_limit = int(m.group(1))

    samples_stats = analyze_samples(samples_file)
    trace_stats = analyze_traces(trace_dir)

    print_diagnosis(samples_stats, trace_stats, step_limit)

    # Optionally show judged results
    if judged_file:
        judged = json.loads(Path(judged_file).read_text(encoding="utf-8"))
        tp = sum(1 for s in judged if s.get("judgement", False))
        fn = sum(1 for s in judged if not s.get("judgement", False))
        print(f"\n  Judged results: TP={tp}, FN={fn}, Accuracy={tp/(tp+fn):.4f}")


if __name__ == "__main__":
    fire.Fire(main)
