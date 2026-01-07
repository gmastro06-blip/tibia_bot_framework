import cv2
from typing import Dict


def draw_debug_overlay(
    frame: cv2.Mat,
    rois: Dict[str, tuple[int, int, int, int]],
    target: str = ""
) -> cv2.Mat:
    for name, rect in rois.items():
        cv2.rectangle(frame, (rect[0], rect[1]),
                      (rect[0]+rect[2], rect[1]+rect[3]), (0, 255, 0), 2)
        cv2.putText(frame, name, (rect[0], rect[1]-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0))
    # Indicador target
    return frame
