import json
from ..decision.engine import DecisionEngine
from ..vision.inference import VisionInference

def replay_offline(replay_dir: str) -> None:
    with open(f"{replay_dir}/log.json", 'r') as f:
        logs = [json.loads(line) for line in f]
    vision = VisionInference(gpu=False)
    decision = DecisionEngine()
    for entry in logs:
        if entry['type'] == "state":
            rois: dict[str, tuple[int, int, int, int]] = {}
            detections = vision.process(None, rois)
            actions = decision.evaluate(entry['data'])
            print(f"Replay acción: {actions}")