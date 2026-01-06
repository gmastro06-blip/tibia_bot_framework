import json
from ..decision.engine import DecisionEngine  # Relativo
from ..vision.inference import VisionInference  # Relativo

def replay_offline(replay_dir: str) -> None:
    with open(f"{replay_dir}/log.json", 'r') as f:
        logs = [json.loads(line) for line in f]
    vision = VisionInference(gpu=False)
    decision = DecisionEngine()
    for entry in logs:
        if entry['type'] == "state":
            # Reconstruir from ROIs guardados
            rois: dict[str, tuple[int, int, int, int]] = {}  # Anotación
            detections = vision.process(None, rois)  # Mock frame
            actions = decision.evaluate(entry['data'])
            print(f"Replay acción: {actions}")