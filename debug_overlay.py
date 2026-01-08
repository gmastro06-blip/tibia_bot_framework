from __future__ import annotations

import cv2
from typing import Dict

class DebugOverlay:
    def draw(self, frame: cv2.Mat, rois: Dict, target: str):
        for name, roi in rois.items():
            cv2.rectangle(frame, (roi[0], roi[1]), (roi[0]+roi[2], roi[1]+roi[3]), (0,255,0), 2)
        cv2.putText(frame, f"Target: {target}", (10,30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,0,0), 2)
        cv2.imshow('Debug', frame)
        cv2.waitKey(1)