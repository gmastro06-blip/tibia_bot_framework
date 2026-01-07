# Vision utility functions - Complete corrected version (explicit cast for mask)

import cv2
import numpy as np
from typing import List, Tuple


def find_contours_rects(
    img: np.ndarray,
    min_area: float = 100.0
) -> List[Tuple[int, int, int, int]]:
    if img.size == 0:
        return []
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects: List[Tuple[int, int, int, int]] = []
    for c in contours:
        area = cv2.contourArea(c)
        if area > min_area:
            x, y, w, h = cv2.boundingRect(c)
            rects.append((x, y, w, h))
    return rects


def blob_detect_white(img: np.ndarray) -> bool:
    if img.size == 0:
        return False
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 200]), np.array([180, 50, 255]))  # type: ignore[arg-type]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        area = cv2.contourArea(c)
        if 5 < area < 100:
            return True
    return False


def hsv_segment_bar(crop: np.ndarray, color: str = "red") -> float:
    if crop.size == 0:
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    if color == "red":
        lower1 = np.array([0, 100, 100], dtype=np.uint8)
        upper1 = np.array([10, 255, 255], dtype=np.uint8)
        lower2 = np.array([170, 100, 100], dtype=np.uint8)
        upper2 = np.array([180, 255, 255], dtype=np.uint8)
        mask1 = cv2.inRange(hsv, lower1, upper1)  # type: ignore[arg-type]
        mask2 = cv2.inRange(hsv, lower2, upper2)  # type: ignore[arg-type]
        mask = cv2.bitwise_or(mask1, mask2)  # Mejor que + para evitar overflow
    elif color == "blue":
        lower = np.array([100, 100, 100], dtype=np.uint8)
        upper = np.array([140, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)  # type: ignore[arg-type]
    else:
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)

    # Cast explícito a uint8 para mypy/OpenCV countNonZero
    mask = mask.astype(np.uint8)
    filled = cv2.countNonZero(mask)
    total_pixels = crop.shape[0] * crop.shape[1]
    return filled / total_pixels if total_pixels > 0 else 0.0


def validate_roi(
    frame: np.ndarray,
    rect: Tuple[int, int, int, int],
    name: str
) -> bool:
    crop = frame[rect[1]:rect[1]+rect[3], rect[0]:rect[0]+rect[2]]
    if crop.size == 0:
        return False
    if name == "minimap_content":
        return blob_detect_white(crop) and cv2.Canny(crop, 100, 200).mean() > 10
    return True
