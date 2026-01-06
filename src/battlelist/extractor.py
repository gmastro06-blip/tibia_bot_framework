import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import List, Dict, Any
import cv2
import numpy as np
import easyocr
from collections import deque
from vision.ocr import robust_ocr_digits

class BattlelistExtractor:
    def __init__(self, row_height: int = 20, n_buffer: int = 10):
        self.row_height = row_height
        self.buffers: Dict[int, deque[Dict[str, Any]]] = {}

    def extract(self, crop: np.ndarray) -> List[Dict[str, Any]]:
        if crop is None or crop.size == 0:
            return []
        h, w = crop.shape[:2]
        num_rows = max(1, h // self.row_height)
        rows: List[Dict[str, Any]] = []
        reader = easyocr.Reader(['en'], gpu=True)
        for i in range(num_rows):
            row_crop = crop[i*self.row_height:(i+1)*self.row_height, :]
            if row_crop.size == 0:
                continue
            ocr_text = ""
            results = reader.readtext(row_crop)
            if results and len(results) > 0:
                ocr_text = results[0][1]
            hp_pct = self._measure_hp_bar(row_crop)
            if i not in self.buffers:
                self.buffers[i] = deque(maxlen=10)
            self.buffers[i].append({"text": ocr_text, "score": 0.9})
            if len(self.buffers[i]) >= 3:
                majority = max(self.buffers[i], key=lambda x: x['score'])['text']
                rows.append({"ocr_text": majority, "hp_pct": hp_pct})
        return rows

    def _measure_hp_bar(self, row_crop: np.ndarray) -> float:
        if row_crop.size == 0:
            return 0.0
        hsv = cv2.cvtColor(row_crop[:, -50:], cv2.COLOR_BGR2HSV)
        mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))
        mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))
        mask = mask1 + mask2
        return cv2.countNonZero(mask) / (row_crop.shape[0] * 50)