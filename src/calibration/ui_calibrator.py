from typing import Dict, Tuple, List, Optional
import cv2
import numpy as np
import easyocr  # type: ignore[import-untyped]
import json
import os
import re

class UICalibrator:
    def __init__(self, rois_guess_norm: Dict, source_res: Tuple[int, int]):
        self.rois_norm = rois_guess_norm
        self.source_res = source_res
        self.rois_px: Dict[str, Tuple[int, int, int, int]] = self.normalize_to_px()
        self.reader: Optional[easyocr.Reader] = None
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

    def load_reader(self) -> easyocr.Reader:
        if self.reader is None:
            self.reader = easyocr.Reader(['en'], gpu=True)
        return self.reader

    def find_contours_rects(self, img: np.ndarray, min_area: int = 1000, aspect_ratio_range: Tuple[float, float] = (0.5, 2.0)) -> List[Tuple[int, int, int, int]]:
        if img is None or img.size == 0:
            return []
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

    def blob_detect_white(self, img: np.ndarray) -> List[Tuple[int, int]]:
        if img is None or img.size == 0:
            return []
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([0, 0, 220]), np.array([180, 30, 255]))  # type: ignore[arg-type]
        mask = cv2.erode(mask, np.ones((3,3)), iterations=1)  # type: ignore[arg-type]
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for cnt in contours:
            if cv2.contourArea(cnt) > 5:
                M = cv2.moments(cnt)
                if M["m00"] != 0:
                    cx = int(M["m10"] / M["m00"])
                    cy = int(M["m01"] / M["m00"])
                    blobs.append((cx, cy))
        return blobs

    def hsv_segment_bar(self, img: np.ndarray, color: str = 'red') -> float:
        if img is None or img.size == 0:
            return 0.0
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        if color == 'red':
            mask1 = cv2.inRange(hsv, np.array([0, 70, 50]), np.array([10, 255, 255]))  # type: ignore[arg-type]
            mask2 = cv2.inRange(hsv, np.array([170, 70, 50]), np.array([180, 255, 255]))  # type: ignore[arg-type]
            mask = cv2.bitwise_or(mask1, mask2)
        elif color == 'blue':
            mask = cv2.inRange(hsv, np.array([100, 70, 50]), np.array([140, 255, 255]))  # type: ignore[arg-type]
        else:
            return 0.0
        return float(cv2.countNonZero(mask)) / (img.shape[0] * img.shape[1])

    def auto_calibrate_minimap(self, frame: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
        if frame is None or frame.size == 0:
            return None
        search_h = int(0.4 * self.source_res[1])
        search_w_start = int(0.6 * self.source_res[0])
        search_region = frame[0:search_h, search_w_start:]
        if search_region.size == 0:
            return None
        rects = self.find_contours_rects(search_region, min_area=5000, aspect_ratio_range=(0.8, 1.2))
        candidates = []
        for x, y, w, h in rects:
            crop = search_region[y:y+h, x:x+w]
            if crop.size == 0:
                continue
            canny = cv2.Canny(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), 50, 150)
            edge_density = float(np.mean(canny.astype(np.float32))) / 255.0
            dots = self.blob_detect_white(crop)
            score = edge_density + (20 if dots else 0)
            candidates.append(((x + search_w_start, y, w, h), score))
        if candidates:
            best = max(candidates, key=lambda c: c[1])[0]
            return best
        return None

    def detect_player_dot(self, minimap_roi: np.ndarray) -> Tuple[int, int]:
        blobs = self.blob_detect_white(minimap_roi)
        if blobs:
            return blobs[0]
        return (minimap_roi.shape[1] // 2, minimap_roi.shape[0] // 2)

    def assisted_calibrate(self, frame: np.ndarray) -> None:
        rois = {}
        def mouse_callback(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN:
                param['tl'] = (x, y)
            elif event == cv2.EVENT_LBUTTONUP:
                param['br'] = (x, y)
                rois[param['name']] = (param['tl'][0], param['tl'][1], param['br'][0] - param['tl'][0], param['br'][1] - param['tl'][1])
        cv2.namedWindow('Calibrate')
        cv2.imshow('Calibrate', frame)
        for name in self.rois_norm.keys():
            param = {'name': name, 'tl': None, 'br': None}
            cv2.setMouseCallback('Calibrate', mouse_callback, param)
            cv2.waitKey(0)
        cv2.destroyAllWindows()
        with open('data/rois_resueltos.json', 'w') as f:
            json.dump(rois, f)

    def check_drift(self, frame: np.ndarray, roi_name: str) -> float:
        roi = self.rois_px[roi_name]
        crop = frame[roi[1]:roi[1]+roi[3], roi[0]:roi[0]+roi[2]]
        baseline_path = f'data/baseline_{roi_name}.json'
        if os.path.exists(baseline_path):
            with open(baseline_path, 'r') as f:
                baseline_hist = np.array(json.load(f))
            hist = cv2.calcHist([crop], [0], None, [256], [0, 256])
            score = cv2.compareHist(hist, baseline_hist, cv2.HISTCMP_BHATTACHARYYA)
            return score
        return 0.0

    def validate_roi(self, frame: np.ndarray, roi_name: str) -> bool:
        roi = self.rois_px[roi_name]
        crop = frame[roi[1]:roi[1]+roi[3], roi[0]:roi[0]+roi[2]]
        if roi_name == 'minimap_content':
            dots = self.blob_detect_white(crop)
            return bool(dots)
        elif roi_name in ['hp_top_ocr', 'mp_top_ocr']:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            text = self.load_reader().readtext(thresh, allowlist='0123456789/', detail=0)
            if text:
                text = ''.join(text)
                match = re.match(r'(\d+)/(\d+)', text)
                if match and int(match.group(1)) <= int(match.group(2)):
                    return True
            return False
        elif roi_name in ['hp_low_bar', 'mp_low_bar']:
            color = 'red' if 'hp' in roi_name else 'blue'
            fill = self.hsv_segment_bar(crop, color)
            return 0.1 < fill < 1.0
        elif roi_name == 'battlelist_rows':
            texts = [self.load_reader().readtext(crop[i:i+20, :], detail=0) for i in range(0, crop.shape[0], 20)]
            return any(len(t) > 0 for t in texts)
        return False

    def refine_roi(self, frame: np.ndarray, roi_name: str) -> Tuple[int, int, int, int]:
        roi = self.rois_px[roi_name]
        expand_factor = 1.2
        exp_x = max(0, roi[0] - int(roi[2] * 0.1))
        exp_y = max(0, roi[1] - int(roi[3] * 0.1))
        exp_w = min(self.source_res[0] - exp_x, int(roi[2] * expand_factor))
        exp_h = min(self.source_res[1] - exp_y, int(roi[3] * expand_factor))
        exp_crop = frame[exp_y:exp_y+exp_h, exp_x:exp_x+exp_w]
        new_rect = self.auto_calibrate_minimap(exp_crop) if 'minimap' in roi_name else roi
        return new_rect or roi

    def calibrate(self, frame: np.ndarray) -> Dict[str, Tuple[int, int, int, int]]:
        if self.calibrated or frame is None or frame.size == 0:
            return self.rois_px
        new_minimap = self.auto_calibrate_minimap(frame)
        if new_minimap is not None:
            self.rois_px['minimap_content'] = new_minimap
        right_region = frame[:, int(0.8 * frame.shape[1]):]
        rects = self.find_contours_rects(right_region, min_area=2000)
        if rects:
            rects.sort(key=lambda r: r[1])
            y_start = rects[0][1]
            total_h = sum(r[3] for r in rects[:10])
            self.rois_px['battlelist_rows'] = (int(0.8 * frame.shape[1]), y_start, frame.shape[1] - int(0.8 * frame.shape[1]), total_h)
        top_region = frame[0:int(0.1 * frame.shape[0]), :]
        hp_rects = self.find_contours_rects(top_region, min_area=200)
        if hp_rects:
            hp_rects.sort(key=lambda r: r[0])
            self.rois_px['hp_top_ocr'] = hp_rects[0] if len(hp_rects) > 0 else self.rois_px.get('hp_top_ocr', (0, 0, 100, 50))
            self.rois_px['mp_top_ocr'] = hp_rects[-1] if len(hp_rects) > 1 else self.rois_px.get('mp_top_ocr', (0, 0, 100, 50))
        if all(k in self.rois_px for k in ['minimap_content', 'battlelist_rows', 'hp_top_ocr', 'hp_low_bar']):
            self.calibrated = True
            os.makedirs('data', exist_ok=True)
            with open('data/rois_resueltos.json', 'w') as f:
                json.dump({k: list(v) for k, v in self.rois_px.items()}, f)
        return self.rois_px