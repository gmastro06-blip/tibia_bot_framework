from typing import List, Tuple
import cv2
import numpy as np


def find_contours_rects(
    img: np.ndarray,
    min_area: int = 1000,
    aspect_ratio_range: Tuple[float, float] = (0.5, 2.0)
) -> List[Tuple[int, int, int, int]]:
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


def blob_detect_white(
    img: np.ndarray,
    min_area: int = 10,
    max_area: int = 100
) -> List[Tuple[int, int]]:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, 200]), np.array([180, 50, 255]))
    params = cv2.SimpleBlobDetector_Params()
    params.filterByArea = True
    params.minArea = min_area
    params.maxArea = max_area
    detector = cv2.SimpleBlobDetector_create(params)
    keypoints = detector.detect(mask)
    return [(int(kp.pt[0]), int(kp.pt[1])) for kp in keypoints]


def hsv_segment_bar(img: np.ndarray, color: str = 'red') -> float:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    if color == 'red':
        lower = np.array([0, 70, 50])
        upper = np.array([10, 255, 255])
        mask1 = cv2.inRange(hsv, lower, upper)
        lower = np.array([170, 70, 50])
        upper = np.array([180, 255, 255])
        mask2 = cv2.inRange(hsv, lower, upper)
        mask = mask1 + mask2
    elif color == 'blue':
        mask = cv2.inRange(hsv, np.array([100, 70, 50]), np.array([140, 255, 255]))
    else:
        return 0.0
    fill_ratio = cv2.countNonZero(mask) / (img.shape[0] * img.shape[1])
    return fill_ratio
