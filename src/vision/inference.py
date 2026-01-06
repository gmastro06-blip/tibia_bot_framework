from __future__ import annotations

from typing import List, Any, Optional
import cv2
import numpy as np
import onnxruntime as ort


class VisionInference:
    def __init__(self, model_path: str, classes: Optional[List[str]] = None, prefer_cuda: bool = True):
        providers = ["CPUExecutionProvider"]
        if prefer_cuda:
            # Si onnxruntime-gpu está bien instalado, CUDAExecutionProvider funcionará
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        self.sess = ort.InferenceSession(model_path, providers=providers)

        inp = self.sess.get_inputs()[0]
        self.input_name = inp.name
        self.input_shape = inp.shape  # puede contener None / 'batch'
        self.classes = classes or []

    def _infer_input_hw(self, fallback_hw=(224, 224)) -> tuple[int, int]:
        # Espera normalmente NCHW: [N, C, H, W]
        if len(self.input_shape) == 4:
            h = self.input_shape[2]
            w = self.input_shape[3]
            if isinstance(h, int) and isinstance(w, int):
                return (w, h)
        return fallback_hw  # (W, H)

    def classify(self, img_bgr: cv2.Mat) -> Any:
        # Para MobileNet típico: 224x224 NCHW float32
        w, h = self._infer_input_hw((224, 224))
        pre = cv2.resize(img_bgr, (w, h)).astype(np.float32) / 255.0
        pre = pre[..., ::-1]  # BGR->RGB (común en modelos)
        x = np.transpose(pre, (2, 0, 1))[np.newaxis, ...]  # NCHW

        out = self.sess.run(None, {self.input_name: x})[0]
        idx = int(np.argmax(out, axis=1)[0]) if out.ndim == 2 else int(np.argmax(out))
        if self.classes and 0 <= idx < len(self.classes):
            return self.classes[idx]
        return idx  # si no tienes labels, devuelve el índice

    def detect(self, frame_bgr: cv2.Mat) -> Any:
        """
        OJO: Esto solo tiene sentido si el modelo cargado es realmente de detección.
        Devuelve la salida cruda; el postproceso depende del modelo.
        """
        w, h = self._infer_input_hw((640, 640))
        pre = cv2.resize(frame_bgr, (w, h)).astype(np.float32) / 255.0
        pre = pre[..., ::-1]  # BGR->RGB
        x = np.transpose(pre, (2, 0, 1))[np.newaxis, ...]  # NCHW

        return self.sess.run(None, {self.input_name: x})
