import cv2
import numpy as np
import re
import os
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List, Sequence, Mapping, cast
import json
from vision.hud_parsing import parse_current_and_max_with_reason


class OCRProcessor:
    def __init__(self):
        self._debug = os.getenv("OCR_DEBUG", "").strip().lower() in {"1", "true", "yes"} or os.getenv(
            "BOT_DEBUG", ""
        ).strip().lower() in {"1", "true", "yes"}

        # Avoid noisy init logs in UI runs.
        # Enable explicitly with OCR_INIT_LOG=1.
        try:
            self._log_init = (os.getenv("OCR_INIT_LOG", "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        except Exception:
            self._log_init = False

        # Global OCR kill-switch to keep the vision thread real-time.
        # When disabled, we never import/initialize EasyOCR/Torch.
        ocr_enabled_raw = (os.getenv("OCR_ENABLED", "1") or "1").strip().lower()
        self._ocr_enabled = ocr_enabled_raw in {"1", "true", "yes", "y", "on"}

        # Optional: run EasyOCR in a separate process with hard timeouts.
        # This prevents the vision thread from freezing if Torch/EasyOCR blocks.
        try:
            iso_raw = (os.getenv("OCR_ISOLATE_PROCESS", "0") or "0").strip().lower()
            self._ocr_isolate_process = bool(self._ocr_enabled) and iso_raw in {"1", "true", "yes", "y", "on"}
        except Exception:
            self._ocr_isolate_process = False
        self._ocr_isolate_fail_until_ts = 0.0
        self._ocr_isolate_client = None

        self._last_cap_debug_ts = 0.0

        # Último debug de CAP (para observabilidad y tuning).
        # Estructura típica:
        # {
        #   'roi': 123, 'panel': 456, 'panel_source': 'regex'|'bbox_row'|'',
        #   'chosen': 456, 'chosen_source': 'roi'|'panel'|'none',
        #   'decision': 'roi_only'|'panel_only'|'panel_suffix'|'panel_diff'|'roi_default'|'none'
        # }
        self.last_cap_debug: Dict[str, Any] = {}

        # Último debug de SOUL (para observabilidad y tuning).
        self.last_soul_debug: Dict[str, Any] = {}

        # Inicializar EasyOCR (lazy import).
        # Nota: En algunos entornos Windows, el backend GPU puede provocar crashes
        # nativos (sin traceback de Python). Permitimos forzar CPU por env.
        #   OCR_GPU=0  -> fuerza gpu=False
        #   OCR_GPU=1  -> intenta gpu=True (default)
        self.reader = None
        if not bool(self._ocr_enabled):
            try:
                if self._log_init:
                    print("OCR deshabilitado (OCR_ENABLED=0)")
            except Exception:
                pass
        elif bool(self._ocr_isolate_process):
            # In isolate mode, we intentionally do NOT import/initialize EasyOCR
            # in the main process; it runs in a worker process.
            self.reader = None
            try:
                if self._log_init:
                    print("OCR en proceso aislado (OCR_ISOLATE_PROCESS=1)")
            except Exception:
                pass
        else:
            try:
                import easyocr  # type: ignore

                ocr_gpu_raw = (os.getenv("OCR_GPU", "1") or "1").strip().lower()
                ocr_gpu = ocr_gpu_raw in {"1", "true", "yes", "y", "on"}
                if not ocr_gpu:
                    try:
                        self.reader = easyocr.Reader(["en"], gpu=False)
                        if self._log_init:
                            print("OCR inicializado sin GPU (OCR_GPU=0)")
                    except Exception:
                        # Last resort: still try GPU if CPU init fails.
                        self.reader = easyocr.Reader(["en"], gpu=True)
                        if self._log_init:
                            print("OCR inicializado con GPU (fallback)")
                else:
                    try:
                        self.reader = easyocr.Reader(["en"], gpu=True)
                        if self._log_init:
                            print("OCR inicializado con GPU")
                    except Exception:
                        self.reader = easyocr.Reader(["en"], gpu=False)
                        if self._log_init:
                            print("OCR inicializado sin GPU")
            except Exception:
                # Fail-safe: bot should still run without OCR.
                self.reader = None
                try:
                    if self._log_init:
                        print("OCR no disponible; continuando sin OCR")
                except Exception:
                    pass

        # Cargar correcciones OCR
        self.corrections = self._load_corrections()

        # Últimos métodos usados para HP/MP (observabilidad)
        self.last_hp_ocr_source = ""
        self.last_hp_ocr_reason = ""
        self.last_mp_ocr_source = ""
        self.last_mp_ocr_reason = ""

    def _ocr_available(self) -> bool:
        try:
            if not bool(getattr(self, "_ocr_enabled", True)):
                return False
            if bool(getattr(self, "_ocr_isolate_process", False)):
                return True
            return self.reader is not None
        except Exception:
            return self.reader is not None

    def _ocr_isolate_timeout_s(self) -> float:
        # Default is conservative: keep the vision thread realtime.
        try:
            raw = (os.getenv("OCR_READTEXT_TIMEOUT_MS", "250") or "250").strip()
            ms = float(raw) if raw else 250.0
        except Exception:
            ms = 250.0
        ms = max(10.0, float(ms))
        return float(ms) / 1000.0

    def _get_ocr_isolate_client(self):
        try:
            if self._ocr_isolate_client is not None:
                return self._ocr_isolate_client
            from vision.ocr_isolate import get_ocr_isolate_client

            self._ocr_isolate_client = get_ocr_isolate_client()
            return self._ocr_isolate_client
        except Exception:
            self._ocr_isolate_client = None
            return None

    def _readtext_detail0(
        self,
        image: np.ndarray,
        *,
        allowlist: Optional[str],
        deadline_ts: float | None = None,
    ) -> List[str]:
        if image is None or getattr(image, "size", 0) == 0:
            return []

        if not bool(self._ocr_available()):
            return []

        # Respect shared deadline when provided.
        try:
            if deadline_ts is not None and float(time.time()) >= float(deadline_ts):
                return []
        except Exception:
            pass

        if bool(getattr(self, "_ocr_isolate_process", False)):
            try:
                now = float(time.time())
                if now < float(getattr(self, "_ocr_isolate_fail_until_ts", 0.0) or 0.0):
                    return []
            except Exception:
                pass
            client = self._get_ocr_isolate_client()
            if client is None:
                return []
            try:
                strings = client.readtext_strings(
                    image,
                    allowlist=allowlist,
                    timeout_s=float(self._ocr_isolate_timeout_s()),
                )
                return list(strings or [])
            except Exception:
                try:
                    self._ocr_isolate_fail_until_ts = float(time.time()) + 0.5
                except Exception:
                    pass
                return []

        # Non-isolated (direct) OCR.
        if self.reader is None:
            return []
        try:
            res = self.reader.readtext(image, detail=0, allowlist=allowlist)
        except Exception:
            res = []
        out: List[str] = []
        for r in res or []:
            try:
                s = str(r).strip()
            except Exception:
                continue
            if s:
                out.append(s)
        return out

    def _readtext_detail1(
        self,
        image: np.ndarray,
        *,
        allowlist: Optional[str],
        deadline_ts: float | None = None,
    ) -> list:
        if image is None or getattr(image, "size", 0) == 0:
            return []
        if not bool(self._ocr_available()):
            return []
        try:
            if deadline_ts is not None and float(time.time()) >= float(deadline_ts):
                return []
        except Exception:
            pass
        # In isolate mode we intentionally avoid detail=1 paths (bbox/conf),
        # because they increase payload size and complexity.
        if bool(getattr(self, "_ocr_isolate_process", False)):
            return []
        if self.reader is None:
            return []
        try:
            return list(self.reader.readtext(image, detail=1, allowlist=allowlist) or [])
        except Exception:
            return []

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

        # Binarización adaptativa (baseline estable)
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Escalar a 2x para mejor reconocimiento
        h, w = thresh.shape[:2]
        scaled = cv2.resize(thresh, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        # Denoise
        try:
            denoised = cv2.fastNlMeansDenoising(scaled, h=10)
        except Exception:
            denoised = cv2.medianBlur(scaled, 3)

        # Sharpen suave para mejorar dígitos
        try:
            blur = cv2.GaussianBlur(denoised, (0, 0), 1.0)
            sharpened = cv2.addWeighted(denoised, 1.5, blur, -0.5, 0)
        except Exception:
            sharpened = denoised

        # Dilatar ligeramente para conectar caracteres
        kernel = np.ones((2, 2), np.uint8)
        dilated = cv2.dilate(sharpened, kernel, iterations=1)

        return dilated

    def _preprocess_no_dilate(self, image: np.ndarray) -> np.ndarray:
        """Baseline preprocess but without dilation (can help avoid merging digits)."""

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        h, w = thresh.shape[:2]
        scaled = cv2.resize(thresh, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

        try:
            denoised = cv2.fastNlMeansDenoising(scaled, h=10)
        except Exception:
            denoised = cv2.medianBlur(scaled, 3)

        try:
            blur = cv2.GaussianBlur(denoised, (0, 0), 1.0)
            sharpened = cv2.addWeighted(denoised, 1.5, blur, -0.5, 0)
        except Exception:
            sharpened = denoised

        return sharpened

    def _preprocess_otsu_3x(self, image: np.ndarray) -> np.ndarray:
        """Alternative preprocess: Otsu threshold + 3x upscale + median blur."""

        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image

        try:
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
        except Exception:
            pass

        try:
            _thr, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        except Exception:
            th = gray

        try:
            h, w = th.shape[:2]
            th = cv2.resize(th, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
        except Exception:
            pass

        try:
            th = cv2.medianBlur(th, 3)
        except Exception:
            pass

        return th

    def _preprocess_variants(self, image: np.ndarray) -> list[tuple[str, np.ndarray]]:
        """Return multiple preprocess variants for robust OCR selection."""

        variants: list[tuple[str, np.ndarray]] = []
        try:
            if image is not None and getattr(image, "size", 0) != 0:
                variants.append(("raw", image))
        except Exception:
            pass
        try:
            variants.append(("pre", self.preprocess_image(image)))
        except Exception:
            pass
        try:
            variants.append(("no_dilate", self._preprocess_no_dilate(image)))
        except Exception:
            pass
        try:
            variants.append(("otsu3x", self._preprocess_otsu_3x(image)))
        except Exception:
            pass
        try:
            variants.append(("hsv_white", self._preprocess_hsv_white_digits(image)))
        except Exception:
            pass
        return variants

    def _preprocess_hsv_white_digits(self, image: np.ndarray) -> np.ndarray:
        """Preprocess tuned for white digits on dark HUD backgrounds.

        Uses an HSV band for low saturation + high value (near-white), then
        returns a high-contrast single-channel image suitable for EasyOCR.
        """

        import cv2

        if image is None or getattr(image, "size", 0) == 0:
            return image

        hsv = cast("cv2.Mat", cv2.cvtColor(image, cv2.COLOR_BGR2HSV))

        # White-ish: low saturation, high value.
        # Keep conservative to avoid pulling in colorful HUD elements.
        # OpenCV typing stubs expect Mat for bounds as well.
        lower = cast("cv2.Mat", np.array([0, 0, 160], dtype=np.uint8))
        upper = cast("cv2.Mat", np.array([180, 60, 255], dtype=np.uint8))
        mask = cast("cv2.Mat", cv2.inRange(hsv, lower, upper))

        try:
            k = np.ones((2, 2), np.uint8)
            mask = cast("cv2.Mat", cv2.morphologyEx(mask, cv2.MORPH_OPEN, k))
            mask = cast("cv2.Mat", cv2.dilate(mask, k, iterations=1))
        except Exception:
            pass

        # EasyOCR tends to like dark text on light background.
        img = cast(np.ndarray, 255 - cast(np.ndarray, mask))

        try:
            h, w = img.shape[:2]
            img = cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        except Exception:
            pass

        return img

    def extract_text_best(self, image: np.ndarray, *, allowlist: Optional[str] = None) -> str:
        """Extract best candidate string (multi-variant, multi-result).

        Keeps API compatible with `extract_text`, but is more robust:
        - considers multiple OCR candidates (detail=1)
        - tries multiple preprocess variants
        """

        if image is None or getattr(image, "size", 0) == 0:
            return ""

        # Global OCR disabled / reader not available.
        if not bool(self._ocr_available()):
            return ""

        best_text = ""
        best_score = -1e9

        for _name, img in self._preprocess_variants(image):
            try:
                results = self._readtext_detail1(img, allowlist=allowlist)
            except Exception:
                results = []

            # Isolated OCR path: no bbox/conf, so score based on string heuristics.
            if not results and bool(getattr(self, "_ocr_isolate_process", False)):
                try:
                    strings = self._readtext_detail0(img, allowlist=allowlist)
                except Exception:
                    strings = []
                for s0 in strings or []:
                    try:
                        raw = str(s0 or "")
                    except Exception:
                        raw = ""
                    cleaned = re.sub(r"[^0-9/|]", "", raw).replace("|", "/")
                    cleaned = str(self.corrections.get(cleaned, cleaned))
                    if not cleaned:
                        continue
                    digits = len(re.findall(r"\d", cleaned))
                    has_sep = 1 if "/" in cleaned else 0
                    looks_cur_max = 1 if re.search(r"\d{1,7}\s*/\s*\d{1,7}", cleaned) else 0
                    score = (looks_cur_max * 10.0) + (has_sep * 2.0) + (digits * 0.1)
                    if score > best_score:
                        best_score = score
                        best_text = cleaned
                continue

            for item in results or []:
                try:
                    _bbox, text, conf = item
                except Exception:
                    continue

                try:
                    raw = str(text or "")
                except Exception:
                    raw = ""

                # Limpiar: solo números y separadores /|
                cleaned = re.sub(r"[^0-9/|]", "", raw)
                cleaned = cleaned.replace("|", "/")
                cleaned = str(self.corrections.get(cleaned, cleaned))
                if not cleaned:
                    continue

                # Heurística simple de scoring.
                try:
                    conf_f = float(conf) if conf is not None else 0.0
                except Exception:
                    conf_f = 0.0

                digits = len(re.findall(r"\d", cleaned))
                has_sep = 1 if "/" in cleaned else 0
                looks_cur_max = 1 if re.search(r"\d{1,7}\s*/\s*\d{1,7}", cleaned) else 0

                score = (conf_f * 10.0) + (looks_cur_max * 10.0) + (has_sep * 2.0) + (digits * 0.1)
                if score > best_score:
                    best_score = score
                    best_text = cleaned

        if self._debug and best_text:
            try:
                print(f"OCR best: '{best_text}' score={best_score:.2f}")
            except Exception:
                pass

        return best_text

    def extract_text(self, image: np.ndarray, *, allowlist: Optional[str] = None) -> str:
        """Extrae texto de una imagen usando OCR"""
        try:
            # Backward compatible API: now delegates to best-candidate selection.
            return self.extract_text_best(image, allowlist=allowlist)
        except Exception:
            return ""

    def _readtext_strings(
        self,
        image: np.ndarray,
        *,
        allowlist: Optional[str] = None,
        deadline_ts: float | None = None,
    ) -> List[str]:
        """Best-effort OCR returning a list of raw strings.

        Central helper used by multiple extraction paths.
        """

        if image is None or getattr(image, "size", 0) == 0:
            return []

        # Global OCR disabled / reader not available.
        if not bool(self._ocr_available()):
            return []

        out: List[str] = []
        seen: set[str] = set()

        for _name, img in self._preprocess_variants(image):
            try:
                if deadline_ts is not None and float(time.time()) >= float(deadline_ts):
                    break
            except Exception:
                pass
            res = self._readtext_detail0(img, allowlist=allowlist, deadline_ts=deadline_ts)
            for r in res or []:
                try:
                    s = str(r).strip()
                except Exception:
                    continue
                if not s:
                    continue
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)

        return out

    def _maybe_dump_hpmp_failure(
        self,
        *,
        crops: dict[str, np.ndarray],
        meta: dict[str, object],
    ) -> None:
        """Optional: dump HP/MP crops + preprocess variants when OCR fails."""

        # Strict gating: only dump when OCR_DEBUG=1.
        if not (os.getenv("OCR_DEBUG", "").strip().lower() in {"1", "true", "yes"}):
            return
        if not (os.getenv("HPMP_DUMP_ON_FAIL", "").strip().lower() in {"1", "true", "yes"}):
            return

        try:
            keep = int(float(os.getenv("HPMP_DUMP_N", "10").strip() or "10"))
        except Exception:
            keep = 10
        keep = max(1, int(keep))

        out_dir = Path((os.getenv("HPMP_DUMP_DIR", "logs/hpmp_dump") or "logs/hpmp_dump").strip())
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

        ts = float(time.time())
        base = out_dir / f"{ts:.6f}"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            return

        # Write JSON meta
        try:
            (base / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        except Exception:
            pass

        # Write images (raw + variants)
        try:
            import cv2

            for name, crop in (crops or {}).items():
                if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
                    continue
                try:
                    cv2.imwrite(str(base / f"{name}_raw.png"), crop)
                except Exception:
                    pass

                try:
                    for vname, vimg in self._preprocess_variants(crop):
                        if vimg is None or not isinstance(vimg, np.ndarray) or vimg.size == 0:
                            continue
                        try:
                            cv2.imwrite(str(base / f"{name}_{vname}.png"), vimg)
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception:
            pass

        # Prune old dumps (keep latest N)
        try:
            dirs = [p for p in out_dir.iterdir() if p.is_dir()]
            dirs.sort(key=lambda p: p.name)
            extra = max(0, len(dirs) - keep)
            for p in dirs[:extra]:
                try:
                    for child in p.glob("**/*"):
                        try:
                            if child.is_file():
                                child.unlink(missing_ok=True)
                        except Exception:
                            pass
                    try:
                        p.rmdir()
                    except Exception:
                        pass
                except Exception:
                    continue
        except Exception:
            pass

    def _set_last_ocr_meta(self, *, kind: str, source: str, reason: str) -> None:
        """Store per-tick OCR observability for HP/MP."""

        k = str(kind or "").strip().lower()
        try:
            if k == "hp":
                self.last_hp_ocr_source = str(source or "")
                self.last_hp_ocr_reason = str(reason or "")
            elif k == "mp":
                self.last_mp_ocr_source = str(source or "")
                self.last_mp_ocr_reason = str(reason or "")
        except Exception:
            pass

    @staticmethod
    def _parse_capacity_from_text(text: str) -> Optional[int]:
        """Parse CAP value from OCR text."""

        try:
            s = str(text or "")
        except Exception:
            return None

        # CAP is usually displayed as a labeled field (e.g. "Cap: 2800").
        # Avoid the old "max number anywhere" heuristic: the skills panel contains
        # many unrelated numbers (level, skills, timers), which can create large
        # false positives.
        #
        # OCR sometimes misreads letters as digits (e.g. "C4p").
        # IMPORTANT: do NOT normalize digits into letters globally because that
        # would corrupt the numeric payload (e.g. "Cap: 2800" -> "Cap: 2boo").
        try:
            s2 = s.lower()
        except Exception:
            s2 = s

        try:
            m = re.search(
                r"(?:\bcap\b|\bc[a4]p\b|\bcapacity\b|\bcapac(?:ity)?\b)\D{0,12}(\d{1,6})",
                s2,
                flags=re.IGNORECASE,
            )
            if m:
                return int(m.group(1))
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_soul_from_text(text: str) -> Optional[int]:
        """Parse SOUL value from OCR text."""

        try:
            s = str(text or "")
        except Exception:
            return None

        try:
            s2 = s.lower()
            s2 = (
                s2.replace("0", "o")
                .replace("1", "l")
                .replace("|", "l")
                .replace("5", "s")
            )
        except Exception:
            s2 = s

        # Tibia soul is usually small (<= 200), but keep it a bit wider.
        try:
            m = re.search(r"(?:\bsoul\b|\bsoul\s*:)\D{0,12}(\d{1,4})", s2, flags=re.IGNORECASE)
            if m:
                return int(m.group(1))
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_coords_from_text(text: str) -> tuple[int, int, int | None] | None:
        """Parse coords from OCR text.

        Accepts formats like:
        - "X: 12345 Y: 67890 Z: 7"
        - "12345,67890,7" or "12345 67890"
        """

        try:
            s = str(text or "").strip()
        except Exception:
            return None

        if not s:
            return None

        nums = re.findall(r"-?\d+", s)
        if len(nums) < 2:
            return None

        try:
            x = int(nums[0])
            y = int(nums[1])
            z = int(nums[2]) if len(nums) >= 3 else None
            return x, y, z
        except Exception:
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
                candidates.extend(self._readtext_detail0(crop, allowlist="0123456789XYZxyz:,- "))
            except Exception:
                pass

            # Then try preprocessed OCR.
            try:
                processed = self.preprocess_image(crop)
                candidates.extend(self._readtext_detail0(processed, allowlist="0123456789XYZxyz:,- "))
            except Exception:
                pass

            # Also join any raw strings to increase parse success.
            try:
                candidates.extend(self._readtext_strings(crop, allowlist=None))
            except Exception:
                pass

            # Sanity-check parsed coords: prevents false positives when ROI drifts.
            try:
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

            # Fallback: pick first 2 large numbers in-range.
            try:
                nums_all = [int(n) for n in re.findall(r"-?\d+", joined)]
                nums_big = [v for v in nums_all if (min_xy <= abs(int(v)) <= max_xy)]
                if len(nums_big) >= 2:
                    x2 = int(nums_big[0])
                    y2 = int(nums_big[1])
                    z2: int | None = None
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

        # Allow disabling CAP OCR specifically (to keep vision realtime).
        try:
            cap_on = (os.getenv("CAP_OCR_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "y", "on"}
        except Exception:
            cap_on = True
        if not bool(cap_on):
            try:
                self.last_cap_debug = {
                    "roi": None,
                    "panel": None,
                    "panel_source": "",
                    "chosen": None,
                    "chosen_source": "none",
                    "decision": "disabled",
                }
            except Exception:
                pass
            return None

        # Global OCR disabled / reader not available.
        if not bool(self._ocr_available()):
            try:
                self.last_cap_debug = {
                    "roi": None,
                    "panel": None,
                    "panel_source": "",
                    "chosen": None,
                    "chosen_source": "none",
                    "decision": "no_reader",
                }
            except Exception:
                pass
            return None

        def normalize_to_px(roi_def: Dict[str, Any]) -> Tuple[int, int, int, int]:
            return self._roi_to_px(frame, rois, resolution, roi_def)

        def _reconcile(v_roi: Optional[int], v_panel: Optional[int], *, panel_source: str) -> tuple[Optional[int], str, str]:
            """Return (chosen_value, chosen_source, decision_reason)."""

            if v_roi is None and v_panel is None:
                return None, "none", "none"
            if v_roi is None:
                return v_panel, "panel", "panel_only"
            if v_panel is None:
                return v_roi, "roi", "roi_only"

            try:
                s_roi = str(int(v_roi))
                s_pan = str(int(v_panel))
            except Exception:
                return v_roi, "roi", "roi_default"

            src = str(panel_source or "").strip().lower()

            # Trust levels:
            # - trusted: CAP-specific parse (anchored to CAP label)
            # - weak: heuristic max-number fallback inside skills_panel (ONLY useful when ROI is missing)
            trusted_panel = src in {"regex", "bbox_row"}

            # Common failure mode: cap_ocr ROI captures only the last digits.
            # If panel value ends with roi value and is longer, trust the panel.
            try:
                if trusted_panel and len(s_pan) > len(s_roi) and s_pan.endswith(s_roi):
                    return int(v_panel), "panel", "panel_suffix"
            except Exception:
                pass

            # Another common truncation case: ROI reads a short (<=3 digit) tail
            # while the panel (CAP-labeled) shows a 4+ digit number.
            try:
                if trusted_panel and len(s_pan) > len(s_roi) and len(s_roi) <= 3 and len(s_pan) >= 4:
                    return int(v_panel), "panel", "panel_more_digits"
            except Exception:
                pass

            # Optional heuristic: if panel is significantly larger, it may be the full number.
            # This is risky because skills_panel can contain many unrelated numbers.
            # Keep it OFF by default.
            try:
                allow_diff = (os.getenv("CAP_RECONCILE_ALLOW_DIFF", "") or "").strip().lower() in {"1", "true", "yes"}
            except Exception:
                allow_diff = False

            if allow_diff:
                try:
                    if int(v_panel) > int(v_roi):
                        diff = int(v_panel) - int(v_roi)
                        thr = max(50, int(abs(int(v_roi)) * 0.5))
                        if trusted_panel and diff >= thr:
                            return int(v_panel), "panel", "panel_diff"
                except Exception:
                    pass

            return int(v_roi), "roi", "roi_default"

        cap_from_roi: Optional[int] = None
        cap_from_panel: Optional[int] = None
        panel_source: str = ""

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
                        overlaps = (sx < x1) and ((sx + sw) > x0) and (sy < y1) and ((sy + sh) > y0)
                        if overlaps:
                            cap_cx = float(x) + float(w) * 0.5
                            soul_cx = float(sx) + float(sw) * 0.5
                            if soul_cx <= cap_cx:
                                # Soul is left of cap: keep crop start after soul.
                                x0 = max(x0, int(sx + sw + 2))
                            else:
                                # Soul is right of cap: keep crop end before soul.
                                x1 = min(x1, max(0, int(sx - 2)))
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
                    raw_res = self._readtext_detail1(crop, allowlist="0123456789")
                    v_raw = _best_single_box_number(raw_res)
                    if v_raw is not None:
                        cap_from_roi = int(v_raw)
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

                if cap_from_roi is None:
                    try:
                        processed = self.preprocess_image(crop)
                        res = self._readtext_detail1(processed, allowlist="0123456789")
                        v = _read_number_from_digit_boxes(res)
                        if v is not None:
                            cap_from_roi = int(v)
                    except Exception:
                        pass

                if cap_from_roi is None:
                    try:
                        processed = self.preprocess_image(crop)
                        inv = cv2.bitwise_not(processed)
                        res = self._readtext_detail1(inv, allowlist="0123456789")
                        v = _read_number_from_digit_boxes(res)
                        if v is not None:
                            cap_from_roi = int(v)
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
                if nums and cap_from_roi is None:
                    try:
                        cap_from_roi = int(max(nums, key=lambda s: int(s)))
                    except Exception:
                        cap_from_roi = None
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
                    cap_from_panel = int(cap)
                    panel_source = "regex"

                # BBox-based fallback: find the cap/capacity label and read the number on the same row.
                try:
                    def _norm_label(s: str) -> str:
                        try:
                            return re.sub(r"[^a-z]", "", str(s or "").lower())
                        except Exception:
                            return ""

                    def _extract_bbox_row_cap(results) -> tuple[int | None, float]:
                        """Return (cap_value, score).

                        Supports HUD layouts where the numeric value is either:
                        - to the RIGHT of the 'cap' label (same row)
                        - BELOW the 'cap' label (common Tibia skills panel layouts)
                        """

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

                        cap_labels = [
                            e
                            for e in entries
                            if (lambda t: ("cap" in t or "capac" in t))(_norm_label(e[0]))
                        ]
                        if not cap_labels:
                            return None, 0.0

                        cap_label = max(cap_labels, key=lambda e: e[1])
                        _s, label_conf, lx0, ly0, lx1, ly1 = cap_label
                        ly_mid = (ly0 + ly1) / 2.0
                        lx_mid = (lx0 + lx1) / 2.0

                        # Tolerances scale with label size.
                        tol_y = max(10.0, (ly1 - ly0) * 1.8)
                        tol_x = max(12.0, (lx1 - lx0) * 1.6)
                        tol_down = max(18.0, (ly1 - ly0) * 5.0)

                        # Candidate tuples: (num_conf, dist, value)
                        cands: list[tuple[float, float, int]] = []

                        for s, conf, bx0, by0, bx1, by1 in entries:
                            found = [int(n) for n in re.findall(r"\d{1,6}", str(s or ""))]
                            if not found:
                                continue

                            y_mid = (by0 + by1) / 2.0
                            x_mid = (bx0 + bx1) / 2.0

                            # (A) Right-of-label, same row.
                            if bx0 > lx1:
                                if abs(y_mid - ly_mid) <= tol_y:
                                    dist = abs(y_mid - ly_mid) + max(0.0, bx0 - lx1) * 0.05
                                    for v in found:
                                        cands.append((float(conf or 0.0), float(dist), int(v)))

                            # (B) Below-label: allow overlap in X and require it to be under the label.
                            below_ok = (by0 >= (ly1 - tol_y * 0.25)) and ((bx1 >= (lx0 - tol_x)) and (bx0 <= (lx1 + tol_x)))
                            if below_ok:
                                dy = float(y_mid - ly1)
                                if 0.0 <= dy <= tol_down:
                                    dist = dy + abs(x_mid - lx_mid) * 0.12
                                    for v in found:
                                        cands.append((float(conf or 0.0), float(dist), int(v)))

                        if not cands:
                            return None, float(label_conf)

                        # Prefer higher OCR confidence; then closer geometry; then larger value.
                        cands.sort(key=lambda t: (t[0], -t[1], t[2]), reverse=True)
                        best_conf, best_dist, best_val = cands[0]

                        # Combined score used to compare preprocess variants.
                        score = float(label_conf) * 0.6 + float(best_conf) * 0.4
                        try:
                            score = float(score) - min(0.25, float(best_dist) / 400.0)
                        except Exception:
                            pass
                        if score < 0.0:
                            score = 0.0
                        if score > 1.0:
                            score = 1.0
                        return int(best_val), float(score)

                    # detail=1: [ (bbox, text, conf), ... ] where bbox has 4 points
                    cap_candidates: list[tuple[int | None, float, str]] = []
                    try:
                        res_raw = self._readtext_detail1(crop, allowlist=None)
                        v, lc = _extract_bbox_row_cap(res_raw)
                        cap_candidates.append((v, lc, "raw"))
                    except Exception:
                        pass
                    try:
                        processed = self.preprocess_image(crop)
                        res_proc = self._readtext_detail1(processed, allowlist=None)
                        v, lc = _extract_bbox_row_cap(res_proc)
                        cap_candidates.append((v, lc, "proc"))
                    except Exception:
                        pass
                    try:
                        processed = self.preprocess_image(crop)
                        inv = cv2.bitwise_not(processed)
                        res_inv = self._readtext_detail1(inv, allowlist=None)
                        v, lc = _extract_bbox_row_cap(res_inv)
                        cap_candidates.append((v, lc, "inv"))
                    except Exception:
                        pass

                    best = None
                    for v, lc, _src in cap_candidates:
                        if v is None:
                            continue
                        key = (float(lc), int(v))
                        if best is None or key > best[0]:
                            best = (key, int(v))
                    if best is not None:
                        cap_from_panel = int(best[1])
                        panel_source = "bbox_row"
                except Exception:
                    pass

                # Last-resort panel fallback (OFF by default): picking the max number
                # in the skills panel is very error-prone (can grab unrelated values).
                # Enable explicitly only for debugging.
                try:
                    allow_max_any = (os.getenv("CAP_ALLOW_MAX_ANY", "0") or "0").strip().lower() in {"1", "true", "yes"}
                except Exception:
                    allow_max_any = False

                if allow_max_any:
                    try:
                        if cap_from_panel is None:
                            nums = [int(n) for n in re.findall(r"\d{1,6}", joined)]
                            if nums:
                                cap_from_panel = int(max(nums))
                                panel_source = "max_any"
                    except Exception:
                        pass
        except Exception:
            pass

        chosen, chosen_source, decision = _reconcile(cap_from_roi, cap_from_panel, panel_source=panel_source)
        try:
            self.last_cap_debug = {
                "roi": cap_from_roi,
                "panel": cap_from_panel,
                "panel_source": panel_source,
                "chosen": chosen,
                "chosen_source": chosen_source,
                "decision": decision,
            }
        except Exception:
            pass

        return chosen

    def extract_soul(self, frame: np.ndarray, rois: Mapping[str, Any], resolution: Tuple[int, int]) -> Optional[int]:
        """Extrae SOUL (si hay ROI `soul_ocr`).

        Intencionalmente simple (sin heurística global del panel) para evitar
        capturar números no relacionados.

        Env vars:
          - SOUL_OCR_ENABLED=0 para deshabilitar
          - SOUL_OCR_DEBUG_SNAP=1 para guardar crops en logs/debug_soul/
        """

        try:
            soul_on = (os.getenv("SOUL_OCR_ENABLED", "1") or "1").strip().lower() in {"1", "true", "yes", "y", "on"}
        except Exception:
            soul_on = True
        if not soul_on:
            return None

        if not bool(getattr(self, "_ocr_enabled", True)):
            return None
        if not bool(self._ocr_available()):
            return None

        def normalize_to_px(roi_def: Dict[str, Any]) -> Tuple[int, int, int, int]:
            return self._roi_to_px(frame, rois, resolution, roi_def)

        soul_from_roi: Optional[int] = None
        soul_from_panel: Optional[int] = None
        panel_source: str = ""

        try:
            if hasattr(rois, "get") and rois.get("soul_ocr") is not None:
                x, y, w, h = normalize_to_px(cast(Dict[str, Any], rois["soul_ocr"]))

                cap_bounds: Optional[Tuple[int, int, int, int]] = None
                try:
                    if hasattr(rois, "get") and rois.get("cap_ocr") is not None:
                        cap_bounds = normalize_to_px(cast(Dict[str, Any], rois["cap_ocr"]))
                except Exception:
                    cap_bounds = None

                # Expand: digits can be tiny and ROIs can drift.
                x0 = max(0, int(x - (w * 2.0)))
                y0 = max(0, int(y - (h * 1.0)))
                x1 = min(int(frame.shape[1]), int(x + w + (w * 1.5)))
                y1 = min(int(frame.shape[0]), int(y + h + (h * 1.0)))

                # Avoid expanding into CAP ROI when they are very close.
                try:
                    if cap_bounds is not None:
                        cx, cy, cw, ch = cap_bounds
                        overlaps = (cx < x1) and ((cx + cw) > x0) and (cy < y1) and ((cy + ch) > y0)
                        if overlaps:
                            soul_cx = float(x) + float(w) * 0.5
                            cap_cx = float(cx) + float(cw) * 0.5
                            if cap_cx >= soul_cx:
                                x1 = min(x1, max(0, int(cx - 2)))
                            else:
                                x0 = max(x0, int(cx + cw + 2))
                except Exception:
                    pass

                crop = frame[y0:y1, x0:x1]

                # Optional debug snapshot of the exact crop used for SOUL OCR.
                try:
                    if self._debug and os.getenv("SOUL_OCR_DEBUG_SNAP", "").strip().lower() in {"1", "true", "yes"}:
                        import time as _time

                        now = float(_time.time())
                        if (now - float(getattr(self, "_last_soul_debug_ts", 0.0))) >= 1.0:
                            setattr(self, "_last_soul_debug_ts", now)
                            try:
                                os.makedirs("logs/debug_soul", exist_ok=True)
                                cv2.imwrite(f"logs/debug_soul/{now:.6f}_soul_crop.png", crop)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Extra upscale for tiny digits.
                try:
                    crop_up = cv2.resize(crop, (0, 0), fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
                except Exception:
                    crop_up = crop

                def _best_single_box_number(results) -> Optional[int]:
                    best: tuple[float, int, int] | None = None  # (conf, n_digits, value)
                    for _bbox, text, conf in results or []:
                        try:
                            s = re.sub(r"[^0-9]", "", str(text or ""))
                            if not s:
                                continue
                            if not (1 <= len(s) <= 4):
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
                    res = self._readtext_detail1(crop_up, allowlist="0123456789")
                    soul_from_roi = _best_single_box_number(res)
                except Exception:
                    soul_from_roi = None

                if soul_from_roi is None:
                    try:
                        processed = self.preprocess_image(crop_up)
                        res = self._readtext_detail1(processed, allowlist="0123456789")
                        soul_from_roi = _best_single_box_number(res)
                    except Exception:
                        soul_from_roi = None

                if soul_from_roi is None:
                    try:
                        processed = self.preprocess_image(crop_up)
                        inv = cv2.bitwise_not(processed)
                        res = self._readtext_detail1(inv, allowlist="0123456789")
                        soul_from_roi = _best_single_box_number(res)
                    except Exception:
                        soul_from_roi = None
        except Exception:
            soul_from_roi = None

        # (2) Safe fallback: parse "Soul: <n>" inside skills_panel.
        # This stays conservative: ONLY accept labeled parses (regex/bbox-row).
        try:
            if hasattr(rois, "get") and rois.get("skills_panel") is not None:
                x, y, w, h = normalize_to_px(cast(Dict[str, Any], rois["skills_panel"]))
                crop = frame[y : y + h, x : x + w]

                texts = self._readtext_strings(crop, allowlist=None)
                joined = " ".join(texts)
                v = self._parse_soul_from_text(joined)
                if v is not None:
                    soul_from_panel = int(v)
                    panel_source = "regex"

                # BBox-based row parse: find "soul" label and number on same row.
                try:
                    def _norm_label_soul(s: str) -> str:
                        try:
                            t = re.sub(r"[^a-z0-9]", "", str(s or "").lower())
                            t = (
                                t.replace("0", "o")
                                .replace("1", "l")
                                .replace("|", "l")
                                .replace("5", "s")
                            )
                            return t
                        except Exception:
                            return ""

                    def _extract_bbox_row_soul(results) -> tuple[int | None, float]:
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

                        soul_labels = [e for e in entries if "soul" in _norm_label_soul(e[0])]
                        if not soul_labels:
                            return None, 0.0

                        soul_label = max(soul_labels, key=lambda e: e[1])
                        _s, label_conf, lx0, ly0, lx1, ly1 = soul_label
                        ly_mid = (ly0 + ly1) / 2.0
                        lx_mid = (lx0 + lx1) / 2.0

                        tol_y = max(10.0, (ly1 - ly0) * 1.8)
                        tol_x = max(12.0, (lx1 - lx0) * 1.6)
                        tol_down = max(18.0, (ly1 - ly0) * 5.0)

                        cands: list[tuple[float, float, int]] = []
                        for s, conf, bx0, by0, bx1, by1 in entries:
                            found = [int(n) for n in re.findall(r"\d{1,4}", str(s or ""))]
                            if not found:
                                continue

                            y_mid = (by0 + by1) / 2.0
                            x_mid = (bx0 + bx1) / 2.0

                            # (A) Right-of-label (same row)
                            if bx0 > lx1 and abs(y_mid - ly_mid) <= tol_y:
                                dist = abs(y_mid - ly_mid) + max(0.0, bx0 - lx1) * 0.05
                                for vv in found:
                                    cands.append((float(conf or 0.0), float(dist), int(vv)))

                            # (B) Below-label (overlapping column)
                            below_ok = (by0 >= (ly1 - tol_y * 0.25)) and ((bx1 >= (lx0 - tol_x)) and (bx0 <= (lx1 + tol_x)))
                            if below_ok:
                                dy = float(y_mid - ly1)
                                if 0.0 <= dy <= tol_down:
                                    dist = dy + abs(x_mid - lx_mid) * 0.12
                                    for vv in found:
                                        cands.append((float(conf or 0.0), float(dist), int(vv)))

                        if not cands:
                            return None, float(label_conf)

                        cands.sort(key=lambda t: (t[0], -t[1], t[2]), reverse=True)
                        best_conf, best_dist, best_val = cands[0]

                        score = float(label_conf) * 0.6 + float(best_conf) * 0.4
                        try:
                            score = float(score) - min(0.25, float(best_dist) / 400.0)
                        except Exception:
                            pass
                        if score < 0.0:
                            score = 0.0
                        if score > 1.0:
                            score = 1.0
                        return int(best_val), float(score)

                    soul_candidates: list[tuple[int | None, float, str]] = []
                    try:
                        res_raw = self._readtext_detail1(crop, allowlist=None)
                        v0, lc0 = _extract_bbox_row_soul(res_raw)
                        soul_candidates.append((v0, lc0, "raw"))
                    except Exception:
                        pass
                    try:
                        processed = self.preprocess_image(crop)
                        res_proc = self._readtext_detail1(processed, allowlist=None)
                        v0, lc0 = _extract_bbox_row_soul(res_proc)
                        soul_candidates.append((v0, lc0, "proc"))
                    except Exception:
                        pass
                    try:
                        processed = self.preprocess_image(crop)
                        inv = cv2.bitwise_not(processed)
                        res_inv = self._readtext_detail1(inv, allowlist=None)
                        v0, lc0 = _extract_bbox_row_soul(res_inv)
                        soul_candidates.append((v0, lc0, "inv"))
                    except Exception:
                        pass

                    best = None
                    for v0, lc0, _src0 in soul_candidates:
                        if v0 is None:
                            continue
                        key = (float(lc0), int(v0))
                        if best is None or key > best[0]:
                            best = (key, int(v0))
                    if best is not None:
                        soul_from_panel = int(best[1])
                        panel_source = "bbox_row"
                except Exception:
                    pass
        except Exception:
            pass

        # Reconcile ROI vs panel (prefer trusted label-based panel parse).
        chosen = None
        chosen_source = "none"
        decision = "no_digits"

        if soul_from_roi is None and soul_from_panel is None:
            chosen = None
            chosen_source = "none"
            decision = "no_digits"
        elif soul_from_roi is None:
            chosen = soul_from_panel
            chosen_source = "panel"
            decision = "panel_only"
        elif soul_from_panel is None:
            chosen = soul_from_roi
            chosen_source = "roi"
            decision = "roi_only"
        else:
            trusted_panel = str(panel_source or "").strip().lower() in {"regex", "bbox_row"}
            s_roi = str(int(soul_from_roi))
            s_pan = str(int(soul_from_panel))
            # Handle truncation: ROI often captures only the last digit(s).
            if trusted_panel and len(s_pan) > len(s_roi) and s_pan.endswith(s_roi):
                chosen = int(soul_from_panel)
                chosen_source = "panel"
                decision = "panel_suffix"
            elif trusted_panel and len(s_pan) > len(s_roi) and len(s_roi) <= 2:
                chosen = int(soul_from_panel)
                chosen_source = "panel"
                decision = "panel_more_digits"
            else:
                chosen = int(soul_from_roi)
                chosen_source = "roi"
                decision = "roi_default"

        try:
            self.last_soul_debug = {
                "roi": soul_from_roi,
                "panel": soul_from_panel,
                "panel_source": panel_source,
                "chosen": chosen,
                "chosen_source": chosen_source,
                "decision": decision,
            }
        except Exception:
            pass

        return chosen

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

        # Two mapping modes:
        # - Letterbox mode (uniform scale + centered offsets): for frames that contain
        #   the source content surrounded by black bars.
        # - Crop/scale mode (independent x/y scale, no offsets): for captures where
        #   the source is simply cropped/resized (common in window capture when the
        #   client height differs from the profile, e.g., 1920x1032 vs 1920x1080).
        #
        # ROI transform mode selection.
        # Default "auto" preserves current behavior.
        #
        # Env: ROI_TRANSFORM_MODE = auto|letterbox|crop_scale|crop_only
        # - letterbox: uniform scale + centered offsets (classic)
        # - crop_scale: independent x/y scaling, no offsets (best for resized/cropped captures)
        # - crop_only: no scaling, no offsets (best for pure pixel-crop from top-left)
        try:
            import os

            mode = (os.getenv("ROI_TRANSFORM_MODE", "auto") or "auto").strip().lower()
        except Exception:
            mode = "auto"

        # Auto transform heuristics.
        #
        # In real Tibia window capture, it's common to see frames like 1920x1032
        # for profiles authored at 1920x1080 (window borders/titlebar). That is
        # a *crop*, not a scale. For crops we should NOT rescale coordinates,
        # otherwise small HUD ROIs (cap/soul/hungry) drift and OCR becomes noisy.
        auto_choice = "letterbox"
        if mode == "auto":
            try:
                dw = int(frame_w) - int(source_w)
                dh = int(frame_h) - int(source_h)
            except Exception:
                dw, dh = 0, 0

            try:
                max_crop_delta = int(float((os.getenv("ROI_TRANSFORM_CROP_ONLY_MAX_DELTA_PX", "120") or "120").strip() or "120"))
            except Exception:
                max_crop_delta = 120
            max_crop_delta = max(0, int(max_crop_delta))

            # Small negative deltas typically indicate window cropping.
            try:
                if int(frame_w) == int(source_w) and int(frame_h) < int(source_h) and abs(int(dh)) <= max_crop_delta:
                    auto_choice = "crop_only"
                elif int(frame_h) == int(source_h) and int(frame_w) < int(source_w) and abs(int(dw)) <= max_crop_delta:
                    auto_choice = "crop_only"
                elif int(frame_w) < int(source_w) and int(frame_h) < int(source_h) and (abs(int(dw)) <= max_crop_delta or abs(int(dh)) <= max_crop_delta):
                    auto_choice = "crop_only"
                # When both dimensions differ, it's more likely a resize/scale.
                elif int(frame_w) != int(source_w) and int(frame_h) != int(source_h):
                    auto_choice = "crop_scale"
                else:
                    auto_choice = "letterbox"
            except Exception:
                auto_choice = "letterbox"

        if mode in {"crop_only", "crop"}:
            scale_x = 1.0
            scale_y = 1.0
            offset_x = 0.0
            offset_y = 0.0
        elif mode in {"crop_scale", "scale"} or (mode == "auto" and auto_choice == "crop_scale"):
            scale_x = (frame_w / source_w) if source_w else 1.0
            scale_y = (frame_h / source_h) if source_h else 1.0
            offset_x = 0.0
            offset_y = 0.0
        elif mode == "auto" and auto_choice == "crop_only":
            scale_x = 1.0
            scale_y = 1.0
            offset_x = 0.0
            offset_y = 0.0
        else:
            # letterbox (or auto fallback)
            scale = min(frame_w / source_w, frame_h / source_h) if source_w and source_h else 1.0
            scale_x = scale
            scale_y = scale
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

        x = int(round(offset_x + x_src * float(scale_x)))
        y = int(round(offset_y + y_src * float(scale_y)))
        w = int(round(w_src * float(scale_x)))
        h = int(round(h_src * float(scale_y)))

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

        # Reset per-call OCR meta
        self._set_last_ocr_meta(kind="hp", source="", reason="")
        self._set_last_ocr_meta(kind="mp", source="", reason="")

        # Fast path: disable HP/MP OCR entirely (use bars fallback in GameStateBuilder).
        # This is useful for CPU-only environments where EasyOCR can be too slow.
        try:
            raw = (os.getenv("HPMP_OCR_ENABLED", "1") or "1").strip().lower()
            if raw in {"0", "false", "no", "off"}:
                self._set_last_ocr_meta(kind="hp", source="disabled", reason="HPMP_OCR_ENABLED=0")
                self._set_last_ocr_meta(kind="mp", source="disabled", reason="HPMP_OCR_ENABLED=0")
                return None, None, None, None
        except Exception:
            pass

        # Optional time budget for HP/MP OCR to avoid stalling the vision thread.
        # When set (ms > 0), all OCR calls inside this function share the same deadline.
        # Example: HPMP_OCR_MAX_MS=250
        hpmp_deadline_ts: float | None = None
        try:
            raw = (os.getenv("HPMP_OCR_MAX_MS", "0") or "0").strip()
            max_ms = float(raw) if raw else 0.0
            if max_ms > 0.0:
                hpmp_deadline_ts = float(time.time()) + (float(max_ms) / 1000.0)
        except Exception:
            hpmp_deadline_ts = None

        # Some OCR outputs contain a single large number that can be unrelated
        # to HP/MP (e.g., timestamps, stamina, UI digits). Disable the
        # single-number fallback by default to avoid polluting telemetry.
        try:
            allow_single_number = (os.getenv("HPMP_ALLOW_SINGLE_NUMBER", "0") or "0").strip().lower() in {
                "1",
                "true",
                "yes",
                "y",
                "on",
            }
        except Exception:
            allow_single_number = False

        # Top-strip OCR can be noisy when dedicated top OCR ROIs exist.
        # Default: only use it when the dedicated ROIs are missing, unless
        # explicitly enabled.
        try:
            force_strip = (os.getenv("HPMP_TOP_STRIP_OCR", "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        except Exception:
            force_strip = False
        use_top_strip = bool(force_strip or ("hp_top_ocr" not in rois and "mp_top_ocr" not in rois))

        try:
            # Función para convertir coordenadas normalizadas a píxeles.
            # Importante: las ROIs suelen estar definidas para una "source_resolution" (p.ej. 1920x1080),
            # pero el frame capturado puede tener otra (p.ej. proyector 1920x1009). Reescalamos.
            def normalize_to_px(roi_def: Dict[str, Any]) -> Tuple[int, int, int, int]:
                return self._roi_to_px(frame, rois, resolution, roi_def)

            def _valid_roi(roi: Tuple[int, int, int, int]) -> tuple[bool, str]:
                try:
                    x, y, w, h = roi
                    if w < 6 or h < 6:
                        return False, "too_small"
                    if x < 0 or y < 0:
                        return False, "negative"
                    if x + w > frame.shape[1] or y + h > frame.shape[0]:
                        return False, "out_of_bounds"
                    return True, "ok"
                except Exception:
                    return False, "invalid"

            def _pick_best_cur_max(texts: list[str], label: str) -> tuple[Optional[int], Optional[int], str]:
                best = None
                best_score = -1
                try:
                    max_value = int(float(os.getenv("HPMP_MAX_OCR", "100000").strip() or "100000"))
                except Exception:
                    max_value = 100000
                max_value = int(max(1000, max_value))
                for t in texts:
                    cur, mx, reason = self._parse_current_and_max_with_reason(str(t), label)
                    if mx is None:
                        continue
                    try:
                        mx_i = int(mx)
                        cur_i = int(cur) if cur is not None else None
                    except Exception:
                        continue
                    if mx_i <= 0 or mx_i > max_value:
                        continue
                    if cur_i is not None and (cur_i < 0 or cur_i > mx_i):
                        continue
                    try:
                        score = int(mx)
                    except Exception:
                        score = 0
                    if score > best_score:
                        best_score = score
                        best = (cur, mx, reason)
                if best is not None:
                    return best
                return None, None, "no_max_candidates"

            def _pick_unique_single_number(texts: list[str], label: str) -> tuple[Optional[int], str]:
                """Pick a single unambiguous number from OCR outputs.

                This is intentionally conservative: it only returns a value when
                there's exactly ONE unique numeric candidate across all OCR strings.
                """

                try:
                    max_value = int(float(os.getenv("HPMP_MAX_OCR", "100000").strip() or "100000"))
                except Exception:
                    max_value = 100000
                max_value = int(max(1000, max_value))

                candidates: set[int] = set()
                for t in texts or []:
                    try:
                        raw = str(t or "")
                        cleaned = re.sub(r"[^0-9/|\s]", "", raw).replace("|", "/")
                        nums = re.findall(r"\d{1,6}", cleaned)
                        for s in nums:
                            try:
                                v = int(s)
                            except Exception:
                                continue
                            if 0 <= v <= max_value:
                                candidates.add(int(v))
                    except Exception:
                        continue

                if len(candidates) == 1:
                    return list(candidates)[0], "single_number"
                if len(candidates) > 1:
                    return None, "ambiguous_single_number"
                return None, "no_number"

            # Mostrar información de debug de la imagen
            if self._debug:
                print(f"Imagen de entrada: {frame.shape}, tipo: {frame.dtype}")
                print(f"Valor promedio de píxeles: {frame.mean():.2f}")

            # Verificar si la imagen está mayoritariamente negra (posible ventana minimizada)
            if frame.mean() < 5.0:
                print("⚠️  ADVERTENCIA: La imagen capturada está mayoritariamente negra")
                print("   Esto puede indicar que la ventana de Tibia está minimizada o no visible")
                print("   Asegúrate de que Tibia esté maximizado y en primer plano")
                self._set_last_ocr_meta(kind="hp", source="frame", reason="black_frame")
                self._set_last_ocr_meta(kind="mp", source="frame", reason="black_frame")
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
                        hp_text = self.extract_text(hp_crop_rf, allowlist="0123456789/|")
                        if hp_text:
                            hp_current, hp_max, hp_reason = self._parse_current_and_max_with_reason(hp_text, "HP")
                            if hp_current is not None:
                                self._set_last_ocr_meta(kind="hp", source="rf_box", reason="ok")
                            else:
                                self._set_last_ocr_meta(kind="hp", source="rf_box", reason=hp_reason)
                        else:
                            self._set_last_ocr_meta(kind="hp", source="rf_box", reason="ocr_empty")

                if mp_box is not None and mp_current is None:
                    mp_crop_rf = self._crop_from_rf_box(frame, mp_box)
                    if mp_crop_rf is not None:
                        mp_text = self.extract_text(mp_crop_rf, allowlist="0123456789/|")
                        if mp_text:
                            mp_current, mp_max, mp_reason = self._parse_current_and_max_with_reason(mp_text, "MP")
                            if mp_current is not None:
                                self._set_last_ocr_meta(kind="mp", source="rf_box", reason="ok")
                            else:
                                self._set_last_ocr_meta(kind="mp", source="rf_box", reason=mp_reason)
                        else:
                            self._set_last_ocr_meta(kind="mp", source="rf_box", reason="ocr_empty")

            # Fallback: OCR fijo por ROIs
            # (0) Prioridad: top strip (HP/MP juntos). Primero intentamos split
            # 0..60% (HP) / 60..100% (MP) para evitar confusiones con stamina/soul.
            if 'hpmp_top_strip' in rois and use_top_strip:
                strip_roi = normalize_to_px(rois['hpmp_top_strip'])
                ok, reason = _valid_roi(strip_roi)
                if not ok:
                    self._set_last_ocr_meta(kind="hp", source="top_strip", reason=f"invalid_roi:{reason}")
                    self._set_last_ocr_meta(kind="mp", source="top_strip", reason=f"invalid_roi:{reason}")
                else:
                    strip_crop = frame[strip_roi[1]:strip_roi[1]+strip_roi[3], strip_roi[0]:strip_roi[0]+strip_roi[2]]
                    if strip_crop.size > 0:
                        try:
                            # Split-first strategy (most robust).
                            try:
                                w0 = int(strip_crop.shape[1])
                                sx = int(max(1, round(float(w0) * 0.60)))
                                left_crop = strip_crop[:, :sx]
                                right_crop = strip_crop[:, sx:]
                            except Exception:
                                left_crop = strip_crop
                                right_crop = strip_crop

                            if left_crop is not None and getattr(left_crop, "size", 0) != 0:
                                hp_texts = self._readtext_strings(left_crop, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                                hp_current, hp_max, hp_reason = _pick_best_cur_max(hp_texts or [], "HP")
                                if allow_single_number and hp_current is None and hp_max is None:
                                    v, why = _pick_unique_single_number(hp_texts or [], "HP")
                                    if v is not None:
                                        hp_current, hp_max = int(v), None
                                        hp_reason = why
                                if hp_current is not None:
                                    self._set_last_ocr_meta(kind="hp", source="top_strip_split", reason=("ok" if hp_max is not None else (hp_reason or "single_number")))
                                else:
                                    self._set_last_ocr_meta(kind="hp", source="top_strip_split", reason=(hp_reason or "parse_fail"))

                            if right_crop is not None and getattr(right_crop, "size", 0) != 0:
                                mp_texts = self._readtext_strings(right_crop, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                                mp_current, mp_max, mp_reason = _pick_best_cur_max(mp_texts or [], "MP")
                                if allow_single_number and mp_current is None and mp_max is None:
                                    v, why = _pick_unique_single_number(mp_texts or [], "MP")
                                    if v is not None:
                                        mp_current, mp_max = int(v), None
                                        mp_reason = why
                                if mp_current is not None:
                                    self._set_last_ocr_meta(kind="mp", source="top_strip_split", reason=("ok" if mp_max is not None else (mp_reason or "single_number")))
                                else:
                                    self._set_last_ocr_meta(kind="mp", source="top_strip_split", reason=(mp_reason or "parse_fail"))

                            # If split already worked for both, skip the heavier bbox-based parsing.
                            if (hp_current is not None or hp_max is not None) and (mp_current is not None or mp_max is not None):
                                raise StopIteration()

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
                            results = self._readtext_detail1(processed, allowlist="0123456789/|")

                            parsed = []
                            for item in results or []:
                                try:
                                    bbox, text, conf = item
                                except Exception:
                                    continue
                                cleaned = re.sub(r'[^0-9/|]', '', str(text))
                                cleaned = cleaned.replace("|", "/")
                                if not cleaned:
                                    continue

                                cur, mx, _reason = self._parse_current_and_max_with_reason(cleaned, "STRIP")
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

                            # If we have OCR candidates, try to pick HP/MP.
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

                                # Prefer candidates that include max (cur/max).
                                with_max = [t for t in parsed if t[2] is not None]
                                if not with_max:
                                    # Conservative fallback: pick a single-number per side ONLY when unambiguous.
                                    left = [t for t in parsed if float(t[0]) <= (strip_w * 0.5)]
                                    right = [t for t in parsed if float(t[0]) > (strip_w * 0.5)]

                                    if hp_current is None:
                                        try:
                                            vals = sorted({int(t[1]) for t in left if t[1] is not None})
                                            if len(vals) == 1:
                                                hp_current = int(vals[0])
                                                self._set_last_ocr_meta(kind="hp", source="top_strip", reason="single_number")
                                            else:
                                                self._set_last_ocr_meta(kind="hp", source="top_strip", reason="no_max_candidates")
                                        except Exception:
                                            self._set_last_ocr_meta(kind="hp", source="top_strip", reason="no_max_candidates")

                                    if mp_current is None:
                                        try:
                                            vals = sorted({int(t[1]) for t in right if t[1] is not None})
                                            if len(vals) == 1:
                                                mp_current = int(vals[0])
                                                self._set_last_ocr_meta(kind="mp", source="top_strip", reason="single_number")
                                            else:
                                                self._set_last_ocr_meta(kind="mp", source="top_strip", reason="no_max_candidates")
                                        except Exception:
                                            self._set_last_ocr_meta(kind="mp", source="top_strip", reason="no_max_candidates")

                                    # No max to return here; continue with other ROI fallbacks.
                                    with_max = []

                                if with_max:
                                    # Pick HP
                                    if hp_current is None:
                                        choice = _pick_best(with_max, float(hp_ref))
                                        if choice is not None:
                                            _x, cur, mx, _conf, _txt = choice
                                            hp_current, hp_max = cur, mx
                                            self._set_last_ocr_meta(kind="hp", source="top_strip", reason="ok")

                                    # Pick MP (avoid reusing the same exact candidate when possible)
                                    if mp_current is None:
                                        pool = list(with_max)
                                        try:
                                            if hp_current is not None:
                                                pool = [t for t in pool if t[0] != (choice[0] if choice is not None else None)]
                                        except Exception:
                                            pool = list(with_max)
                                        choice2 = _pick_best(pool or list(with_max), float(mp_ref))
                                        if choice2 is not None:
                                            _x, cur, mx, _conf, _txt = choice2
                                            mp_current, mp_max = cur, mx
                                            self._set_last_ocr_meta(kind="mp", source="top_strip", reason="ok")
                            else:
                                if hp_current is None:
                                    self._set_last_ocr_meta(kind="hp", source="top_strip", reason="no_candidates")
                                if mp_current is None:
                                    self._set_last_ocr_meta(kind="mp", source="top_strip", reason="no_candidates")
                        except StopIteration:
                            # Split succeeded; no need to fall back.
                            pass
                        except Exception as e:
                            print(f"Error OCR strip superior: {e}")

            if 'hp_top_ocr' in rois and hp_current is None:
                hp_roi = normalize_to_px(rois['hp_top_ocr'])
                ok, reason = _valid_roi(hp_roi)
                if not ok:
                    self._set_last_ocr_meta(kind="hp", source="top_ocr", reason=f"invalid_roi:{reason}")
                else:
                    hp_crop = frame[hp_roi[1]:hp_roi[1]+hp_roi[3], hp_roi[0]:hp_roi[0]+hp_roi[2]]
                    if self._debug:
                        print(f"HP ROI: {hp_roi}, crop shape: {hp_crop.shape if hp_crop.size > 0 else 'empty'}")
                    if hp_crop.size > 0:
                        if self._debug:
                            print(f"HP crop - valor promedio: {hp_crop.mean():.2f}")
                        hp_texts = self._readtext_strings(hp_crop, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                        hp_text = hp_texts[0] if hp_texts else ""
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
                                hp_texts = self._readtext_strings(hp_crop2, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                                hp_text = hp_texts[0] if hp_texts else hp_text
                        if self._debug:
                            print(f"HP texto crudo: '{hp_text}'")
                        if hp_text:
                            hp_current, hp_max, hp_reason = _pick_best_cur_max(hp_texts or [hp_text], "HP")
                            if hp_current is None and hp_max is None:
                                if allow_single_number:
                                    v, why = _pick_unique_single_number(hp_texts or [hp_text], "HP")
                                    if v is not None:
                                        hp_current, hp_max = int(v), None
                                        hp_reason = why

                            # If HP ROI is very wide (common in some configs),
                            # it may include unrelated numbers. Try a few
                            # smaller windows inside the ROI before giving up.
                            if hp_current is None and hp_max is None:
                                try:
                                    if int(hp_roi[2]) >= 200 and hp_crop is not None and getattr(hp_crop, "size", 0) != 0:
                                        w0 = int(hp_crop.shape[1])
                                        h0 = int(hp_crop.shape[0])
                                        # Candidate windows: overlapping slices in the central-left region.
                                        wins = [
                                            (int(w0 * 0.05), int(w0 * 0.35)),
                                            (int(w0 * 0.12), int(w0 * 0.45)),
                                            (int(w0 * 0.20), int(w0 * 0.60)),
                                        ]
                                        for x0, x1 in wins:
                                            x0 = max(0, min(int(x0), w0 - 1))
                                            x1 = max(x0 + 1, min(int(x1), w0))
                                            sub = hp_crop[:, x0:x1]
                                            if sub is None or getattr(sub, "size", 0) == 0:
                                                continue
                                            sub_texts = self._readtext_strings(sub, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                                            cur2, mx2, r2 = _pick_best_cur_max(sub_texts or [], "HP")
                                            if cur2 is not None:
                                                hp_current, hp_max, hp_reason = cur2, mx2, r2
                                                self._set_last_ocr_meta(kind="hp", source="top_ocr_subwin", reason=("ok" if hp_max is not None else (hp_reason or "single_number")))
                                                break
                                except Exception:
                                    pass
                            if hp_current is not None:
                                # If max is missing, expose that in reason.
                                self._set_last_ocr_meta(kind="hp", source="top_ocr", reason=("ok" if hp_max is not None else (hp_reason or "single_number")))
                            else:
                                self._set_last_ocr_meta(kind="hp", source="top_ocr", reason=hp_reason)
                        else:
                            self._set_last_ocr_meta(kind="hp", source="top_ocr", reason="ocr_empty")

            # Extraer MP del OCR superior
            if 'mp_top_ocr' in rois and mp_current is None:
                mp_roi = normalize_to_px(rois['mp_top_ocr'])
                ok, reason = _valid_roi(mp_roi)
                if not ok:
                    self._set_last_ocr_meta(kind="mp", source="top_ocr", reason=f"invalid_roi:{reason}")
                else:
                    mp_crop = frame[mp_roi[1]:mp_roi[1]+mp_roi[3], mp_roi[0]:mp_roi[0]+mp_roi[2]]
                    if self._debug:
                        print(f"MP ROI: {mp_roi}, crop shape: {mp_crop.shape if mp_crop.size > 0 else 'empty'}")
                    if mp_crop.size > 0:
                        if self._debug:
                            print(f"MP crop - valor promedio: {mp_crop.mean():.2f}")
                        mp_texts = self._readtext_strings(mp_crop, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                        mp_text = mp_texts[0] if mp_texts else ""
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
                                mp_texts = self._readtext_strings(mp_crop2, allowlist="0123456789/|", deadline_ts=hpmp_deadline_ts)
                                mp_text = mp_texts[0] if mp_texts else mp_text
                        if self._debug:
                            print(f"MP texto crudo: '{mp_text}'")
                        if mp_text:
                            mp_current, mp_max, mp_reason = _pick_best_cur_max(mp_texts or [mp_text], "MP")
                            if mp_current is None and mp_max is None:
                                if allow_single_number:
                                    v, why = _pick_unique_single_number(mp_texts or [mp_text], "MP")
                                    if v is not None:
                                        mp_current, mp_max = int(v), None
                                        mp_reason = why
                            if mp_current is not None:
                                self._set_last_ocr_meta(kind="mp", source="top_ocr", reason=("ok" if mp_max is not None else (mp_reason or "single_number")))
                            else:
                                self._set_last_ocr_meta(kind="mp", source="top_ocr", reason=mp_reason)
                        else:
                            self._set_last_ocr_meta(kind="mp", source="top_ocr", reason="ocr_empty")

        except Exception as e:
            print(f"Error extrayendo HP/MP: {e}")

        # Optional diagnostics: dump evidence when we couldn't recover a stable HP/MP.
        try:
            incomplete = bool(
                (hp_current is None) or (mp_current is None) or (hp_current is not None and hp_max is None) or (mp_current is not None and mp_max is None)
            )
        except Exception:
            incomplete = False

        if incomplete:
            try:
                crops: dict[str, np.ndarray] = {}
                try:
                    if 'hpmp_top_strip' in rois:
                        x, y, w, h = normalize_to_px(rois['hpmp_top_strip'])
                        crop = frame[y:y+h, x:x+w]
                        if crop is not None and getattr(crop, 'size', 0) != 0:
                            crops['hpmp_top_strip'] = crop
                except Exception:
                    pass
                try:
                    if 'hp_top_ocr' in rois:
                        x, y, w, h = normalize_to_px(rois['hp_top_ocr'])
                        crop = frame[y:y+h, x:x+w]
                        if crop is not None and getattr(crop, 'size', 0) != 0:
                            crops['hp_top_ocr'] = crop
                except Exception:
                    pass
                try:
                    if 'mp_top_ocr' in rois:
                        x, y, w, h = normalize_to_px(rois['mp_top_ocr'])
                        crop = frame[y:y+h, x:x+w]
                        if crop is not None and getattr(crop, 'size', 0) != 0:
                            crops['mp_top_ocr'] = crop
                except Exception:
                    pass

                meta = {
                    'ts': float(time.time()),
                    'hp': {'cur': hp_current, 'max': hp_max, 'source': self.last_hp_ocr_source, 'reason': self.last_hp_ocr_reason},
                    'mp': {'cur': mp_current, 'max': mp_max, 'source': self.last_mp_ocr_source, 'reason': self.last_mp_ocr_reason},
                }
                self._maybe_dump_hpmp_failure(crops=crops, meta=meta)
            except Exception:
                pass

        return hp_current, hp_max, mp_current, mp_max

    def _parse_current_and_max_with_reason(self, text: str, label: str) -> Tuple[Optional[int], Optional[int], str]:
        """Parsea current/max y devuelve razón del resultado.

        Nota: tolera separadores OCR ambiguos ("|" vs "/") y valida cur<=max.
        """
        cur, mx, reason = parse_current_and_max_with_reason(str(text or ""), label=label)
        if self._debug and reason == "single_number" and cur is not None and mx is None:
            print(f"{label}: Usando valor único encontrado: {cur}")
        if self._debug and reason in {"parse_fail", "invalid_range"}:
            # Avoid log spam for OCR artifacts like a lone '|' with no digits.
            try:
                if not re.search(r"\d", str(text or "")):
                    return cur, mx, reason
            except Exception:
                pass
            print(f"{label}: No se pudo parsear valor de: '{text}' (reason={reason})")
        return cur, mx, reason

    def _parse_current_and_max(self, text: str, label: str) -> Tuple[Optional[int], Optional[int]]:
        """Parsea current/max si existe; si no, devuelve (current, None)."""
        cur, mx, _reason = self._parse_current_and_max_with_reason(text, label)
        return cur, mx


# Shared OCR instance across the process.
#
# This prevents multiple EasyOCR/Torch GPU initializations (slow + noisy) and
# keeps all OCR users (GameStateBuilder, battlelist parsing/targeting) consistent.
_OCR_SHARED: OCRProcessor | None = None


def get_shared_ocr_processor() -> OCRProcessor:
    global _OCR_SHARED
    if _OCR_SHARED is not None:
        return _OCR_SHARED
    _OCR_SHARED = OCRProcessor()
    return _OCR_SHARED