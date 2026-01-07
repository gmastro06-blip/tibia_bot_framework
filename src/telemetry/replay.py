from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Union

import cv2
import numpy as np
from decision.behavior_tree import BehaviorTree

if TYPE_CHECKING:
    from gamestate.state import GameState  # ajusta si GameState está en otro módulo


class Replay:
    def __init__(self, base_dir: str = "logs"):
        self.base_dir = base_dir
        self.rois_dir = os.path.join(base_dir, "rois")
        self.replay_dir = os.path.join(base_dir, "replay")
        os.makedirs(self.rois_dir, exist_ok=True)
        os.makedirs(self.replay_dir, exist_ok=True)

    def save_roi(self, roi_name: str, crop: np.ndarray, gamestate: GameState, action: str) -> None:
        ts = time.time()
        img_path = os.path.join(self.rois_dir, f"{ts}_{roi_name}.png")
        json_path = os.path.join(self.replay_dir, f"{ts}.json")

        cv2.imwrite(img_path, crop)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"gamestate": vars(gamestate), "action": action}, f, ensure_ascii=False)

    def offline_replay(self, log_dir: str) -> None:
        # Import local para evitar ciclos y para que exista GameState en runtime
        from gamestate.state import GameState  # ajusta si hace falta

        bt = BehaviorTree()
        for file in sorted(os.listdir(log_dir)):
            if not file.endswith(".json"):
                continue
            with open(os.path.join(log_dir, file), "r", encoding="utf-8") as f:
                data = json.load(f)

            gs = GameState(**data["gamestate"])
            decision = bt.tick(gs)
            print(f"Replay: {decision}")

    def save_on_event(self, event: str) -> None:
        raise NotImplementedError("Define qué ROI/acción guardar cuando ocurra el evento.")
