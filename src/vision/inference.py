from typing import List, Optional
import onnxruntime as ort  # type: ignore[import-untyped]
import cv2
import numpy as np
import os
from .ocr import OCR

class VisionInference:
    def __init__(self, model_path: str = "models/yolo.onnx", classes: List[str] = []):
        self.classes = classes
        self.ocr = OCR()  # Initialize OCR instance
        self.sess: Optional[ort.InferenceSession] = None
        if os.path.exists(model_path):
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            try:
                session_options = ort.SessionOptions()
                session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                session_options.enable_mem_pattern = True
                session_options.log_severity_level = 3  # Suppress warnings
                self.sess = ort.InferenceSession(model_path, providers=providers, sess_options=session_options)
                print("Modelo ONNX cargado (GPU preferido)")
            except Exception as e:
                print(f"GPU falló: {e}. Fallback CPU")
                self.sess = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        else:
            print(f"Modelo {model_path} no encontrado. Detector desactivado")

    def detect(self, frame: np.ndarray) -> List:
        if self.sess is None or frame is None or frame.size == 0:
            return []
        pre = cv2.resize(frame, (640, 640)) / 255.0
        input = pre.transpose(2, 0, 1)[np.newaxis, ...].astype(np.float32)
        dets = self.sess.run(None, {'input': input})[0]
        return dets.tolist() if dets is not None else []

    def classify(self, img: np.ndarray) -> str:
        if self.sess is None or img is None or img.size == 0:
            return "unknown"
        pre = cv2.resize(img, (224, 224)) / 255.0
        input = pre.astype(np.float32)[np.newaxis, ...].transpose(0, 3, 1, 2)
        output = self.sess.run(None, {'input': input})[0]
        return self.classes[np.argmax(output)] if self.classes else "unknown"