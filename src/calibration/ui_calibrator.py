from typing import Dict, Tuple, List, Optional
import cv2
import numpy as np
import easyocr
import json
import os
import re

class UICalibrator:
    def __init__(self, rois_guess_norm: Dict, source_res: Tuple[int, int]):
        self.rois_norm = rois_guess_norm
        self.source_res = source_res
        self.rois_px: Dict[str, Tuple[int, int, int, int]] = self.normalize_to_px()
        self.reader: Optional[easyocr.Reader] = None
        self.calibrated = False  # Inicializa aquí

    # Resto del código igual...
    def calibrate(self, frame: np.ndarray) -> Dict[str, Tuple[int, int, int, int]]:
        if self.calibrated or frame is None or frame.size == 0:
            return self.rois_px
        # Lógica de calibración...
        self.calibrated = True  # Set al final
        return self.rois_px

    # Otras funciones...