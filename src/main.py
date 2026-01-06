import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import NoReturn
import threading
from capture.provider import CaptureProvider
from vision.inference import VisionInference
from gamestate.builder import GameStateBuilder
from decision.engine import DecisionEngine
from actions.executor import ActionExecutor
from safety.manager import SafetyManager
from telemetry.logger import TelemetryLogger
from utils.concurrency import FrameQueue, GameStateQueue
from calibration.calibrator import UICalibrator

def main() -> NoReturn:
    calibrator = UICalibrator.load_profile("configs/rois_guess.json")
    capture = CaptureProvider(mode="dxcam", window_title="Tibia - Loterinne")
    vision = VisionInference(gpu=True)
    state_builder = GameStateBuilder()
    decision = DecisionEngine()
    executor = ActionExecutor()
    safety = SafetyManager(kill_key="esc")
    telemetry = TelemetryLogger()
    pause_event = threading.Event()

    frame_queue = FrameQueue(max_size=5)
    state_queue = GameStateQueue(max_size=2)

    def capture_thread() -> None:
        while not pause_event.is_set():
            frame = capture.capture()
            frame_queue.put(frame, drop_oldest=True)
            telemetry.log_metric("capture_fps", capture.get_fps())

    def vision_thread() -> None:
        while not pause_event.is_set():
            frame = frame_queue.get_latest()
            if frame is None:
                continue
            rois = calibrator.calibrate(frame)
            detections = vision.process(frame, rois)
            state = state_builder.build(detections)
            state_queue.put(state)
            telemetry.log_metric("vision_ms", vision.get_latency_ms())

    def decision_thread() -> None:
        while not pause_event.is_set():
            state = state_queue.get_latest()
            if state is None:
                continue
            actions = decision.evaluate(state)
            for action in actions:
                executor.queue_action(action, confirm_callback=vision.confirm_action)
            safety.check_watchdogs(state)
            telemetry.log_state(state)

    threading.Thread(target=capture_thread, daemon=True).start()
    threading.Thread(target=vision_thread, daemon=True).start()
    threading.Thread(target=decision_thread, daemon=True).start()

    safety.monitor(pause_event)  # Bloquea main hasta kill

if __name__ == "__main__":
    main()