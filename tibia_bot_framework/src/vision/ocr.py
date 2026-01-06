# src/vision/ocr.py - Versión OCR robusta para números Tibia (preproceso upscale + dilate)

from typing import Tuple, Optional
import cv2
import easyocr
import numpy as np

def robust_ocr_digits(crop: np.ndarray, reader: easyocr.Reader) -> Optional[Tuple[int, int]]:
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 11, 2)
    upscale = cv2.resize(thresh, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
    dilate = cv2.dilate(upscale, np.ones((9,9), np.uint8), iterations=2)
    results = reader.readtext(dilate, allowlist='0123456789/', paragraph=False)
    for _, text, conf in results:
        if conf > 0.05 and '/' in text and text.replace('/', '').isdigit():
            parts = text.split('/')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                cur = int(parts[0])
                max_val = int(parts[1])
                if 0 <= cur <= max_val:
                    return cur, max_val
    return None