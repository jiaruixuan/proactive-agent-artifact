import json

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


def format_reward_instruction(obs: list[dict], pred_task: str) -> list[dict]:
    inst_dict = {
        "Observations (Time Ascending)": obs,
        "Proposed Task": pred_task,
        "Instruction": "Now give your judgement. You should complete the reasoning process in first person."
    }
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": json.dumps(inst_dict, sort_keys=False, ensure_ascii=False, indent=4)}
    ]
