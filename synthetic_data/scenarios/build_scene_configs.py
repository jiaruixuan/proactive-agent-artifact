import asyncio
import yaml
import os
import re
import random
import json
import fire
import tqdm
from pathlib import Path
from codelinker import CodeLinker, CodeLinkerConfig

SCENARIO_DIR = Path(__file__).resolve().parent
SYNTHETIC_DATA_DIR = SCENARIO_DIR.parent

cfg_file = SYNTHETIC_DATA_DIR / "private.toml"
cl = CodeLinker(CodeLinkerConfig.from_toml(cfg_file))

example_events = []
save_path = str(SCENARIO_DIR / "agent_data")
testset_dir = str(SCENARIO_DIR / "test_data")


SYSTEM = """<Task>
You are tasked to generate realistic scenarios where a user might need assistance from an AI assistant. Always remember to keep the scene realistic and believable by including as much details as possible.
</Task>

<Rule>
- You will iteratively generate more information about the scene. Make sure each time you add a new detail, it is consistent with the previous details. Always generate new content based on the previous generated content.
- You can add as many details as you want, but make sure they are consistent with the previous details.
- Try to generate diverse details about the scene. You will be tasked to simulate events in the scene later.
</Rule>"""

sem = asyncio.Semaphore(4)

async def update_inst(inst, messages:list, tempurature=1.0):
    messages.append({"role": "user", "content": inst})
    async with sem:
        res = await cl.exec(
            messages=messages,
            completions_kwargs={
                "temperature": tempurature,
            }
        )
    messages.append({"role": "assistant", "content": res})
    return messages


async def forward(scene, seed_task, sample_events):
    messages = [{"role": "system", "content": SYSTEM}]

    init_inst = f"""## Scene Description Generation
User Profile: Describe the user's expertise level, preferences, and specific interests or habits.
Context: Detail the user's current environment or situation, including location, time of day, and tasks they are engaged in.
Behavioral Patterns: Note any repetitive tasks or queries the user typically performs.
Assistance Cues: Identify signals that suggest the user might need help.
Potential Tasks: List tasks the AI might assist with, aligned with the user's preferences and expertise.
Scene: {scene}
Seed Task: {seed_task}

Generate diverse descriptions of the scene and seed task based on these aspects. Be creative and detailed.
"""
    messages = await update_inst(init_inst, messages)

    task_inst = """Now you should first generate the tasks that user are going to perform and user characters like age, job, name, skills, etc. You will receive a seed task to start with, and you should generate different tasks and characters based on the seed task.\n\nSeed Task: """ + seed_task

    messages = await update_inst(task_inst, messages)

    entity_inst = """Now you should generate the entities that are involved in the scene. You should think about the following aspects:
1. Is there any specific object that the user interacts with?
2. Are there any other entities that may be used or affected by the user's actions when performing the tasks?
3. Are there any other entities that may influence the user's behavior or decisions?

You should generate diverse descriptions of the entities involved in the scene based on the above aspects. You are encouraged to be creative and detailed in your descriptions."""
    messages = await update_inst(entity_inst, messages)

    agent_inst = """Now you should generate what the AI assistant can do in the scene. Think about if the agent can read text and images, if it can generate text. You will asked to implement the agent's available actions. Here are some examples of the agent's actions:
Agent can operate terminal, file system and web browser.
Examples like:
- terminal_run(command)
- file.open(file_path)
- file.save(file_path, content)
- browser.open(url)

These example lacks details and essential descriptions of its parameters, return values, and functions. You should provide more detailed descriptions of the agent's actions based on the above examples.
Now generate diverse descriptions of the agent's actions space based on the above aspects. You are encouraged to be creative and detailed in your descriptions. Make sure all the action is implementable and realistic for a text-and-vision model based agent."""

    messages = await update_inst(agent_inst, messages)

    event_inst = """Now you should generate at least 10 example events that could happen in the scene. Focus on the events that are related to the **user or agent's actions**. You should think about the following aspects:

---
1. As real world could be complex, you should generate events that are diverse and detailed. Also include possible failures or unexpected events.
2. You should generate events that are related to the user's tasks and the agent's actions.
3. There are no limitations on the types of events you can generate, but make sure they are consistent with the scene you have described.
4. Focus on the events' subjects, only generate objective events without subjective opinions or emotions.
5. Make sure the event is detailed and very specific, and it should be consistent with the previous events.
6. You should describe the events in a clear and concise manner. Do not write code or pseudo code.
---

Important Notes:
Watch the given samples, sometimes you should generate meaningless actions like the some samples shows.
Make sure generate as much events as possible. Make each event is in same granularity as samples.
MAKE SURE EACH EVENT IS SHORT AND SPECIFIC. DONOT PUT MULTIPLE EVENTS IN ONE DESCRIPTION.

WARNING: Do not include any inferential comprehension in the events. The events should be objective and specific.

MAKE SURE YOUR EVENTS LIKE THE FOLLOWING:
- The agent opens the browser and search for tutoriols for Java.
- The agent opens the file `example.cpp` and identifies severl bugs.
- The agent reads received e-mails and schedule a meeting for user.
""" + '\n- '.join(sample_events)


    messages = await update_inst(event_inst, messages)



    structure_inst = """Now you should summarize and structure the content you have generated in JSON syntax as follow:\n"""+json.dumps(
        {
            "environment": {
                "theme": "a short term to describe the scene",
                "description": "a detailed description of the scene",
                "entities": "a list of entities involved in the scene",
                "agent_ops": "a list of agent's available actions",
                "events_example": [
                    "a list of example events that could happen in the scene",
                    "include all events you generated",
                ],
            },
            "user": {
                "goal": "The task user are going to perform in the scene",
                "description": "a detailed description of the user",
            }
        }
    )+"\n\nGenerate exactly ONE scene config as a single JSON object (not an array). Make sure it is consistent and logically correct, contains enough details, and is well-structured. Tidy all the content you have generated as detailed as possible."
    messages = await update_inst(structure_inst, messages, tempurature=0.3)

    content = messages[-1]['content']
    # 
    try:
        result = json.loads(content)
        #  LLM 
        if isinstance(result, list):
            return result[0] if result else {}
        return result
    except:
        pass
    #  ```json 
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)\n\s*```", content, re.DOTALL)
    for block in blocks:
        try:
            result = json.loads(block)
            if isinstance(result, list):
                return result[0] if result else {}
            return result
        except:
            continue
    #  { ... }
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group())
            if isinstance(result, list):
                return result[0] if result else {}
            return result
        except:
            pass
    print(f"[warn] JSON 300: {content[:300]}")
    return {}


async def main(seedfile: str, savefile: str):
    with open(seedfile, "r", encoding = "utf-8") as f:
        seeds = yaml.safe_load(f)
    tasks = []
    for scene, seed_tasks in seeds.items():
        n_events = min(15, len(seed_tasks["sample_events"]))
        for task in seed_tasks["tasks"]:
            tasks.append(asyncio.create_task(forward(scene, task, random.sample(seed_tasks["sample_events"], n_events))))

    results = []
    for t in tqdm.tqdm(asyncio.as_completed(tasks,),total=len(tasks),ncols=100):
        try:
            ret = await t
            if isinstance(ret, dict) and ret:
                results.append(ret)
            elif isinstance(ret, list):
                results.extend(ret)
            with open(savefile, "w") as f:
                yaml.dump(results, f)
        except:
            import traceback
            traceback.print_exc()

    scenes = results

    os.makedirs(save_path, exist_ok=True)
    for idx,settings in enumerate(scenes):
        out_file_path = os.path.join(save_path,"scene_{}.jsonl".format(idx))
        cfg_file_path = os.path.join(save_path,"scene_{}.yaml".format(idx))
        settings['agent'] = {
        }
        settings['user']['theme'] = settings['environment']['theme']
        config = {
            "eventSink": {
                "out_file": out_file_path,
            },
            **settings
        }
        # write config to file
        with open(cfg_file_path, 'w') as f:
            yaml.dump(config, f)

if __name__ == "__main__":
    fire.Fire(main)
