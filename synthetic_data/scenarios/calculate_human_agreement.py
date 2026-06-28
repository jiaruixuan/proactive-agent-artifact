import jsonlines
from pathlib import Path

REWARD_DATA_DIR = Path(__file__).resolve().parent / "reward_data"

for filen in [REWARD_DATA_DIR / "test_data.jsonl", REWARD_DATA_DIR / "train_data.jsonl"]:
    with open(filen, "r") as file:
        data = list(jsonlines.Reader(file))
    human_agreement = []
    for item in data:
        human_agreement.extend([i == item["valid"] for i in item["annotation"]])
    print(f"File: {filen}\nHuman Agreement: ", sum(human_agreement) / len(human_agreement))
