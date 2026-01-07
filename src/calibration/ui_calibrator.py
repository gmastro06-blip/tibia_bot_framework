from typing import Dict, Tuple, List, Optional, Any
import cv2
import numpy as np
import json
import os
import re

try:
    import easyocr
except ImportError:
    easyocr = None  # type: ignore

class UICalibrator:
    def __init__(self, rois_guess_norm: Dict[str, Any], source_res: Tuple[int, int]):
        self.rois_norm = rois_guess_norm
        self.source_res = source_res
        self.rois_px: Dict[str, Tuple[int, int, int, int]] = self.normalize_to_px()
        self.reader: Optional[Any] = None
        self.calibrated = False

    def normalize_to_px(self) -> Dict[str, Tuple[int, int, int, int]]:
        rois_px = {}
        for name, norm in self.rois_norm.items():
            x = max(0, int(norm['x'] * self.source_res[0]))
            y = max(0, int(norm['y'] * self.source_res[1]))
            w = min(self.source_res[0] - x, int(norm['w'] * self.source_res[0]))
            h = min(self.source_res[1] - y, int(norm['h'] * self.source_res[1]))
            if w > 0 and h > 0:
                rois_px[name] = (x, y, w, h)
        return rois_px

    def load_reader(self) -> Any:
        if self.reader is None:
            if easyocr is None:
                raise ImportError("easyocr is required for UICalibrator.load_reader()")
            self.reader = easyocr.Reader(['en'], gpu=True)
        return self.reader

    def find_contours_rects(self, img: np.ndarray, min_area: int = 1000, aspect_ratio_range: Tuple[float, float] = (0.5, 2.0)) -> List[Tuple[int, int, int, int]]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rects = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            aspect = w / h if h != 0 else 0
            if area > min_area and aspect_ratio_range[0] <= aspect <= aspect_ratio_range[1]:
                rects.append((x, y, w, h))
        return rects

    def blob_detect_white(self, img: np.ndarray, min_area: int = 10, max_area: int = 100) -> List[Tuple[int, int]]:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 0, 200], dtype=np.uint8), np.array([180, 50, 255], dtype=np.uint8))
        # Use contours to find white blobs instead of SimpleBlobDetector
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if min_area <= area <= max_area:
                M = cv2.moments(cnt)
                if M['m00'] != 0:
                    cx = int(M['m10'] / M['m00'])
                    cy = int(M['m01'] / M['m00'])
                    blobs.append((cx, cy))
        return blobs

    def hsv_segment_bar(self, img: np.ndarray, color: str = 'red') -> float:
        if img is None or img.size == 0:
            return 0.0
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        if color == 'red':
            mask1 = cv2.inRange(hsv, np.array([0, 70, 50], dtype=np.uint8), np.array([10, 255, 255], dtype=np.uint8))
            mask2 = cv2.inRange(hsv, np.array([170, 70, 50], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8))
            mask = cv2.bitwise_or(mask1, mask2)
        elif color == 'blue':
            mask = cv2.inRange(hsv, np.array([100, 70, 50], dtype=np.uint8), np.array([140, 255, 255], dtype=np.uint8))
        else:
            return 0.0
        non_zero = cv2.countNonZero(mask)
        return float(non_zero) / (img.shape[0] * img.shape[1]) if img.size > 0 else 0.0

    def auto_calibrate_minimap(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        if frame is None or frame.size == 0:
            return None
        search_h = int(0.4 * self.source_res[1])
        search_w_start = int(0.6 * self.source_res[0])
        search_region = frame[0:search_h, search_w_start:]
        if search_region.size == 0:
            return None
        rects = self.find_contours_rects(search_region, min_area=5000, aspect_ratio_range=(0.8, 1.2))
        candidates: List[Tuple[Tuple[int, int, int, int], float]] = []
        for x, y, w, h in rects:
            crop = search_region[y:y+h, x:x+w]
            if crop.size == 0:
                continue
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            edges = cv2.Canny(gray, 50, 150)
            edge_density: float = float(np.mean(edges.astype(np.float64))) / 255.0
            dots = self.blob_detect_white(crop)
            score: float = edge_density + (10.0 if any(abs(dx - w/2) < 20 and abs(dy - h/2) < 20 for dx, dy in dots) else 0.0)
            candidates.append(((x + search_w_start, y, w, h), score))
        if candidates:
            return max(candidates, key=lambda c: c[1])[0]
        return self.rois_px.get('minimap_content')

    def calibrate(self, frame: np.ndarray) -> Dict[str, Tuple[int, int, int, int]]:
        if self.calibrated or frame is None or frame.size == 0:
            return self.rois_px
        new_minimap = self.auto_calibrate_minimap(frame)
        if new_minimap:
            self.rois_px['minimap_content'] = new_minimap
        if len(self.rois_px) >= 8:  # Umbral flexible
            self.calibrated = True
            path = os.path.join('data', 'rois_resueltos.json')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump({k: list(v) for k, v in self.rois_px.items()}, f)
        return self.rois_px