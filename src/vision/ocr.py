import cv2
import numpy as np
import easyocr
import re
import os
from typing import Optional, Tuple, Dict, Any, List, Sequence, Mapping, cast
import json


class OCRProcessor:
    def __init__(self):
        self._debug = os.getenv("OCR_DEBUG", "").strip().lower() in {"1", "true", "yes"} or os.getenv(
            "BOT_DEBUG", ""
        ).strip().lower() in {"1", "true", "yes"}

        self._last_cap_debug_ts = 0.0

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
        # OpenCV permite dsize=None cuando se usan fx/fy, pero los stubs tipados no.
        scaled = cv2.resize(thresh, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

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
                text = str(results[0])  # Tomar el primer resultado
                if self._debug:
                    print(f"OCR encontró: '{text}'")
                # Limpiar texto: solo números y /
                cleaned = re.sub(r'[^0-9/]', '', text)
                # Aplicar correcciones
                return str(self.corrections.get(cleaned, cleaned))
            else:
                if self._debug:
                    print("OCR no encontró texto")
                return ""

        except Exception as e:
            print(f"Error en OCR: {e}")
            return ""

    def _readtext_strings(self, image: np.ndarray, *, allowlist: Optional[str] = None) -> List[str]:
        """Devuelve una lista de strings OCR (sin limpiar a solo dígitos)."""
        try:
            processed = self.preprocess_image(image)
            results = self.reader.readtext(processed, detail=0, allowlist=allowlist)
            out: List[str] = []
            for r in results or []:
                try:
                    s = str(r)
                    if s:
                        out.append(s)
                except Exception:
                    continue
            if self._debug and out:
                print(f"OCR raw strings: {out}")
            return out
        except Exception:
            return []

    @staticmethod
    def _parse_capacity_from_text(text: str) -> Optional[int]:
        if not text:
            return None
        try:
            m = re.search(r"\bcap\b\s*[:\-]?\s*(\d{1,6})", text, flags=re.IGNORECASE)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_coords_from_text(text: str) -> tuple[int, int, int | None] | None:
        """Parse (x,y,z) from OCR text.

        Accepts formats like:
        - X: 32561 Y: 32496 Z: 7
        - 32561 32496 7
        - 32561,32496,7
        - 32561 32496
        """

        s = (text or "").strip()
        if not s:
            return None

        try:
            m = re.search(
                r"(?i)(?:x\s*[:=\s,]+(?P<x>-?\d+))\D+"
                r"(?:y\s*[:=\s,]+(?P<y>-?\d+))"
                r"(?:\D+(?:z\s*[:=\s,]+(?P<z>-?\d+)))?",
                s,
            )
            if m:
                x = int(m.group("x"))
                y = int(m.group("y"))
                z_raw = m.group("z")
                z = int(z_raw) if z_raw is not None else None
                return x, y, z
        except Exception:
            pass

        nums = re.findall(r"-?\d+", s)
        if len(nums) >= 2:
            try:
                x = int(nums[0])
                y = int(nums[1])
                z = int(nums[2]) if len(nums) >= 3 else None
                return x, y, z
            except Exception:
                return None
        return None

    def extract_coords(
        self,
        frame: np.ndarray,
        rois: Mapping[str, Any],
        resolution: Tuple[int, int],
    ) -> tuple[int, int, int | None] | None:
        """Extract player coordinates (x,y,z) via OCR.

        Requires an ROI named `coords_ocr` in the ROI config.
        Returns None if ROI is missing or OCR/parsing fails.

        Aqui se puede mejorar: si el cliente no muestra coordenadas en pantalla,
        esta extracción por OCR no es fiable y puede producir falsos positivos.
        En ese caso, conviene deshabilitarlo o reemplazarlo por otra estrategia
        (p.ej. minimap-motion experimental, anchor-based inference, o telemetría externa).
        """

        try:
            if not (hasattr(rois, "get") and rois.get("coords_ocr") is not None):
                return None

            x, y, w, h = self._roi_to_px(frame, rois, resolution, cast(Dict[str, Any], rois["coords_ocr"]))

            # Expand a bit: coords text is often tight and OCR benefits from margin.
            x0 = max(0, int(x - (w * 0.25)))
            y0 = max(0, int(y - (h * 0.50)))
            x1 = min(int(frame.shape[1]), int(x + w + (w * 0.25)))
            y1 = min(int(frame.shape[0]), int(y + h + (h * 0.50)))
            crop = frame[y0:y1, x0:x1]
            if crop is None or getattr(crop, "size", 0) == 0:
                return None

            # Upscale for small fonts.
            try:
                crop = cv2.resize(crop, (0, 0), fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            except Exception:
                pass

            # Try raw OCR first (some fonts break with heavy thresholding).
            candidates: List[str] = []
            try:
                res = self.reader.readtext(
                    crop,
                    detail=0,
                    allowlist="0123456789XYZxyz:,- ",
                )
                for r in res or []:
                    s = str(r).strip()
                    if s:
                        candidates.append(s)
            except Exception:
                pass

            # Then try preprocessed OCR.
            try:
                processed = self.preprocess_image(crop)
                res2 = self.reader.readtext(
                    processed,
                    detail=0,
                    allowlist="0123456789XYZxyz:,- ",
                )
                for r in res2 or []:
                    s = str(r).strip()
                    if s:
                        candidates.append(s)
            except Exception:
                pass

            # Also join any raw strings to increase parse success.
            try:
                candidates.extend(self._readtext_strings(crop, allowlist=None))
            except Exception:
                pass

            # Sanity-check parsed coords: prevents false positives when ROI drifts
            # into HP/MP/CAP areas (common early in calibration).
            try:
                # Default más alto para evitar falsos positivos cuando NO hay coords visibles.
                # Si tu servidor/cliente usa coords más bajas, override vía COORDS_MIN_XY.
                min_xy = int(float(os.getenv("COORDS_MIN_XY", "20000").strip() or "20000"))
            except Exception:
                min_xy = 20000
            try:
                max_xy = int(float(os.getenv("COORDS_MAX_XY", "100000").strip() or "100000"))
            except Exception:
                max_xy = 100000
            try:
                max_z = int(float(os.getenv("COORDS_MAX_Z", "15").strip() or "15"))
            except Exception:
                max_z = 15

            def _valid_coords(p: tuple[int, int, int | None]) -> bool:
                try:
                    cx, cy, cz = p
                    if cx < min_xy or cy < min_xy:
                        return False
                    if cx > max_xy or cy > max_xy:
                        return False
                    if cz is not None and not (0 <= int(cz) <= int(max_z)):
                        return False
                    return True
                except Exception:
                    return False

            joined = " ".join(candidates)
            parsed = self._parse_coords_from_text(joined)
            if parsed is not None and _valid_coords(parsed):
                return parsed

            # Fallback: OCR sometimes returns X and Y as separate tokens without labels.
            # The generic parser may grab early small numbers (timers, counters) and fail validation.
            # Here we try to find the first two "large" numbers in-range and treat them as (x,y).
            try:
                nums_all = [int(n) for n in re.findall(r"-?\d+", joined)]
                nums_big = [v for v in nums_all if (min_xy <= abs(int(v)) <= max_xy)]
                if len(nums_big) >= 2:
                    x2 = int(nums_big[0])
                    y2 = int(nums_big[1])
                    z2: int | None = None
                    # Optional Z: look for the first small integer after y.
                    try:
                        for v in nums_all:
                            vv = int(v)
                            if 0 <= vv <= int(max_z):
                                z2 = vv
                                break
                    except Exception:
                        z2 = None

                    cand = (x2, y2, z2)
                    if _valid_coords(cand):
                        return cand
            except Exception:
                pass

            # Last resort: parse each candidate line independently.
            for c in candidates:
                parsed = self._parse_coords_from_text(c)
                if parsed is not None and _valid_coords(parsed):
                    return parsed

            return None
        except Exception:
            return None

    def extract_capacity(self, frame: np.ndarray, rois: Mapping[str, Any], resolution: Tuple[int, int]) -> Optional[int]:
        """Extrae capacidad (cap) vía OCR.

        Estrategia:
        1) Si existe ROI `cap_ocr`, OCR solo dígitos (más robusto).
        2) Si no, intentar parsear "Cap: 123" desde `skills_panel` (OCR raw).
        """

        def normalize_to_px(roi_def: Dict[str, Any]) -> Tuple[int, int, int, int]:
            return self._roi_to_px(frame, rois, resolution, roi_def)

        # (1) ROI específico de cap (recomendado)
        try:
            if hasattr(rois, "get") and rois.get("cap_ocr") is not None:
                x, y, w, h = normalize_to_px(cast(Dict[str, Any], rois["cap_ocr"]))

                # Avoid expanding into the nearby soul_ocr region (very close on some HUDs).
                # If we include both, digit OCR may concatenate/choose the wrong number.
                soul_bounds: Optional[Tuple[int, int, int, int]] = None
                try:
                    if hasattr(rois, "get") and rois.get("soul_ocr") is not None:
                        soul_bounds = normalize_to_px(cast(Dict[str, Any], rois["soul_ocr"]))
                except Exception:
                    soul_bounds = None

                # Expand a bit: CAP digits can be tiny and ROIs can drift.
                # We bias expansion to the left (numbers typically right-aligned).
                # NOTE: At 1920x1080 the configured cap_ocr ROI is ~36px wide.
                # That can easily capture only the last digit. We expand a bit to the left,
                # but not too much, or we might include the 'Cap' label which OCR can
                # misread as digits.
                x0 = max(0, int(x - (w * 2.2)))
                y0 = max(0, int(y - (h * 1.0)))
                x1 = min(int(frame.shape[1]), int(x + w + (w * 1.5)))
                y1 = min(int(frame.shape[0]), int(y + h + (h * 1.0)))

                try:
                    if soul_bounds is not None:
                        sx, sy, sw, sh = soul_bounds
                        x0 = max(x0, int(sx + sw + 2))
                except Exception:
                    pass
                crop = frame[y0:y1, x0:x1]

                # Optional debug snapshot of the exact crop used for CAP OCR.
                try:
                    if self._debug and os.getenv("CAP_OCR_DEBUG_SNAP", "").strip().lower() in {"1", "true", "yes"}:
                        import time as _time
                        now = float(_time.time())
                        if (now - float(getattr(self, "_last_cap_debug_ts", 0.0))) >= 1.0:
                            setattr(self, "_last_cap_debug_ts", now)
                            try:
                                os.makedirs("logs/debug_cap", exist_ok=True)
                                cv2.imwrite(f"logs/debug_cap/{now:.6f}_cap_crop.png", crop)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Extra upscale for tiny CAP digits.
                try:
                    crop = cv2.resize(crop, (0, 0), fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
                except Exception:
                    pass

                # First attempt: run OCR directly on the raw (upscaled) crop.
                # For some HUD fonts, aggressive threshold/dilate can distort digits
                # and cause misreads (e.g. reading label fragments as numbers).
                def _best_single_box_number(results) -> Optional[int]:
                    best: tuple[float, int, int] | None = None  # (conf, n_digits, value)
                    for _bbox, text, conf in results or []:
                        try:
                            s = re.sub(r"[^0-9]", "", str(text or ""))
                            if not s:
                                continue
                            if not (1 <= len(s) <= 6):
                                continue
                            v = int(s)
                            c = float(conf or 0.0)
                            key = (c, len(s), v)
                            if best is None or key > best:
                                best = key
                        except Exception:
                            continue
                    return int(best[2]) if best is not None else None

                try:
                    raw_res = self.reader.readtext(crop, detail=1, allowlist="0123456789")
                    v_raw = _best_single_box_number(raw_res)
                    if v_raw is not None:
                        return v_raw
                except Exception:
                    pass

                # Prefer bbox-based digit OCR and reconstruct a full number.
                def _read_number_from_digit_boxes(results) -> Optional[int]:
                    items: list[tuple[float, float, float, float, str]] = []  # (x0,x1,y_mid,conf,digits)
                    for bbox, text, conf in results or []:
                        try:
                            s = re.sub(r"[^0-9]", "", str(text or ""))
                            if not s:
                                continue
                            xs = [p[0] for p in bbox]
                            ys = [p[1] for p in bbox]
                            x0f = float(min(xs))
                            x1f = float(max(xs))
                            y0f = float(min(ys))
                            y1f = float(max(ys))
                            y_mid = (y0f + y1f) / 2.0
                            items.append((x0f, x1f, y_mid, float(conf or 0.0), s))
                        except Exception:
                            continue

                    if not items:
                        return None

                    # Cluster into rows by y_mid. For this HUD crop we expect a single row.
                    # Use a tolerance derived from y spread (upscaled crops have bigger absolute coords).
                    y_vals = sorted(i[2] for i in items)
                    y_tol = max(10.0, (y_vals[-1] - y_vals[0]) * 0.25)

                    rows: list[list[tuple[float, float, float, float, str]]] = []
                    row_y: list[float] = []
                    for it in sorted(items, key=lambda t: t[2]):
                        placed = False
                        for idx, y0 in enumerate(row_y):
                            if abs(it[2] - y0) <= y_tol:
                                rows[idx].append(it)
                                # update running average y
                                row_y[idx] = (row_y[idx] * (len(rows[idx]) - 1) + it[2]) / float(len(rows[idx]))
                                placed = True
                                break
                        if not placed:
                            rows.append([it])
                            row_y.append(it[2])

                    # Pick the row that is most to the right (cap number is right-aligned).
                    def row_key(row: list[tuple[float, float, float, float, str]]) -> tuple[float, float]:
                        max_x1 = max(t[1] for t in row)
                        sum_conf = sum(t[3] for t in row)
                        return (max_x1, sum_conf)

                    best_row = max(rows, key=row_key)
                    parts = [t[4] for t in sorted(best_row, key=lambda t: t[0])]
                    joined = "".join(parts)
                    joined = joined.strip()
                    if not joined:
                        return None
                    try:
                        return int(joined)
                    except Exception:
                        # If OCR split weirdly, fall back to max digit group.
                        nums = re.findall(r"\d{1,6}", joined)
                        return int(max(nums, key=lambda s: int(s))) if nums else None

                try:
                    processed = self.preprocess_image(crop)
                    res = self.reader.readtext(processed, detail=1, allowlist="0123456789")
                    v = _read_number_from_digit_boxes(res)
                    if v is not None:
                        return v
                except Exception:
                    pass

                try:
                    processed = self.preprocess_image(crop)
                    inv = cv2.bitwise_not(processed)
                    res = self.reader.readtext(inv, detail=1, allowlist="0123456789")
                    v = _read_number_from_digit_boxes(res)
                    if v is not None:
                        return v
                except Exception:
                    pass

                # Fallback: keep old behavior but on expanded crop.
                candidates: List[str] = []
                try:
                    candidates.extend(self._readtext_strings(crop, allowlist="0123456789"))
                except Exception:
                    pass
                try:
                    candidates.extend(self._readtext_strings(crop, allowlist=None))
                except Exception:
                    pass
                joined = " ".join(candidates)
                nums = re.findall(r"\d{1,6}", joined)
                if nums:
                    return int(max(nums, key=lambda s: int(s)))
        except Exception:
            pass

        # (2) Parse dentro del skills_panel
        try:
            if hasattr(rois, "get") and rois.get("skills_panel") is not None:
                x, y, w, h = normalize_to_px(cast(Dict[str, Any], rois["skills_panel"]))
                crop = frame[y : y + h, x : x + w]
                texts = self._readtext_strings(crop, allowlist=None)
                joined = " ".join(texts)
                cap = self._parse_capacity_from_text(joined)
                if cap is not None:
                    return cap

                # BBox-based fallback: find the cap/capacity label and read the number on the same row.
                try:
                    if re.search(r"\bcap\b|capac", joined, flags=re.IGNORECASE):
                        processed = self.preprocess_image(crop)
                        # detail=1: [ (bbox, text, conf), ... ] where bbox has 4 points
                        results = self.reader.readtext(processed, detail=1, allowlist=None)
                        entries: list[tuple[str, float, float, float, float, float]] = []
                        for bbox, text, conf in results or []:
                            try:
                                s = str(text or "")
                                if not s:
                                    continue
                                xs = [p[0] for p in bbox]
                                ys = [p[1] for p in bbox]
                                bx0 = float(min(xs))
                                by0 = float(min(ys))
                                bx1 = float(max(xs))
                                by1 = float(max(ys))
                                entries.append((s, float(conf or 0.0), float(bx0), float(by0), float(bx1), float(by1)))
                            except Exception:
                                continue

                        cap_labels = [e for e in entries if "cap" in e[0].lower() or "capac" in e[0].lower()]
                        if cap_labels:
                            cap_label = max(cap_labels, key=lambda e: e[1])
                            _s, _c, lx0, ly0, lx1, ly1 = cap_label
                            ly_mid = (ly0 + ly1) / 2.0

                            num_pairs: list[tuple[int, float]] = []
                            for s, conf, bx0, by0, bx1, by1 in entries:
                                if bx0 <= lx1:
                                    continue
                                y_mid = (by0 + by1) / 2.0
                                if abs(y_mid - ly_mid) > max(10.0, (ly1 - ly0) * 1.5):
                                    continue
                                found = [int(n) for n in re.findall(r"\d{1,6}", s)]
                                for v in found:
                                    num_pairs.append((v, conf))

                            if num_pairs:
                                # Prefer higher confidence; if ties, larger value.
                                num_pairs.sort(key=lambda t: (t[1], t[0]), reverse=True)
                                return int(num_pairs[0][0])

                        # Last resort: digit-only OCR on full panel and pick a reasonable maximum.
                        digit_texts = self._readtext_strings(crop, allowlist="0123456789")
                        djoined = " ".join(digit_texts)
                        vals = [int(n) for n in re.findall(r"\d{1,6}", djoined)]
                        if vals:
                            return int(max(vals))
                except Exception:
                    pass
        except Exception:
            pass

        return None

    def extract_hp_mp(self, frame: np.ndarray, rois: Mapping[str, Any], resolution: Tuple[int, int]) -> Tuple[Optional[int], Optional[int]]:
        """Compat: devuelve solo HP/MP actuales."""
        hp_cur, _hp_max, mp_cur, _mp_max = self.extract_hp_mp_full(frame, rois, resolution)
        return hp_cur, mp_cur

    def _roi_to_px(
        self,
        frame: np.ndarray,
        rois: Mapping[str, Any],
        resolution: Tuple[int, int],
        roi_def: Mapping[str, Any],
    ) -> Tuple[int, int, int, int]:
        """Convierte una ROI (normalizada o en px) al frame actual, compensando letterboxing."""
        frame_w, frame_h = frame.shape[1], frame.shape[0]

        source_resolution = rois.get("_source_resolution") if hasattr(rois, "get") else None
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

        unit = str(roi_def.get("unit", "") if hasattr(roi_def, "get") else "").lower()
        x_val = roi_def.get("x") if hasattr(roi_def, "get") else None
        y_val = roi_def.get("y") if hasattr(roi_def, "get") else None
        w_val = roi_def.get("w") if hasattr(roi_def, "get") else None
        h_val = roi_def.get("h") if hasattr(roi_def, "get") else None

        def _f(v: Any, default: float) -> float:
            try:
                if v is None:
                    return default
                return float(v)
            except Exception:
                return default

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
            x_src = _f(x_val, 0.0) * source_w
            y_src = _f(y_val, 0.0) * source_h
            w_src = _f(w_val, 0.0) * source_w
            h_src = _f(h_val, 0.0) * source_h
        else:
            x_src = _f(x_val, 0.0)
            y_src = _f(y_val, 0.0)
            w_src = _f(w_val, 0.0)
            h_src = _f(h_val, 0.0)

        # Optional global offset (in *source px*), applied to all ROIs.
        # Used by AnchorTracker to keep ROIs aligned when the in-game HUD moves.
        try:
            off = rois.get("_roi_offset_px") if hasattr(rois, "get") else None
            if isinstance(off, (list, tuple)) and len(off) == 2:
                x_src += float(off[0] or 0.0)
                y_src += float(off[1] or 0.0)
        except Exception:
            pass

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

        def _norm(name: str) -> str:
            s = (name or "").strip().lower()
            s = re.sub(r"[\s\-]+", "_", s)
            s = re.sub(r"[^a-z0-9_]+", "", s)
            s = re.sub(r"_+", "_", s)
            return s

        wanted = {_norm(c) for c in class_names if c and _norm(c)}
        best = None
        best_conf = -1.0
        for b in boxes:
            cls = _norm(str(b.get("class", "")))
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
        rois: Mapping[str, Any],
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
            if self._debug:
                print(f"Imagen de entrada: {frame.shape}, tipo: {frame.dtype}")
                print(f"Valor promedio de píxeles: {frame.mean():.2f}")

            # Verificar si la imagen está mayoritariamente negra (posible ventana minimizada)
            if frame.mean() < 5.0:
                print("⚠️  ADVERTENCIA: La imagen capturada está mayoritariamente negra")
                print("   Esto puede indicar que la ventana de Tibia está minimizada o no visible")
                print("   Asegúrate de que Tibia esté maximizado y en primer plano")
                return None, None, None, None

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
                        # When the user adds/removes HUD bars, the strip can contain extra numbers
                        # (stamina, soul, etc). To avoid mis-assigning HP/MP, estimate where the
                        # red/blue bars are inside the strip and choose OCR candidates closest to them.
                        def _estimate_color_x_center(crop_bgr: np.ndarray, kind: str) -> Optional[float]:
                            try:
                                hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
                                hsv_mat = cast(Any, hsv)

                                if kind.lower() == "hp":
                                    low1 = cast(Any, np.array([0, 70, 50], dtype=np.uint8))
                                    high1 = cast(Any, np.array([10, 255, 255], dtype=np.uint8))
                                    low2 = cast(Any, np.array([170, 70, 50], dtype=np.uint8))
                                    high2 = cast(Any, np.array([180, 255, 255], dtype=np.uint8))
                                    mask_low = cv2.inRange(hsv_mat, low1, high1)
                                    mask_high = cv2.inRange(hsv_mat, low2, high2)
                                    mask = cv2.bitwise_or(mask_low, mask_high)
                                elif kind.lower() == "mp":
                                    low = cast(Any, np.array([90, 70, 50], dtype=np.uint8))
                                    high = cast(Any, np.array([135, 255, 255], dtype=np.uint8))
                                    mask = cv2.inRange(hsv_mat, low, high)
                                else:
                                    return None

                                # Column occupancy: where the bar color appears consistently.
                                col_frac = (mask > 0).mean(axis=0)
                                cols = np.where(col_frac > 0.15)[0]
                                if cols.size == 0:
                                    return None
                                # Use midpoint between first/last matching columns.
                                x0 = float(int(cols.min()))
                                x1 = float(int(cols.max()))
                                return (x0 + x1) / 2.0
                            except Exception:
                                return None

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
                                pts = cast(Sequence[Sequence[Any]], bbox)
                                xs = [float(pt[0]) for pt in pts]
                                x_center = float(sum(xs)) / max(1, len(xs))
                            except Exception:
                                x_center = 0.0

                            parsed.append((x_center, cur, mx, float(conf) if conf is not None else 0.0, cleaned))

                        # If we have no OCR candidates, bail early.
                        if parsed:
                            strip_w = float(strip_crop.shape[1])
                            hp_ref = _estimate_color_x_center(strip_crop, "hp")
                            mp_ref = _estimate_color_x_center(strip_crop, "mp")

                            # Sensible fallbacks: HP tends to be left-ish, MP right-ish.
                            if hp_ref is None:
                                hp_ref = strip_w * 0.25
                            if mp_ref is None:
                                mp_ref = strip_w * 0.75

                            def _pick_best(cands, ref_x: float):
                                # Lower is better.
                                best = None
                                for x, cur, mx, conf, txt in cands:
                                    try:
                                        dist = abs(float(x) - float(ref_x))
                                        has_max = 1 if mx is not None else 0
                                        nd = len(re.sub(r"[^0-9]", "", str(txt or "")))
                                        # Prefer having max (cur/max), then higher confidence, then more digits.
                                        score = dist - (has_max * 30.0) - (float(conf) * 5.0) - (float(nd) * 0.2)
                                        if best is None or score < best[0]:
                                            best = (score, (x, cur, mx, conf, txt))
                                    except Exception:
                                        continue
                                return best[1] if best is not None else None

                            # Prefer candidates that include max (cur/max). If none, allow single numbers.
                            with_max = [t for t in parsed if t[2] is not None]
                            any_num = list(parsed)

                            # Pick HP
                            if hp_current is None:
                                choice = _pick_best(with_max or any_num, float(hp_ref))
                                if choice is not None:
                                    _x, cur, mx, _conf, _txt = choice
                                    hp_current, hp_max = cur, mx

                            # Pick MP (avoid reusing the same exact candidate when possible)
                            if mp_current is None:
                                pool = (with_max or any_num)
                                try:
                                    if hp_current is not None:
                                        pool = [t for t in pool if t[0] != (choice[0] if choice is not None else None)]
                                except Exception:
                                    pool = (with_max or any_num)
                                choice2 = _pick_best(pool or (with_max or any_num), float(mp_ref))
                                if choice2 is not None:
                                    _x, cur, mx, _conf, _txt = choice2
                                    mp_current, mp_max = cur, mx
                    except Exception as e:
                        print(f"Error OCR strip superior: {e}")

            if 'hp_top_ocr' in rois and hp_current is None:
                hp_roi = normalize_to_px(rois['hp_top_ocr'])
                hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
                if self._debug:
                    print(f"HP ROI: {hp_roi}, crop shape: {hp_crop.shape if hp_crop.size > 0 else 'empty'}")
                if hp_crop.size > 0:
                    if self._debug:
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
                        if self._debug:
                            print(f"HP ROI fallback: ({x0}, {y0}, {x1-x0}, {y1-y0}), crop shape: {hp_crop2.shape if hp_crop2.size > 0 else 'empty'}")
                        if hp_crop2.size > 0:
                            hp_text = self.extract_text(hp_crop2, allowlist="0123456789/")
                    if self._debug:
                        print(f"HP texto crudo: '{hp_text}'")
                    if hp_text:
                        hp_current, hp_max = self._parse_current_and_max(hp_text, "HP")

            # Extraer MP del OCR superior
            if 'mp_top_ocr' in rois and mp_current is None:
                mp_roi = normalize_to_px(rois['mp_top_ocr'])
                mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
                if self._debug:
                    print(f"MP ROI: {mp_roi}, crop shape: {mp_crop.shape if mp_crop.size > 0 else 'empty'}")
                if mp_crop.size > 0:
                    if self._debug:
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
                        if self._debug:
                            print(f"MP ROI fallback: ({x0}, {y0}, {x1-x0}, {y1-y0}), crop shape: {mp_crop2.shape if mp_crop2.size > 0 else 'empty'}")
                        if mp_crop2.size > 0:
                            mp_text = self.extract_text(mp_crop2, allowlist="0123456789/")
                    if self._debug:
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
                        if self._debug:
                            print(f"{label}: Usando valor único encontrado: {num}")
                        return num, None

            if self._debug:
                print(f"{label}: No se pudo parsear valor de: '{text}'")
            return None, None

        except Exception as e:
            print(f"Error parseando {label}: {e}")
            return None, None