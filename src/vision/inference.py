# src/vision/inference.py - Versión con ROI hard-coded preciso + OCR robusto para HP/MP top

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import Dict, List, Any, Tuple
import cv2
import onnxruntime as ort
import easyocr
import time
import numpy as np
from vision.ocr import robust_ocr_digits  # Si tienes, o usa interno
from vision.utils import hsv_segment_bar
from battlelist.extractor import BattlelistExtractor

class VisionInference:
    def __init__(self, gpu: bool = True):
        providers = ['CUDAExecutionProvider'] if gpu else ['CPUExecutionProvider']
        model_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "mobilenet.onnx")
        if os.path.exists(model_path):
            self.ort_session = ort.InferenceSession(model_path, providers=providers)
            self.has_classifier = True
        else:
            self.has_classifier = False
        self.ocr_reader = easyocr.Reader(['en'], gpu=gpu)
        self.battle_extractor = BattlelistExtractor()
        self.latency_ms = 0.0

    def process(self, frame: np.ndarray, rois: Dict[str, Tuple[int, int, int, int]]) -> Dict[str, Any]:
        if frame is None or frame.ndim != 3:
            return {}
        t0 = time.time()
        detections: Dict[str, Any] = {}
        # HP top hard-coded (ajustado a tu imagen "96/180")
        hp_roi = rois.get('hp_top_ocr', (50, 10, 250, 70))  # x, y, w, h izquierda top
        hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
        detections['hp_top'] = self._robust_ocr_numbers(hp_crop) or (0, 0)
        # MP top hard-coded ("85/85")
        mp_roi = rois.get('mp_top_ocr', (1700, 10, 250, 70))  # Derecha top
        mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
        detections['mp_top'] = self._robust_ocr_numbers(mp_crop) or (0, 0)
        # Battlelist
        if 'battlelist_rows' in rois:
            crop = frame[rois['battlelist_rows'][1]:rois['battlelist_rows'][1]+rois['battlelist_rows'][3],
                         rois['battlelist_rows'][0]:rois['battlelist_rows'][0]+rois['battlelist_rows'][2]]
            detections['battlelist'] = self.battle_extractor.extract(crop)
        detections['states'] = []
        self.latency_ms = (time.time() - t0) * 1000
        return detections

    def _robust_ocr_numbers(self, crop: np.ndarray) -> Tuple[int, int] | None:
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        upscale = cv2.resize(thresh, None, fx=8, fy=8, interpolation=cv2.INTER_CUBIC)
        dilate = cv2.dilate(upscale, np.ones((11,11), np.uint8), iterations=4)
        results = self.ocr_reader.readtext(dilate, allowlist='0123456789/', paragraph=False)
        for _, text, conf in results:
            if conf > 0.05 and '/' in text and text.replace('/', '').isdigit():
                parts = text.split('/')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    cur = int(parts[0])
                    max_val = int(parts[1])
                    if 0 <= cur <= max_val:
                        return (cur, max_val)
        return None

    def _classify_icons(self, crop: np.ndarray) -> List[str]:
        if not self.has_classifier or crop.size == 0:
            return []
        input_blob = cv2.resize(crop, (224, 224)).astype(np.float32) / 255.0
        input_blob = np.expand_dims(input_blob.transpose(2, 0, 1), axis=0)
        outputs = self.ort_session.run(None, {'input': input_blob})
        confidences = outputs[0][0]
        pred_idx = np.argmax(confidences)
        classes = ["fire", "poison", "haste", "other"]
        if confidences[pred_idx] > 0.8:
            return [classes[pred_idx]]
        return []

    def confirm_action(self, action_type: str, expected_change: Dict[str, Any]) -> bool:
        return True

    def get_latency_ms(self) -> float:
        return self.latency_ms