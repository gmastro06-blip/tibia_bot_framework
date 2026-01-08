import cv2
import numpy as np
import easyocr
import re
import os
from typing import Optional, Tuple, Dict, Any, List, Sequence
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

        # Mejorar contraste para texto blanco sobre fondo oscuro
        # Aplicar CLAHE (Contrast Limited Adaptive Histogram Equalization)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)

        # Aplicar threshold adaptativo más agresivo
        thresh = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Invertir si el texto es blanco (más común en juegos)
        # Verificar si hay más píxeles blancos que negros
        white_pixels = cv2.countNonZero(thresh)
        total_pixels = thresh.size
        if white_pixels > total_pixels / 2:
            # Texto blanco sobre fondo negro - invertir
            thresh = cv2.bitwise_not(thresh)

        # Escalar a 2x para mejor reconocimiento
        scaled = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

        # Aplicar denoising
        denoised = cv2.medianBlur(scaled, 3)

        # Dilatar ligeramente para conectar caracteres
        kernel = np.ones((2, 2), np.uint8)
        dilated = cv2.dilate(denoised, kernel, iterations=1)

        return dilated

    def extract_text(self, image: np.ndarray, *, allowlist: Optional[str] = None) -> str:
        """Extrae texto de una imagen usando OCR"""
        try:
            # Preprocesar imagen
            processed = self.preprocess_image(image)

            # Intentar OCR sin restricciones primero
            results = self.reader.readtext(processed, detail=0, allowlist=allowlist)

            if results:
                text = results[0]  # Tomar el primer resultado
                print(f"OCR encontró: '{text}'")
                # Limpiar texto: solo números y /
                cleaned = re.sub(r'[^0-9/]', '', text)
                # Aplicar correcciones
                return self.corrections.get(cleaned, cleaned)
            else:
                print("OCR no encontró texto")
                return ""

        except Exception as e:
            print(f"Error en OCR: {e}")
            return ""

    def extract_hp_mp(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> Tuple[Optional[int], Optional[int]]:
        """Compat: devuelve solo HP/MP actuales."""
        hp_cur, _hp_max, mp_cur, _mp_max = self.extract_hp_mp_full(frame, rois, resolution)
        return hp_cur, mp_cur

    def _roi_to_px(
        self,
        frame: np.ndarray,
        rois: Dict[str, Dict[str, float]],
        resolution: Tuple[int, int],
        roi_def: Dict[str, Any],
    ) -> Tuple[int, int, int, int]:
        """Convierte una ROI (normalizada o en px) al frame actual, compensando letterboxing."""
        frame_w, frame_h = frame.shape[1], frame.shape[0]

        source_resolution = rois.get("_source_resolution")
        if (
            isinstance(source_resolution, (list, tuple))
            and len(source_resolution) == 2
            and source_resolution[0]
            and source_resolution[1]
        ):
            source_w, source_h = int(source_resolution[0]), int(source_resolution[1])
        else:
            source_w, source_h = int(resolution[0]), int(resolution[1])

        scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
        content_w = source_w * scale
        content_h = source_h * scale
        offset_x = (frame_w - content_w) / 2.0
        offset_y = (frame_h - content_h) / 2.0

        unit = str(roi_def.get("unit", "")).lower()
        x_val = roi_def.get("x")
        y_val = roi_def.get("y")
        w_val = roi_def.get("w")
        h_val = roi_def.get("h")

        def _is_normalized(v: Any) -> bool:
            try:
                vf = float(v)
            except Exception:
                return False
            return 0.0 <= vf <= 1.0

        is_norm = (
            unit != "px"
            and _is_normalized(x_val)
            and _is_normalized(y_val)
            and _is_normalized(w_val)
            and _is_normalized(h_val)
        )

        if is_norm:
            x_src = float(x_val) * source_w
            y_src = float(y_val) * source_h
            w_src = float(w_val) * source_w
            h_src = float(h_val) * source_h
        else:
            x_src = float(x_val)
            y_src = float(y_val)
            w_src = float(w_val)
            h_src = float(h_val)

        x = int(round(offset_x + x_src * scale))
        y = int(round(offset_y + y_src * scale))
        w = int(round(w_src * scale))
        h = int(round(h_src * scale))

        x = max(0, min(x, frame_w - 1))
        y = max(0, min(y, frame_h - 1))
        w = max(1, min(w, frame_w - x))
        h = max(1, min(h, frame_h - y))
        return x, y, w, h

    @staticmethod
    def _best_box_by_class(boxes: List[Dict[str, Any]], class_names: Sequence[str]) -> Optional[Dict[str, Any]]:
        if not boxes or not class_names:
            return None
        wanted = {c.lower() for c in class_names if c}
        best = None
        best_conf = -1.0
        for b in boxes:
            cls = str(b.get("class", "")).lower()
            if cls not in wanted:
                continue
            try:
                conf = float(b.get("confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            if conf > best_conf:
                best_conf = conf
                best = b
        return best

    @staticmethod
    def _crop_from_rf_box(frame: np.ndarray, box: Dict[str, Any]) -> Optional[np.ndarray]:
        try:
            x = float(box["x"])
            y = float(box["y"])
            w = float(box["width"])
            h = float(box["height"])
        except Exception:
            return None
        x0 = int(round(x - w / 2))
        y0 = int(round(y - h / 2))
        x1 = int(round(x + w / 2))
        y1 = int(round(y + h / 2))
        x0 = max(0, min(x0, frame.shape[1] - 1))
        y0 = max(0, min(y0, frame.shape[0] - 1))
        x1 = max(1, min(x1, frame.shape[1]))
        y1 = max(1, min(y1, frame.shape[0]))
        if x1 <= x0 or y1 <= y0:
            return None
        return frame[y0:y1, x0:x1]

    def extract_hp_mp_full(
        self,
        frame: np.ndarray,
        rois: Dict[str, Dict[str, float]],
        resolution: Tuple[int, int],
        rf_boxes: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
        """Extrae HP/MP actuales y máximos (cuando el OCR devuelve current/max)."""
        hp_current: Optional[int] = None
        hp_max: Optional[int] = None
        mp_current: Optional[int] = None
        mp_max: Optional[int] = None

        try:
            # Función para convertir coordenadas normalizadas a píxeles.
            # Importante: las ROIs suelen estar definidas para una "source_resolution" (p.ej. 1920x1080),
            # pero el frame capturado puede tener otra (p.ej. proyector 1920x1009). Reescalamos.
            def normalize_to_px(roi_def: Dict[str, Any]) -> Tuple[int, int, int, int]:
                return self._roi_to_px(frame, rois, resolution, roi_def)

            # Mostrar información de debug de la imagen
            print(f"Imagen de entrada: {frame.shape}, tipo: {frame.dtype}")
            print(f"Valor promedio de píxeles: {frame.mean():.2f}")

            # Verificar si la imagen está mayoritariamente negra (posible ventana minimizada)
            if frame.mean() < 5.0:
                print("⚠️  ADVERTENCIA: La imagen capturada está mayoritariamente negra")
                print("   Esto puede indicar que la ventana de Tibia está minimizada o no visible")
                print("   Asegúrate de que Tibia esté maximizado y en primer plano")
                return None, None

            # (A) Si hay detecciones Roboflow, intentar OCR sobre boxes de texto primero
            if rf_boxes:
                hp_text_classes = os.getenv("ROBOFLOW_HP_TEXT_CLASSES", "hp_text,hp_ocr,hp").split(",")
                mp_text_classes = os.getenv("ROBOFLOW_MP_TEXT_CLASSES", "mp_text,mp_ocr,mp").split(",")
                hp_box = self._best_box_by_class(rf_boxes, [c.strip() for c in hp_text_classes])
                mp_box = self._best_box_by_class(rf_boxes, [c.strip() for c in mp_text_classes])

                if hp_box is not None and hp_current is None:
                    hp_crop_rf = self._crop_from_rf_box(frame, hp_box)
                    if hp_crop_rf is not None:
                        hp_text = self.extract_text(hp_crop_rf, allowlist="0123456789/")
                        if hp_text:
                            hp_current, hp_max = self._parse_current_and_max(hp_text, "HP")

                if mp_box is not None and mp_current is None:
                    mp_crop_rf = self._crop_from_rf_box(frame, mp_box)
                    if mp_crop_rf is not None:
                        mp_text = self.extract_text(mp_crop_rf, allowlist="0123456789/")
                        if mp_text:
                            mp_current, mp_max = self._parse_current_and_max(mp_text, "MP")

            # Fallback: OCR fijo por ROIs
            # (0) Fallback robusto: OCR sobre el strip superior completo.
            # Usamos detail=1 para obtener bbox y separar izquierda (HP) / derecha (MP).
            if 'hpmp_top_strip' in rois and (hp_current is None or mp_current is None):
                strip_roi = normalize_to_px(rois['hpmp_top_strip'])
                strip_crop = frame[strip_roi[1]:strip_roi[1]+strip_roi[3], strip_roi[0]:strip_roi[0]+strip_roi[2]]
                if strip_crop.size > 0:
                    try:
                        processed = self.preprocess_image(strip_crop)
                        results = self.reader.readtext(processed, detail=1, allowlist="0123456789/")

                        parsed = []
                        for item in results or []:
                            try:
                                bbox, text, conf = item
                            except Exception:
                                continue
                            cleaned = re.sub(r'[^0-9/]', '', str(text))
                            if not cleaned:
                                continue

                            cur, mx = self._parse_current_and_max(cleaned, "STRIP")
                            if cur is None:
                                continue

                            # x_center del bbox (promedio de los 4 puntos)
                            try:
                                xs = [pt[0] for pt in bbox]
                                x_center = float(sum(xs)) / max(1, len(xs))
                            except Exception:
                                x_center = 0.0

                            parsed.append((x_center, cur, mx, float(conf) if conf is not None else 0.0, cleaned))

                        # ordenar por X (izquierda→derecha)
                        parsed.sort(key=lambda t: t[0])

                        # Elegir candidatos con max (formato cur/max) primero
                        with_max = [t for t in parsed if t[2] is not None]

                        def _assign_from(cands):
                            nonlocal hp_current, hp_max, mp_current, mp_max
                            if not cands:
                                return
                            if hp_current is None:
                                x, cur, mx, _conf, _txt = cands[0]
                                hp_current, hp_max = cur, mx
                            if mp_current is None and len(cands) >= 2:
                                x, cur, mx, _conf, _txt = cands[-1]
                                mp_current, mp_max = cur, mx

                        _assign_from(with_max)
                        # Si aún falta alguno, usar cualquier número detectado
                        if hp_current is None or mp_current is None:
                            _assign_from(parsed)
                    except Exception as e:
                        print(f"Error OCR strip superior: {e}")

            if 'hp_top_ocr' in rois and hp_current is None:
                hp_roi = normalize_to_px(rois['hp_top_ocr'])
                hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
                print(f"HP ROI: {hp_roi}, crop shape: {hp_crop.shape if hp_crop.size > 0 else 'empty'}")
                if hp_crop.size > 0:
                    print(f"HP crop - valor promedio: {hp_crop.mean():.2f}")
                    hp_text = self.extract_text(hp_crop, allowlist="0123456789/")
                    # Fallback: si no aparece nada, probar un crop más grande alrededor
                    if not hp_text:
                        pad_x = int(hp_roi[2] * 0.6)
                        pad_y = int(hp_roi[3] * 0.8)
                        x0 = max(0, hp_roi[0] - pad_x)
                        y0 = max(0, hp_roi[1] - pad_y)
                        x1 = min(frame.shape[1], hp_roi[0] + hp_roi[2] + pad_x)
                        y1 = min(frame.shape[0], hp_roi[1] + hp_roi[3] + pad_y)
                        hp_crop2 = frame[y0:y1, x0:x1]
                        print(f"HP ROI fallback: ({x0}, {y0}, {x1-x0}, {y1-y0}), crop shape: {hp_crop2.shape if hp_crop2.size > 0 else 'empty'}")
                        if hp_crop2.size > 0:
                            hp_text = self.extract_text(hp_crop2, allowlist="0123456789/")
                    print(f"HP texto crudo: '{hp_text}'")
                    if hp_text:
                        hp_current, hp_max = self._parse_current_and_max(hp_text, "HP")

            # Extraer MP del OCR superior
            if 'mp_top_ocr' in rois and mp_current is None:
                mp_roi = normalize_to_px(rois['mp_top_ocr'])
                mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
                print(f"MP ROI: {mp_roi}, crop shape: {mp_crop.shape if mp_crop.size > 0 else 'empty'}")
                if mp_crop.size > 0:
                    print(f"MP crop - valor promedio: {mp_crop.mean():.2f}")
                    mp_text = self.extract_text(mp_crop, allowlist="0123456789/")
                    if not mp_text:
                        pad_x = int(mp_roi[2] * 0.6)
                        pad_y = int(mp_roi[3] * 0.8)
                        x0 = max(0, mp_roi[0] - pad_x)
                        y0 = max(0, mp_roi[1] - pad_y)
                        x1 = min(frame.shape[1], mp_roi[0] + mp_roi[2] + pad_x)
                        y1 = min(frame.shape[0], mp_roi[1] + mp_roi[3] + pad_y)
                        mp_crop2 = frame[y0:y1, x0:x1]
                        print(f"MP ROI fallback: ({x0}, {y0}, {x1-x0}, {y1-y0}), crop shape: {mp_crop2.shape if mp_crop2.size > 0 else 'empty'}")
                        if mp_crop2.size > 0:
                            mp_text = self.extract_text(mp_crop2, allowlist="0123456789/")
                    print(f"MP texto crudo: '{mp_text}'")
                    if mp_text:
                        mp_current, mp_max = self._parse_current_and_max(mp_text, "MP")

        except Exception as e:
            print(f"Error extrayendo HP/MP: {e}")

        return hp_current, hp_max, mp_current, mp_max

    def _parse_current_and_max(self, text: str, label: str) -> Tuple[Optional[int], Optional[int]]:
        """Parsea current/max si existe; si no, devuelve (current, None)."""
        try:
            # Estrategia 1: Buscar patrón directo "current/max"
            match = re.search(r'(\d{1,4})/(\d{1,4})', text)
            if match:
                current = int(match.group(1))
                max_val = int(match.group(2))
                # Validar que los valores sean razonables
                if 0 < current <= max_val <= 10000:
                    return current, max_val

            # Estrategia 2: Si no hay patrón completo, buscar solo números
            numbers = re.findall(r'\d{1,4}', text)
            if numbers:
                # Tomar el primer número razonable
                for num_str in numbers:
                    num = int(num_str)
                    if 0 < num <= 10000:
                        print(f"{label}: Usando valor único encontrado: {num}")
                        return num, None

            print(f"{label}: No se pudo parsear valor de: '{text}'")
            return None, None

        except Exception as e:
            print(f"Error parseando {label}: {e}")
            return None, None