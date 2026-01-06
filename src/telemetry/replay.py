import json
import cv2, time, GameState
from decision.behavior_tree import BehaviorTree

class Replay:
    def save_roi(self, roi_name: str, crop: cv2.Mat, gamestate: GameState, action: str):
        ts = time.time()
        cv2.imwrite(f'logs/rois/{ts}_{roi_name}.png', crop)
        with open(f'logs/replay/{ts}.json', 'w') as f:
            json.dump({'gamestate': vars(gamestate), 'action': action}, f)

    def offline_replay(self, log_dir: str):
        bt = BehaviorTree()
        for file in sorted(os.listdir(log_dir)):
            if file.endswith('.json'):
                with open(os.path.join(log_dir, file), 'r') as f:
                    data = json.load(f)
                gs = GameState(**data['gamestate'])
                decision = bt.tick(gs)
                print(f"Replay: {decision}")
    # Código anterior + event-based save
    def save_on_event(self, event: str):
        if event == 'critical':
            self.save_roi(...)