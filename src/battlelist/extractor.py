from typing import List, Dict
import cv2
from vision.ocr import OCR
from collections import deque

class BattlelistExtractor:
    def __init__(self, row_height: int = 20, num_rows: int = 10):
        self.row_height = row_height
        self.buffer: Dict[int, deque[str]] = {i: deque(maxlen=10) for i in range(num_rows)}

    def segment_rows(self, battlelist_roi: cv2.Mat) -> List[cv2.Mat]:
        rows = []
        for i in range(0, battlelist_roi.shape[0], self.row_height):
            row = battlelist_roi[i:i+self.row_height, :]
            rows.append(row)
        return rows[:len(self.buffer)]

    def extract(self, battlelist_roi: cv2.Mat) -> List[Dict]:
        rows = self.segment_rows(battlelist_roi)
        entities = []
        ocr = OCR()
        for idx, row in enumerate(rows):
            text = ocr.read(row, whitelist='abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ -')
            self.buffer[idx].append(text)
            majority = max(set(self.buffer[idx]), key=self.buffer[idx].count) if self.buffer[idx] else ''
            if self.buffer[idx].count(majority) >= 3 and len(majority) > 2:
                hp_bar = self.measure_hp_bar(row)
                entities.append({'name': majority, 'hp_pct': hp_bar, 'row_idx': idx})
        return entities

    def measure_hp_bar(self, row: cv2.Mat) -> float:
        hsv = cv2.cvtColor(row, cv2.COLOR_BGR2HSV)
        mask_red = cv2.inRange(hsv, (0,70,50), (10,255,255)) + cv2.inRange(hsv, (170,70,50), (180,255,255))
        fill = cv2.countNonZero(mask_red) / (row.shape[1] * row.shape[0])
        return fill