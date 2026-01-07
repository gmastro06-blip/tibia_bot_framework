from typing import Optional, Any
import re

import cv2
import numpy as np
import easyocr
import onnxruntime as ort


class OCR:
    def __init__(self, crnn_model_path: Optional[str] = None):
        self.reader = easyocr.Reader(['en'], gpu=True)
        crnn_providers = ['CUDAExecutionProvider']
        if crnn_model_path:
            self.crnn_sess = ort.InferenceSession(
                crnn_model_path, providers=crnn_providers
            )
        else:
            self.crnn_sess = None

    def preprocess(self, img: Any) -> Any:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        upscale = cv2.resize(thresh, None, fx=2, fy=2,
                             interpolation=cv2.INTER_CUBIC)
        denoise = cv2.fastNlMeansDenoising(upscale, h=10)
        dilate = cv2.dilate(denoise, np.ones((3, 3), np.uint8), iterations=1)
        return dilate

    def read(self, img: Any, whitelist: str = '0123456789/') -> str:
        pre = self.preprocess(img)
        result = self.reader.readtext(pre, allowlist=whitelist, detail=0)
        text = ''.join(result).strip()
        return re.sub(r'[^0-9/]', '', text)

    def read_digits_crnn(self, img: Any) -> str:
        if self.crnn_sess is None:
            return ""
        pre = self.preprocess(img)
        input = cv2.resize(pre, (100, 32)).astype(np.float32)[np.newaxis, np.newaxis, ...] / 255.0
        output = self.crnn_sess.run(None, {'input': input})[0]
        # Decode CTC output a dígitos + /
        preds = np.argmax(output, axis=2)
        text = ''
        prev = -1
        for p in preds[0]:
            if p != prev and p > 0:
                text += '0123456789/'[p-1]
            prev = p
        return text

    def read_crnn(self, img: cv2.Mat) -> str:
        # Full CTC decode (placeholder implementado para evitar error de bloque vacío)
        if self.crnn_sess is None:
            return ""
        # Lógica completa: preprocess + inference + decode
        pre = self.preprocess(img)
        resized = cv2.resize(pre, (128, 32)).astype(np.float32)
        input = resized[np.newaxis, np.newaxis, ...] / 255.0
        output = self.crnn_sess.run(None, {'input': input})[0]
        preds = np.argmax(output, axis=2)
        text = ''
        prev = -1
        for p in preds[0]:
            if p != prev and p > 0:
                # Ejemplo decode genérico; ajusta a charset real
                text += chr(p - 1 + ord('a'))
            prev = p
        return text
