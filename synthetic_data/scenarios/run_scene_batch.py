import glob
import os
import sys
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


SCENARIO_DIR = Path(__file__).resolve().parent
SYNTHETIC_DATA_DIR = SCENARIO_DIR.parent
DEFAULT_SCENE_DIR = SCENARIO_DIR / "agent_data"



def run(cfg_file_path,out_file_path):
    cmd = [
        sys.executable,
        "-m",
        "simulator.main",
        "--cfg_file",
        str(Path(cfg_file_path).resolve()),
        "--out_file",
        str(Path(out_file_path).resolve()),
    ]
    if os.environ.get("SETUP_PROACTIVE_AGENT","True") == "True":
        ps = subprocess.run(
            cmd,
            stderr=sys.stderr, stdout=sys.stdout, env=os.environ,
            cwd=SYNTHETIC_DATA_DIR,
            )
    else:
        no_agent_cmd = cmd[:-1] + [str(Path(out_file_path).resolve()).replace(".jsonl", "_noagent.jsonl")]
        ps = subprocess.run(
            no_agent_cmd,
            stderr=sys.stderr, stdout=sys.stdout, env=os.environ,
            cwd=SYNTHETIC_DATA_DIR,
            )
    ps.check_returncode()
    return

if __name__ == "__main__":

    save_path = str(DEFAULT_SCENE_DIR)

    cfg_files = glob.glob(os.path.join(save_path,"*.yaml"))
    cfg_files.sort(key = lambda x: int(x.split("_")[-1].split(".")[0]))
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = []
        for file in cfg_files:

            tasks.append(
                pool.submit(run,file,file.replace(".yaml",".jsonl"))
            )

        for task in as_completed(tasks):
            try:
                task.result()
            except Exception as e:
                print(e)
                continue
