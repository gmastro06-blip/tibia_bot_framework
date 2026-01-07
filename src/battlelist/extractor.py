from typing import List, Dict, Union
import cv2
import numpy as np
from vision.ocr import OCR
from collections import deque

class BattlelistExtractor:
    def __init__(self, row_height: int = 20, num_rows: int = 10):
        self.row_height = row_height
        self.buffer: Dict[int, deque[str]] = {i: deque(maxlen=10) for i in range(num_rows)}

    def segment_rows(self, battlelist_roi: Union[cv2.Mat, np.ndarray]) -> List[Union[cv2.Mat, np.ndarray]]:
        rows = []
        for i in range(0, battlelist_roi.shape[0], self.row_height):
            row = battlelist_roi[i:i+self.row_height, :]
            rows.append(row)
        return rows[:len(self.buffer)]

    def extract(self, battlelist_roi: Union[cv2.Mat, np.ndarray]) -> List[Dict]:
        rows = self.segment_rows(battlelist_roi)
        entities = []
        ocr = OCR()
        for idx, row in enumerate(rows):
            # Ensure row is uint8 for OCR
            row_uint8 = row.astype(np.uint8) if isinstance(row, np.ndarray) else row
            text = ocr.read(row_uint8, whitelist='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ -')
            self.buffer[idx].append(text)
            majority = max(set(self.buffer[idx]), key=self.buffer[idx].count) if self.buffer[idx] else ''
            if self.buffer[idx].count(majority) >= 3 and len(majority) > 2:
                hp_bar = self.measure_hp_bar(row)
                entities.append({'name': majority, 'hp_pct': hp_bar, 'row_idx': idx})
        return entities

    def measure_hp_bar(self, row: Union[cv2.Mat, np.ndarray]) -> float:
        hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
        # Red hue detection (wraps around: 0-10 and 170-180)
        mask_red_low = cv2.inRange(hsv, np.array([0, 70, 50], dtype=np.uint8), np.array([10, 255, 255], dtype=np.uint8))
        mask_red_high = cv2.inRange(hsv, np.array([170, 70, 50], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8))
        mask_red = cv2.bitwise_or(mask_red_low, mask_red_high)
        non_zero = cv2.countNonZero(mask_red)
        total_pixels = row.shape[0] * row.shape[1]
        return float(non_zero) / total_pixels if total_pixels > 0 else 0.0