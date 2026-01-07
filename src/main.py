import sys
import os
import pyautogui
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Optional, Dict, List
import threading
from queue import Queue
import time
import numpy as np
from capture.dxgi_capture import DXGICapture
from calibration.ui_calibrator import UICalibrator
from vision.inference import VisionInference
from gamestate.builder import GameState, GameStateBuilder
from battlelist.extractor import BattlelistExtractor
from bestiary.matcher import BestiaryMatcher
from navigation.navigator import Navigator
from decision.behavior_tree import BehaviorTree
from action.executor import ActionExecutor
from safety.manager import SafetyManager
from telemetry.replay import Replay


def main() -> None:
    rois_guess_norm: Dict[str, Dict[str, float]] = {
            "hpmp_top_strip": {"x": 0.000000, "y": 0.000000, "w": 0.822754, "h": 0.037174},
            "hp_top_ocr": {"x": 0.052734, "y": 0.000000, "w": 0.107422, "h": 0.037174},
            "mp_top_ocr": {"x": 0.568359, "y": 0.000000, "w": 0.107422, "h": 0.037174},
            "skills_panel": {"x": 0.822754, "y": 0.000000, "w": 0.084961, "h": 0.375464},
            "battlelist_panel": {"x": 0.822754, "y": 0.375464, "w": 0.084961, "h": 0.251860},
            "battlelist_rows": {"x": 0.822754, "y": 0.397769, "w": 0.084961, "h": 0.229554},
            "right_hud_panel": {"x": 0.909668, "y": 0.002788, "w": 0.088867, "h": 0.408921},
            "minimap_content": {"x": 0.915039, "y": 0.004647, "w": 0.052734, "h": 0.104089},
            "equipment_slots": {"x": 0.909668, "y": 0.105020, "w": 0.053711, "h": 0.130111},
            "states_icons": {"x": 0.909668, "y": 0.249072, "w": 0.053711, "h": 0.032527},
            "hpmp_low_panel": {"x": 0.909668, "y": 0.264872, "w": 0.088867, "h": 0.055762},
            "hp_low_bar": {"x": 0.919434, "y": 0.276952, "w": 0.078125, "h": 0.013011},
            "mp_low_bar": {"x": 0.919434, "y": 0.289967, "w": 0.078125, "h": 0.013011},
            "game_viewport": {"x": 0.000000, "y": 0.037174, "w": 0.822754, "h": 0.780669},
            "chat_panel": {"x": 0.000000, "y": 0.817843, "w": 0.822754, "h": 0.182157}
        }
    source_resolution: List[int] = [2048, 1076]
    calibrator = UICalibrator(rois_guess_norm, (source_resolution[0], source_resolution[1]))
    capture = DXGICapture("Tibia -")
    vision = VisionInference("models/yolo.onnx", ["classes"])
    # OCR already initialized in VisionInference.__init__
    # tracker = ByteTrack()  # Initialized but not used yet
    battle_extractor = BattlelistExtractor()
    matcher = BestiaryMatcher()
    builder = GameStateBuilder()
    navigator = Navigator((100, 100))
    bt = BehaviorTree()
    # script = ScriptEngine()  # Initialized but not used yet
    executor = ActionExecutor()
    safety = SafetyManager()
    # logger = Logger()  # Initialized but not used yet
    replay = Replay()

    frame_queue: Queue[Optional[np.ndarray]] = Queue(maxsize=5)
    gs_queue: Queue[GameState] = Queue(maxsize=2)
    minimap_cache: list = [None]  # Shared minimap crop for navigator

    def capture_thread() -> None:
        while True:
            frame = capture.capture()
            if frame is not None and frame.size > 0:
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                    except Exception:
                        pass
                frame_queue.put(frame)
            time.sleep(0.01)

    def vision_thread() -> None:
        while True:
            try:
                if frame_queue.empty():
                    time.sleep(0.01)
                    continue
                frame = frame_queue.get()
                if frame is None or frame.size == 0:
                    continue
                rois = calibrator.calibrate(frame)
                if not rois:
                    continue

                def safe_crop(roi_name: str) -> Optional[np.ndarray]:
                    if roi_name not in rois:
                        return None
                    x, y, w, h = rois[roi_name]
                    if w <= 0 or h <= 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
                        return None
                    return frame[y:y+h, x:x+w]

                battle_crop = safe_crop('battlelist_rows')
                if battle_crop is None or battle_crop.size == 0:
                    continue
                # BattlelistExtractor.extract expects cv2.Mat compatible input (numpy arrays work)
                battle = battle_extractor.extract(battle_crop)
                for b in battle:
                    b['resolved'] = matcher.match(b['name'])

                hp_crop = safe_crop('hp_top_ocr')
                ocr_hp = ''
                if hp_crop is not None:
                    ocr_hp = vision.ocr.read(hp_crop.astype(np.uint8))

                hp_bar_crop = safe_crop('hp_low_bar')
                bar_hp = (calibrator.hsv_segment_bar(hp_bar_crop, 'red')
                          if hp_bar_crop is not None else 0.0)

                minimap_crop = safe_crop('minimap_content')
                player_pos = (0, 0)
                if minimap_crop is not None:
                    detected = calibrator.blob_detect_white(minimap_crop)
                    if detected:
                        player_pos = detected[0]
                minimap_cache[0] = minimap_crop  # Store for navigator use

                output = {'battle': battle, 'ocr_hp': ocr_hp, 'bar_hp': bar_hp, 'player_pos': player_pos}
                gs = builder.build(output)
                if gs_queue.full():
                    try:
                        gs_queue.get_nowait()
                    except Exception:
                        pass
                gs_queue.put(gs)

                if minimap_crop is not None:
                    replay.save_roi('minimap', minimap_crop, gs, '')
            except Exception as e:
                print(f"Vision error: {e}")

    navigator.load_waypoints('configs/route.json')

    def decision_thread() -> None:
        while True:
            try:
                if gs_queue.empty():
                    time.sleep(0.01)
                    continue
                gs = gs_queue.get()
                if not safety.check_critical(gs, {}):
                    safety.panic()
                    continue
                minimap_for_nav = (minimap_cache[0] if minimap_cache[0] is not None
                                   else np.zeros((100, 100, 3), dtype=np.uint8))
                navigator.update(gs.player_pos, minimap_for_nav)
                move_dir = navigator.get_next_move()
                if move_dir != 'wait':
                    pyautogui.press(move_dir)
                actions = bt.tick(gs)
                for act in actions:
                    if callable(act):
                        executor.add_action(act, [], timeout=2.0)
                    else:
                        print(f"Warning: action {act} is not callable")
                executor.execute(gs)
            except Exception as e:
                print(f"Decision error: {e}")

    threads = [
        threading.Thread(target=capture_thread, daemon=True),
        threading.Thread(target=vision_thread, daemon=True),
        threading.Thread(target=decision_thread, daemon=True)
    ]
    for t in threads:
        t.start()
    safety.watchdog(threads)


if __name__ == "__main__":
    main()
