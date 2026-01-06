from __future__ import annotations

from typing import List, Any, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort


class VisionInference:
    def __init__(self, model_path: str, classes: Optional[List[str]] = None, prefer_cuda: bool = True):
        self.classes = classes or []

        providers: List[str] = ["CPUExecutionProvider"]
        if prefer_cuda:
            # OJO: en Windows a veces "aparece" CUDA pero falla por DLLs faltantes.
            # Por eso: try/except al crear sesión.
            if "CUDAExecutionProvider" in ort.get_available_providers():
                providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        try:
            self.sess = ort.InferenceSession(model_path, providers=providers)
        except Exception:
            # fallback silencioso a CPU
            self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])

        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        self.input_shape = inp.shape  # puede contener None / 'batch'

    def _infer_input_hw(self, fallback_hw: Tuple[int, int]) -> Tuple[int, int]:
        # Común NCHW: [N, C, H, W]
        if isinstance(self.input_shape, list) and len(self.input_shape) == 4:
            h = self.input_shape[2]
            w = self.input_shape[3]
            if isinstance(h, int) and isinstance(w, int):
                return (w, h)
        return fallback_hw

    def classify(self, img_bgr: np.ndarray) -> Any:
        w, h = self._infer_input_hw((224, 224))
        pre = cv2.resize(img_bgr, (w, h)).astype(np.float32) / 255.0
        pre = pre[..., ::-1]  # BGR->RGB (muy común)
        x = np.transpose(pre, (2, 0, 1))[np.newaxis, ...]  # NCHW

        out = self.sess.run(None, {self.input_name: x})[0]
        idx = int(np.argmax(out, axis=1)[0]) if out.ndim == 2 else int(np.argmax(out))

        if self.classes and 0 <= idx < len(self.classes):
            return self.classes[idx]
        return idx

    def detect(self, frame_bgr: np.ndarray) -> Any:
        # Solo si tu modelo realmente es detector.
        w, h = self._infer_input_hw((640, 640))
        pre = cv2.resize(frame_bgr, (w, h)).astype(np.float32) / 255.0
        pre = pre[..., ::-1]  # BGR->RGB
        x = np.transpose(pre, (2, 0, 1))[np.newaxis, ...]  # NCHW
        return self.sess.run(None, {self.input_name: x})
