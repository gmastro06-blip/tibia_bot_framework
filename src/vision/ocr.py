import cv2
import numpy as np
import easyocr
import re
from typing import Optional, Tuple, Dict, Any
import json
import os

class OCRProcessor:
    def __init__(self):
        # Inicializar EasyOCR con GPU si está disponible
        try:
            self.reader = easyocr.Reader(['en'], gpu=True)
            print("OCR inicializado con GPU")
        except:
            self.reader = easyocr.Reader(['en'], gpu=False)
            print("OCR inicializado sin GPU")

        # Cargar correcciones OCR
        self.corrections = self._load_corrections()

    def _load_corrections(self) -> Dict[str, str]:
        """Carga las correcciones OCR desde el archivo de configuración"""
        corrections_file = os.path.join(os.path.dirname(__file__), '..', '..', 'configs', 'ocr_corrections.json')
        try:
            with open(corrections_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"Archivo de correcciones {corrections_file} no encontrado")
            return {}

    def preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """Preprocesa la imagen para mejorar el OCR"""
        # Convertir a escala de grises si es necesario
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        # Aplicar threshold adaptativo
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Escalar a 2x para mejor reconocimiento
        scaled = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

        # Aplicar denoising
        denoised = cv2.medianBlur(scaled, 3)

        # Dilatar ligeramente
        kernel = np.ones((2, 2), np.uint8)
        dilated = cv2.dilate(denoised, kernel, iterations=1)

        return dilated

    def extract_text(self, image: np.ndarray) -> str:
        """Extrae texto de una imagen usando OCR"""
        try:
            # Preprocesar imagen
            processed = self.preprocess_image(image)

            # Realizar OCR
            results = self.reader.readtext(processed, detail=0)

            if results:
                text = results[0]  # Tomar el primer resultado
                # Limpiar texto: solo números y /
                cleaned = re.sub(r'[^0-9/]', '', text)
                # Aplicar correcciones
                return self.corrections.get(cleaned, cleaned)
            else:
                return ""

        except Exception as e:
            print(f"Error en OCR: {e}")
            return ""

    def extract_hp_mp(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> Tuple[Optional[int], Optional[int]]:
        """Extrae HP y MP de la imagen usando las ROIs definidas"""
        hp_value = None
        mp_value = None

        try:
            # Función para convertir coordenadas normalizadas a píxeles
            def normalize_to_px(roi_norm: Dict[str, float]) -> Tuple[int, int, int, int]:
                x = int(roi_norm['x'] * resolution[0])
                y = int(roi_norm['y'] * resolution[1])
                w = int(roi_norm['w'] * resolution[0])
                h = int(roi_norm['h'] * resolution[1])
                return x, y, w, h

            # Extraer HP del OCR superior
            if 'hp_top_ocr' in rois:
                hp_roi = normalize_to_px(rois['hp_top_ocr'])
                hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
                if hp_crop.size > 0:
                    hp_text = self.extract_text(hp_crop)
                    if hp_text and '/' in hp_text:
                        try:
                            current, _ = hp_text.split('/')
                            hp_value = int(current)
                            print(f"HP OCR extraído: {hp_text} -> {hp_value}")
                        except ValueError:
                            print(f"Error parseando HP: {hp_text}")

            # Extraer MP del OCR superior
            if 'mp_top_ocr' in rois:
                mp_roi = normalize_to_px(rois['mp_top_ocr'])
                mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
                if mp_crop.size > 0:
                    mp_text = self.extract_text(mp_crop)
                    if mp_text and '/' in mp_text:
                        try:
                            current, _ = mp_text.split('/')
                            mp_value = int(current)
                            print(f"MP OCR extraído: {mp_text} -> {mp_value}")
                        except ValueError:
                            print(f"Error parseando MP: {mp_text}")

        except Exception as e:
            print(f"Error extrayendo HP/MP: {e}")

        return hp_value, mp_value