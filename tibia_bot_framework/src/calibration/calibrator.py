# src/calibration/calibrator.py - Versión completa corregida (con todos los métodos definidos, calibración auto robusta para HP/MP top)

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import Dict, Tuple
import json
import cv2
import numpy as np
from vision.utils import find_contours_rects, blob_detect_white, hsv_segment_bar

class UICalibrator:
    def __init__(self, guess_norm: Dict):
        self.guess_norm = guess_norm
        self.resolved_rois: Dict[str, Tuple[int, int, int, int]] = {}
        self.calibrated = False

    @classmethod
    def load_profile(cls, path: str) -> 'UICalibrator':
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "configs", path)
        with open(full_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls(data['rois_guess_norm'])

    def calibrate(self, frame: np.ndarray) -> Dict[str, Tuple[int, int, int, int]]:
        if self.calibrated or frame is None or frame.size == 0:
            return self.resolved_rois
        h, w = frame.shape[:2]
        self._auto_hp_mp_top(frame, h, w)
        self._auto_battlelist(frame, h, w)
        self._auto_minimap(frame, h, w)
        self._auto_low_bars(frame, h, w)
        self._save_resolved("ROIs_resueltos.json")
        self.calibrated = True
        print(f"Calibración auto completa: {len(self.resolved_rois)} ROIs")
        return self.resolved_rois

    def _auto_hp_mp_top(self, frame: np.ndarray, h: int, w: int):
        top_strip = frame[0:int(0.15 * h), 0:w]  # Top 15% para números grandes
        gray = cv2.cvtColor(top_strip, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
        upscale = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        dilate = cv2.dilate(upscale, np.ones((3,3), np.uint8))
        import easyocr
        reader = easyocr.Reader(['en'], gpu=False)
        results = reader.readtext(dilate, allowlist='0123456789/', paragraph=False)
        valid = [r for r in results if r[2] > 0.3 and '/' in r[1] and r[1].replace('/', '').isdigit()]
        if valid:
            valid.sort(key=lambda r: min(p[0] for p in r[0]))
            # HP izquierda (primer resultado)
            hp_res = valid[0]
            bbox, text, conf = hp_res
            x = int(min(p[0] for p in bbox) / 2)  # Downscale bbox
            y = int(min(p[1] for p in bbox) / 2)
            ww = int((max(p[0] for p in bbox) - x) / 2)
            hh = int((max(p[1] for p in bbox) - y) / 2)
            self.resolved_rois['hp_top_ocr'] = (x, y, ww, hh)
            print(f"HP top OCR auto-detectado: '{text}' en {self.resolved_rois['hp_top_ocr']}")
            # MP derecha (último resultado)
            if len(valid) > 1:
                mp_res = valid[-1]
                bbox, text, conf = mp_res
                x = int(min(p[0] for p in bbox) / 2)
                y = int(min(p[1] for p in bbox) / 2)
                ww = int((max(p[0] for p in bbox) - x) / 2)
                hh = int((max(p[1] for p in bbox) - y) / 2)
                self.resolved_rois['mp_top_ocr'] = (x, y, ww, hh)
                print(f"MP top OCR auto-detectado: '{text}' en {self.resolved_rois['mp_top_ocr']}")

    def _auto_battlelist(self, frame: np.ndarray, h: int, w: int):
        right = frame[0:h, int(0.8 * w):w]
        rows = find_contours_rects(right, min_area=200.0)
        if rows:
            rows.sort(key=lambda r: r[1])
            y_start = rows[0][1]
            total_h = sum(r[3] for r in rows)
            self.resolved_rois['battlelist_rows'] = (int(0.8 * w), y_start, w - int(0.8 * w), total_h)
            print(f"Battlelist auto-detectado: {self.resolved_rois['battlelist_rows']}")

    def _auto_minimap(self, frame: np.ndarray, h: int, w: int):
        search = frame[0:int(0.4 * h), int(0.6 * w):w]
        candidates = find_contours_rects(search, min_area=1000.0)
        best_score = 0
        best_rect = None
        for cand in candidates:
            crop = search[cand[1]:cand[1]+cand[3], cand[0]:cand[0]+cand[2]]
            edges = cv2.Canny(crop, 100, 200).mean()
            dot = blob_detect_white(crop)
            score = edges + (500 if dot else 0)
            if score > best_score:
                best_score = score
                best_rect = cand
        if best_rect:
            x = int(0.6 * w + best_rect[0])
            y = best_rect[1]
            self.resolved_rois['minimap_content'] = (x, y, best_rect[2], best_rect[3])
            print(f"Minimap auto-detectado: {self.resolved_rois['minimap_content']}")

    def _auto_low_bars(self, frame: np.ndarray, h: int, w: int):
        low_right = frame[int(0.7 * h):h, int(0.9 * w):w]
        red_ratio = hsv_segment_bar(low_right, "red")
        if red_ratio > 0.05:
            self.resolved_rois['hp_low_bar'] = (int(0.9 * w), int(0.7 * h), int(0.1 * w), int(0.05 * h))
            print("HP low bar auto-detectada")

    def _save_resolved(self, path: str = "ROIs_resueltos.json"):
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            json.dump({k: list(v) for k, v in self.resolved_rois.items()}, f, indent=4)