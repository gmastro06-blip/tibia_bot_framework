import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import NoReturn
import threading
from queue import Queue
import time

from capture.dxgi_capture import DXGICapture
from calibration.ui_calibrator import UICalibrator
from vision.inference import VisionInference
from vision.ocr import OCR

from gamestate.builder import GameStateBuilder
from battlelist.extractor import BattlelistExtractor
from bestiary.matcher import BestiaryMatcher
from navigation.navigator import Navigator
from decision.behavior_tree import BehaviorTree
from scripting.engine import ScriptEngine
from actions.executor import ActionExecutor
from safety.manager import SafetyManager
from telemetry.logger import Logger
from telemetry.replay import Replay


def main() -> NoReturn:
    rois_norm = {
        "source_resolution": [2048, 1076],
        "rois_guess_norm": {
            "hpmp_top_strip": {"x": 0.0, "y": 0.0, "w": 0.822754, "h": 0.037174},
            "hp_top_ocr": {"x": 0.052734, "y": 0.0, "w": 0.107422, "h": 0.037174},
            "mp_top_ocr": {"x": 0.568359, "y": 0.0, "w": 0.107422, "h": 0.037174},
            "skills_panel": {"x": 0.822754, "y": 0.0, "w": 0.084961, "h": 0.375464},
            "battlelist_panel": {"x": 0.822754, "y": 0.375464, "w": 0.084961, "h": 0.251860},
            "battlelist_rows": {"x": 0.822754, "y": 0.397769, "w": 0.084961, "h": 0.229554},
            "right_hud_panel": {"x": 0.909668, "y": 0.002788, "w": 0.088867, "h": 0.408921},
            "minimap_content": {"x": 0.915039, "y": 0.004647, "w": 0.052734, "h": 0.104089},
            "equipment_slots": {"x": 0.909668, "y": 0.105020, "w": 0.053711, "h": 0.130111},
            "states_icons": {"x": 0.909668, "y": 0.249072, "w": 0.053711, "h": 0.032527},
            "hpmp_low_panel": {"x": 0.909668, "y": 0.264872, "w": 0.088867, "h": 0.055762},
            "hp_low_bar": {"x": 0.919434, "y": 0.276952, "w": 0.078125, "h": 0.013011},
            "mp_low_bar": {"x": 0.919434, "y": 0.289967, "w": 0.078125, "h": 0.013011},
            "game_viewport": {"x": 0.0, "y": 0.037174, "w": 0.822754, "h": 0.780669},
            "chat_panel": {"x": 0.0, "y": 0.817843, "w": 0.822754, "h": 0.182157},
        },
    }

    calibrator = UICalibrator(rois_norm["rois_guess_norm"], tuple(rois_norm["source_resolution"]))
    capture = DXGICapture("Tibia -")

    # ✅ No intentes CUDA (evita el error de cublasLt64_12.dll)
    vision = VisionInference("models/mobilenet.onnx", classes=[], prefer_cuda=False)

    ocr = OCR()
    battle_extractor = BattlelistExtractor()
    matcher = BestiaryMatcher()
    builder = GameStateBuilder()
    navigator = Navigator((100, 100))
    bt = BehaviorTree()
    script = ScriptEngine()
    executor = ActionExecutor()
    safety = SafetyManager()
    logger = Logger()
    replay = Replay()

    frame_queue: Queue = Queue(maxsize=5)
    gs_queue: Queue = Queue(maxsize=2)

    def capture_thread():
        while True:
            try:
                frame = capture.capture()
                if frame is None:
                    time.sleep(0.01)
                    continue

                if frame_queue.full():
                    frame_queue.get()
                frame_queue.put(frame)
            except Exception as e:
                print("capture_thread error:", repr(e))
                time.sleep(0.1)

            time.sleep(0.01)

    def vision_thread():
        while True:
            if frame_queue.empty():
                time.sleep(0.001)
                continue

            frame = frame_queue.get()
            if frame is None:
                time.sleep(0.001)
                continue

            rois = calibrator.calibrate(frame) if not calibrator.calibrated else calibrator.rois_px

            # ✅ Con mobilenet (clasificación) no hay detecciones/tracking
            tracks = []

            battle_roi = rois["battlelist_rows"]
            battle_crop = frame[
                battle_roi[1] : battle_roi[1] + battle_roi[3],
                battle_roi[0] : battle_roi[0] + battle_roi[2],
            ]
            battle = battle_extractor.extract(battle_crop)
            for b in battle:
                b["resolved"] = matcher.match(b["name"])

            hp_ocr_roi = rois["hp_top_ocr"]
            hp_crop = frame[
                hp_ocr_roi[1] : hp_ocr_roi[1] + hp_ocr_roi[3],
                hp_ocr_roi[0] : hp_ocr_roi[0] + hp_ocr_roi[2],
            ]
            ocr_hp = ocr.read(hp_crop)

            hp_bar_roi = rois["hp_low_bar"]
            bar_hp = calibrator.hsv_segment_bar(
                frame[
                    hp_bar_roi[1] : hp_bar_roi[1] + hp_bar_roi[3],
                    hp_bar_roi[0] : hp_bar_roi[0] + hp_bar_roi[2],
                ],
                "red",
            )

            output = {
                "dets": tracks,
                "battle": battle,
                "ocr_hp": ocr_hp,
                "bar_hp": bar_hp,
                "ocr_mp": "",
                "bar_mp": 0.0,
            }

            gs = builder.build(output)
            if gs_queue.full():
                gs_queue.get()
            gs_queue.put(gs)

            logger.log("vision", {"ms": time.time() * 1000})

            minimap_roi = rois["minimap_content"]
            replay.save_roi(
                "minimap",
                frame[
                    minimap_roi[1] : minimap_roi[1] + minimap_roi[3],
                    minimap_roi[0] : minimap_roi[0] + minimap_roi[2],
                ],
                gs,
                "",
            )

    def decision_thread():
        while True:
            if gs_queue.empty():
                time.sleep(0.001)
                continue

            gs = gs_queue.get()
            if not safety.check_critical(gs, {}):
                safety.panic()
                continue

            script.validate_json("configs/route.json", "route")
            decision = bt.tick(gs)

            logger.log("decision", {"result": decision})

            # Si aquí ejecutas acciones reales, depende de tu implementación.
            # executor.execute(gs)

    threads = [
        threading.Thread(target=capture_thread, daemon=True),
        threading.Thread(target=vision_thread, daemon=True),
        threading.Thread(target=decision_thread, daemon=True),
    ]
    for t in threads:
        t.start()

    safety.watchdog(threads)


if __name__ == "__main__":
    main()
