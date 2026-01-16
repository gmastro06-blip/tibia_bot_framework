from typing import Optional, Dict, Any, Tuple, List
import os
import time
import json
from pathlib import Path
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor
from vision.roboflow_inference import RoboflowInference
from vision.bar_analysis import estimate_bar_fill_ratio, estimate_bar_fill_ratio_with_reason
from vision.obstacles import compute_viewport_tile_offsets
from vision.presence import is_hungry_hsv, is_nonempty_icon, is_nonempty_equipment_slot, detect_status_icons
from vision.minimap_motion import MinimapMotionTracker
from vision import battlelist
from vision.roi import roi_to_px_result

@dataclass
class GameState:
    """Estado actual del juego"""
    hp_current: Optional[int] = None
    hp_max: Optional[int] = None
    mp_current: Optional[int] = None
    mp_max: Optional[int] = None
    cap_current: Optional[int] = None
    pos_x: Optional[int] = None
    pos_y: Optional[int] = None
    pos_z: Optional[int] = None
    coords_provider: Optional[str] = None
    # Minimap-motion debug (EXPERIMENTAL)
    minimap_mode_used: Optional[str] = None
    minimap_response: Optional[float] = None
    minimap_delta_dx: Optional[float] = None
    minimap_delta_dy: Optional[float] = None
    minimap_acc_dx: Optional[float] = None
    minimap_acc_dy: Optional[float] = None
    minimap_marker_dpx_dx: Optional[float] = None
    minimap_marker_dpx_dy: Optional[float] = None
    coords_confidence: Optional[float] = None
    coords_provider_status: Optional[str] = None
    # Structured provider status (for UI/telemetry)
    # Example: {"enabled": True, "seed_ok": True, "confidence": 0.82, "last_update_ts": 123.4, "reason": "ok"}
    coords_provider_state: Optional[Dict[str, Any]] = None
    coords_confidence_level: Optional[str] = None
    ring_equipped: Optional[bool] = None
    amulet_equipped: Optional[bool] = None
    paralyzed: Optional[bool] = None
    haste_active: Optional[bool] = None
    utamo_active: Optional[bool] = None
    hungry: Optional[bool] = None
    # Señales derivadas (útiles para thresholds/decisiones, aunque no haya OCR perfecto)
    hp_pct: Optional[float] = None
    mp_pct: Optional[float] = None
    roboflow_boxes: Optional[List[Dict[str, Any]]] = None
    viewport_tile_offsets: Optional[List[Tuple[int, int]]] = None

    # HP/MP extraction observability
    hp_method: Optional[str] = None
    hp_reason: Optional[str] = None
    mp_method: Optional[str] = None
    mp_reason: Optional[str] = None

    # CAP extraction observability
    cap_method: Optional[str] = None
    cap_reason: Optional[str] = None

    # EntityList/Battlelist observability
    battlelist_source: Optional[str] = None
    battlelist_reason: Optional[str] = None

    # Per-tick debug payload (ROI px, parse_ok, reasons)
    hud_debug: Optional[Dict[str, Any]] = None
    frame_mean: Optional[float] = None

    # Battlelist (assistant-only observability)
    battlelist_entries: Optional[List[Dict[str, Any]]] = None
    battlelist_n_rows: Optional[int] = None
    battlelist_n_valid: Optional[int] = None
    battlelist_top_names: Optional[List[str]] = None
    battlelist_confidence: Optional[float] = None

    # Battlelist targeting (vision-based)
    battlelist_target_state: Optional[str] = None
    battlelist_target_row: Optional[int] = None
    battlelist_alive_prob: Optional[float] = None
    battlelist_selected_prob: Optional[float] = None
    battlelist_target_reason: Optional[str] = None

    def __str__(self) -> str:
        rf_n = len(self.roboflow_boxes) if self.roboflow_boxes else 0
        pos = ""
        try:
            if self.pos_x is not None and self.pos_y is not None:
                if self.pos_z is not None:
                    pos = f", pos: ({self.pos_x},{self.pos_y},{self.pos_z})"
                else:
                    pos = f", pos: ({self.pos_x},{self.pos_y})"
        except Exception:
            pos = ""
        return (
            f"HP: {self.hp_current}/{self.hp_max}, MP: {self.mp_current}/{self.mp_max}, "
            f"Cap: {self.cap_current}{pos}, ring: {self.ring_equipped}, amulet: {self.amulet_equipped}, "
            f"paralyzed: {self.paralyzed}, haste: {self.haste_active}, utamo: {self.utamo_active}, hungry: {self.hungry}, RF: {rf_n}"
        )

class GameStateBuilder:
    def __init__(self):
        self.ocr_processor = OCRProcessor()
        self.frame_history = []  # Para suavizado de valores
        self._rf = RoboflowInference.from_env()
        self._rf_hpmp = RoboflowInference.from_env_hpmp()  # Separate for HP/MP
        self._rf_last_ts = 0.0
        self._rf_min_interval_s = float(os.getenv("ROBOFLOW_MIN_INTERVAL_S", "0.5"))

        # Throttling opcional (por defecto 0.0 = sin throttle)
        self._rf_hpmp_last_ts = 0.0
        self._rf_hpmp_min_interval_s = float(os.getenv("ROBOFLOW_HPMP_MIN_INTERVAL_S", "0.0"))
        self._ocr_last_ts = 0.0
        self._ocr_min_interval_s = float(os.getenv("OCR_MIN_INTERVAL_S", "0.0"))

        # Caché de últimos valores OCR (para cuando OCR está throttled)
        self._last_hp_current: Optional[int] = None
        self._last_hp_max: Optional[int] = None
        self._last_mp_current: Optional[int] = None
        self._last_mp_max: Optional[int] = None
        self._last_cap_current: Optional[int] = None
        self._last_pos_x: Optional[int] = None
        self._last_pos_y: Optional[int] = None
        self._last_pos_z: Optional[int] = None

        # Coords por minimapa (minimap_motion): requiere seed y ROI minimap_content.
        # No hace OCR de coords; infiere movimiento y lo acumula.
        # Puede hacer fallback a steps (sin coords) u OCR según config.
        self._minimap_tracker = MinimapMotionTracker()
        self._minimap_seed: tuple[int, int, int | None] | None = None
        self._minimap_coords: tuple[int, int, int | None] | None = None
        self._minimap_accept_streak: int = 0
        self._minimap_last_resp: float = 0.0
        self._minimap_confidence: float = 0.0
        self._minimap_status: str = ""
        self._minimap_accept_streak: int = 0
        self._minimap_last_resp: float = 0.0
        self._minimap_last_update_ts: float = 0.0
        self._minimap_low_conf_streak: int = 0
        self._minimap_force_disable_coords: bool = False

        # Battlelist stabilization buffer: row_index -> last N parsed entries
        self._battlelist_buf: Dict[int, List[Dict[str, Any]]] = {}

        # Status icons (paralyze/haste/utamo): debounce across K frames.
        try:
            self._status_persist_k = int(float(os.getenv("STATUS_ICON_PERSIST_K", "3").strip() or "3"))
        except Exception:
            self._status_persist_k = 3
        self._status_persist_k = max(1, min(20, int(self._status_persist_k)))
        try:
            self._status_match_thr = float(os.getenv("STATUS_ICON_MATCH_THRESHOLD", "0.65").strip() or "0.65")
        except Exception:
            self._status_match_thr = 0.65
        self._status_match_thr = float(max(0.0, min(1.0, self._status_match_thr)))

        self._status_state: Dict[str, bool] = {"paralyzed": False, "haste_active": False, "utamo_active": False}
        self._status_on_streak: Dict[str, int] = {"paralyzed": 0, "haste_active": 0, "utamo_active": 0}
        self._status_off_streak: Dict[str, int] = {"paralyzed": 0, "haste_active": 0, "utamo_active": 0}

    def reseed_minimap(self) -> None:
        """Resetea el tracker de minimapa y obliga a pedir seed de nuevo."""

        try:
            self._minimap_tracker.reset()
        except Exception:
            pass
        self._minimap_seed = None
        self._minimap_coords = None
        self._minimap_accept_streak = 0
        self._minimap_last_resp = 0.0
        self._minimap_confidence = 0.0
        self._minimap_status = "reseed"
        self._minimap_last_update_ts = 0.0
        self._minimap_low_conf_streak = 0
        self._minimap_force_disable_coords = False

        # Battlelist is independent of minimap, but reseed is a convenient place
        # to also clear other experimental trackers if desired.


    @staticmethod
    def _coords_provider_kind() -> str:
        return (os.getenv("COORDS_PROVIDER", "ocr") or "ocr").strip().lower()

    @staticmethod
    def _is_coords_disabled(kind: str) -> bool:
        return kind in {"disabled", "none", "off", "0", "false", "no"}

    @staticmethod
    def _coords_from_env() -> tuple[int, int, int | None] | None:
        px = (os.getenv("PLAYER_X", "") or "").strip()
        py = (os.getenv("PLAYER_Y", "") or "").strip()
        pz = (os.getenv("PLAYER_Z", "") or "").strip()
        if not (px and py):
            return None
        try:
            x = int(px)
            y = int(py)
        except Exception:
            return None
        z: int | None = None
        if pz:
            try:
                z = int(pz)
            except Exception:
                z = None
        return (x, y, z)

    @staticmethod
    def _coords_seed_from_env() -> tuple[int, int, int | None] | None:
        """Seed de coords para COORDS_PROVIDER=minimap (EXPERIMENTAL).

        Usa COORDS_SEED_X / COORDS_SEED_Y / opcional COORDS_SEED_Z.
        Fallback a PLAYER_X/PLAYER_Y/PLAYER_Z por conveniencia.
        """

        sx = (os.getenv("COORDS_SEED_X", "") or "").strip()
        sy = (os.getenv("COORDS_SEED_Y", "") or "").strip()
        sz = (os.getenv("COORDS_SEED_Z", "") or "").strip()
        if sx and sy:
            try:
                x = int(sx)
                y = int(sy)
            except Exception:
                return None
            z: int | None = None
            if sz:
                try:
                    z = int(sz)
                except Exception:
                    z = None
            return (x, y, z)

        # Fallback
        return GameStateBuilder._coords_from_env()

    @staticmethod
    def _coords_from_file_path(path_raw: str) -> tuple[int, int, int | None] | None:
        raw = (path_raw or "").strip()
        if not raw:
            return None
        try:
            p = Path(raw)
            if not p.is_absolute():
                repo_root = Path(__file__).resolve().parent.parent.parent
                p = (repo_root / p).resolve()
            if not p.exists() or not p.is_file():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

        try:
            if isinstance(data, dict):
                if "coords" in data:
                    data = data.get("coords")
                if isinstance(data, dict) and ("x" in data and "y" in data):
                    x_raw = data.get("x")
                    y_raw = data.get("y")
                    if x_raw is None or y_raw is None:
                        return None
                    x = int(x_raw)
                    y = int(y_raw)
                    z_raw = data.get("z", None)
                    z = int(z_raw) if z_raw is not None else None
                    return (x, y, z)
            if isinstance(data, (list, tuple)) and len(data) >= 2:
                x = int(data[0])
                y = int(data[1])
                z = int(data[2]) if len(data) >= 3 and data[2] is not None else None
                return (x, y, z)
        except Exception:
            return None
        return None

    def _coords_from_minimap(
        self,
        frame: np.ndarray,
        rois: Dict[str, Dict[str, float]],
        resolution: Tuple[int, int],
        *,
        allow_ocr: bool = True,
    ) -> tuple[int, int, int | None] | None:
        """Infiera coords absolutas trackeando la traslación del minimapa (minimap_motion).

        Requisitos:
        - Debe existir la ROI `minimap_content`.
        - Debe haber seed vía COORDS_SEED_X/Y[/Z] o COORDS_SEED_FILE.

        Fallback:
        - Si confidence < MINIMAP_FALLBACK_CONF_THRESHOLD por MINIMAP_FALLBACK_N_TICKS,
          activa fallback según MINIMAP_FALLBACK_MODE: "steps" (sin coords) o "ocr".
        """

        # Reset per-tick flag.
        self._minimap_force_disable_coords = False

        if not (isinstance(rois, dict) and rois.get("minimap_content") is not None):
            self._minimap_status = "minimap_no_roi"
            return None

        # Seed una vez (o si todavía no existe).
        if self._minimap_seed is None:
            seed_file = (os.getenv("COORDS_SEED_FILE", "") or "").strip()
            seed = None
            if seed_file:
                seed = self._coords_from_file_path(seed_file)
            if seed is None:
                seed = self._coords_seed_from_env()
            if seed is None:
                # Último recurso: permitir COORDS_FILE como seed (one-shot).
                seed = self._coords_from_file()

            if seed is None:
                self._minimap_status = "minimap_no_seed"
                return None

            self._minimap_seed = seed
            self._minimap_coords = seed
            try:
                self._minimap_last_update_ts = float(time.time())
            except Exception:
                pass

        # Cortar minimapa y actualizar motion tracker.
        try:
            mx, my, mw, mh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["minimap_content"])
            crop = frame[my : my + mh, mx : mx + mw]
            if crop is None or crop.size == 0:
                self._minimap_status = "minimap_no_crop"
                return self._minimap_coords
        except Exception:
            self._minimap_status = "minimap_no_crop"
            return self._minimap_coords

        step = None
        try:
            step = self._minimap_tracker.update(crop)
        except Exception:
            step = None

        if step is None:
            # Treat this as a low-confidence tick so fallback policy can engage.
            try:
                self._minimap_last_resp = 0.0
            except Exception:
                pass
            try:
                self._minimap_accept_streak = 0
            except Exception:
                pass
            try:
                self._minimap_confidence = 0.0
            except Exception:
                pass

            self._minimap_status = "minimap_no_step"
            return self._minimap_apply_fallback(frame, rois, resolution, allow_ocr=allow_ocr, coords=self._minimap_coords)

        dx_tiles, dy_tiles, _resp = step
        if self._minimap_coords is None:
            self._minimap_coords = self._minimap_seed

        if self._minimap_coords is None:
            self._minimap_status = "minimap_no_seed"
            return None

        # Confidence gating to reduce jitter (minimap is noisy vs OCR coords).
        resp = _resp
        self._minimap_last_resp = float(resp) if resp is not None else 0.0
        accept_resp = float(os.getenv("MINIMAP_ACCEPT_MIN_RESPONSE", "0.20") or 0.20)
        min_streak = int(float(os.getenv("MINIMAP_ACCEPT_MIN_STREAK", "2") or 2))
        max_step_tiles = int(float(os.getenv("MINIMAP_ACCEPT_MAX_STEP_TILES", "4") or 4))
        min_streak = max(1, min_streak)
        max_step_tiles = max(1, max_step_tiles)

        if self._minimap_last_resp < accept_resp:
            self._minimap_accept_streak = 0
            self._minimap_confidence = min(1.0, self._minimap_last_resp / accept_resp) if accept_resp > 0 else 0.0
            self._minimap_status = "minimap_low_resp"
            return self._minimap_apply_fallback(frame, rois, resolution, allow_ocr=allow_ocr, coords=self._minimap_coords)

        self._minimap_accept_streak += 1
        self._minimap_confidence = min(1.0, float(self._minimap_accept_streak) / float(min_streak))

        if self._minimap_accept_streak < min_streak:
            self._minimap_status = "minimap_warming"
            return self._minimap_apply_fallback(frame, rois, resolution, allow_ocr=allow_ocr, coords=self._minimap_coords)

        if abs(int(dx_tiles)) > max_step_tiles or abs(int(dy_tiles)) > max_step_tiles:
            # Untrusted jump, reset streak and ignore.
            self._minimap_accept_streak = 0
            self._minimap_status = "minimap_reject_step"
            return self._minimap_apply_fallback(frame, rois, resolution, allow_ocr=allow_ocr, coords=self._minimap_coords)

        x0, y0, z0 = self._minimap_coords
        x1 = int(x0) + int(dx_tiles)
        y1 = int(y0) + int(dy_tiles)
        self._minimap_coords = (x1, y1, z0)
        self._minimap_status = "ok"
        self._minimap_confidence = 1.0
        try:
            self._minimap_last_update_ts = float(time.time())
        except Exception:
            pass
        return self._minimap_apply_fallback(frame, rois, resolution, allow_ocr=allow_ocr, coords=self._minimap_coords)

    def _minimap_apply_fallback(
        self,
        frame: np.ndarray,
        rois: Dict[str, Dict[str, float]],
        resolution: Tuple[int, int],
        *,
        allow_ocr: bool,
        coords: tuple[int, int, int | None] | None,
    ) -> tuple[int, int, int | None] | None:
        """Apply low-confidence fallback policy.

        Returns the coordinates to publish for this tick.
        May set `self._minimap_force_disable_coords` to request steps-mode fallback.
        """

        # Configurable thresholds
        try:
            thr = float(os.getenv("MINIMAP_FALLBACK_CONF_THRESHOLD", "0.40").strip() or "0.40")
        except Exception:
            thr = 0.40
        try:
            n_ticks = int(float(os.getenv("MINIMAP_FALLBACK_N_TICKS", "4").strip() or "4"))
        except Exception:
            n_ticks = 4
        n_ticks = max(1, int(n_ticks))
        mode = (os.getenv("MINIMAP_FALLBACK_MODE", "steps") or "steps").strip().lower()
        if mode not in {"steps", "ocr"}:
            mode = "steps"

        conf = None
        try:
            conf = float(self._minimap_confidence)
        except Exception:
            conf = None

        # Track consecutive low-confidence ticks
        try:
            if conf is not None and conf < float(thr):
                self._minimap_low_conf_streak = int(self._minimap_low_conf_streak) + 1
            else:
                self._minimap_low_conf_streak = 0
        except Exception:
            self._minimap_low_conf_streak = 0

        if int(self._minimap_low_conf_streak) < int(n_ticks):
            return coords

        # Fallback active
        if mode == "steps":
            self._minimap_status = "fallback_steps"
            self._minimap_force_disable_coords = True
            return None

        # mode == "ocr"
        self._minimap_status = "fallback_ocr"
        if allow_ocr:
            try:
                return self.ocr_processor.extract_coords(frame, rois, resolution)
            except Exception:
                return coords
        # If OCR is throttled, keep last minimap coords but surface status.
        return coords

    @staticmethod
    def _coords_from_file() -> tuple[int, int, int | None] | None:
        raw = (os.getenv("COORDS_FILE", "") or "").strip()
        if not raw:
            return None
        try:
            p = Path(raw)
            if not p.is_absolute():
                # Resolve relative to repo root (best-effort)
                repo_root = Path(__file__).resolve().parent.parent.parent
                p = (repo_root / p).resolve()
            if not p.exists() or not p.is_file():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

        try:
            if isinstance(data, dict):
                if "coords" in data:
                    data = data.get("coords")
                elif "x" in data and "y" in data:
                    x_raw = data.get("x")
                    y_raw = data.get("y")
                    if x_raw is None or y_raw is None:
                        return None
                    x = int(x_raw)
                    y = int(y_raw)
                    z_raw = data.get("z", None)
                    z = int(z_raw) if z_raw is not None else None
                    return (x, y, z)
            if isinstance(data, (list, tuple)) and len(data) >= 2:
                x = int(data[0])
                y = int(data[1])
                z = int(data[2]) if len(data) >= 3 and data[2] is not None else None
                return (x, y, z)
        except Exception:
            return None
        return None

    def _coords_from_provider(
        self,
        frame: np.ndarray,
        rois: Dict[str, Dict[str, float]],
        resolution: Tuple[int, int],
        *,
        allow_ocr: bool,
    ) -> tuple[tuple[int, int, int | None] | None, bool]:
        kind = self._coords_provider_kind()
        if self._is_coords_disabled(kind):
            return None, True

        if kind in {"env", "player_env"}:
            return self._coords_from_env(), False

        if kind in {"file", "json", "external"}:
            return self._coords_from_file(), False

        if kind in {"minimap", "map", "minimap_motion"}:
            try:
                coords = self._coords_from_minimap(frame, rois, resolution, allow_ocr=bool(allow_ocr))
                # When minimap fallback requests steps-mode, force clear coords.
                if bool(getattr(self, "_minimap_force_disable_coords", False)):
                    return None, True
                return coords, False
            except Exception:
                return None, False

        # Default: OCR
        if not allow_ocr:
            return None, False
        try:
            return self.ocr_processor.extract_coords(frame, rois, resolution), False
        except Exception:
            return None, False

    def update_from_frame(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> GameState:
        """Actualiza el estado del juego desde un frame"""
        frame_mean: Optional[float] = None
        try:
            # Cheap-ish signal to quickly spot dummy/black frames in telemetry.
            frame_mean = float(np.mean(frame))
        except Exception:
            frame_mean = None

        rf_boxes: Optional[List[Dict[str, Any]]] = None
        rf_hpmp_boxes: Optional[List[Dict[str, Any]]] = None
        viewport_offsets: Optional[List[Tuple[int, int]]] = None
        now = time.time()
        if self._rf is not None:
            if now - self._rf_last_ts >= self._rf_min_interval_s:
                try:
                    pred = self._rf.predict(frame)
                    rf_boxes = self._rf.extract_boxes(pred)
                except Exception:
                    rf_boxes = None
                self._rf_last_ts = now

        # Separate inference for HP/MP
        if self._rf_hpmp is not None:
            if now - self._rf_hpmp_last_ts >= self._rf_hpmp_min_interval_s:
                try:
                    pred_hpmp = self._rf_hpmp.predict(frame)
                    rf_hpmp_boxes = self._rf_hpmp.extract_boxes(pred_hpmp)
                except Exception:
                    rf_hpmp_boxes = None
                self._rf_hpmp_last_ts = now

        # (A) Extraer HP/MP usando OCR (throttled opcionalmente)
        hp_current: Optional[int] = None
        hp_max: Optional[int] = None
        mp_current: Optional[int] = None
        mp_max: Optional[int] = None
        cap_current: Optional[int] = None
        pos_x: Optional[int] = None
        pos_y: Optional[int] = None
        pos_z: Optional[int] = None

        do_ocr = (now - self._ocr_last_ts) >= self._ocr_min_interval_s
        # Observability of OCR path
        hp_method = ""
        hp_reason = ""
        mp_method = ""
        mp_reason = ""
        cap_method = ""
        cap_reason = ""

        hud_debug: Dict[str, Any] = {
            "ts": float(now),
            "resolution": [int(resolution[0]), int(resolution[1])],
            "frame_mean": frame_mean,
            "hp": {},
            "mp": {},
            "cap": {},
            "battlelist": {},
        }

        if do_ocr:
            try:
                hp_current, hp_max, mp_current, mp_max = self.ocr_processor.extract_hp_mp_full(
                    frame,
                    rois,
                    resolution,
                    rf_boxes=rf_boxes,
                )
            except Exception:
                hp_current, hp_max, mp_current, mp_max = None, None, None, None

            try:
                hp_method = str(getattr(self.ocr_processor, "last_hp_ocr_source", "") or "")
                hp_reason = str(getattr(self.ocr_processor, "last_hp_ocr_reason", "") or "")
                mp_method = str(getattr(self.ocr_processor, "last_mp_ocr_source", "") or "")
                mp_reason = str(getattr(self.ocr_processor, "last_mp_ocr_reason", "") or "")
            except Exception:
                hp_method = ""
                hp_reason = ""
                mp_method = ""
                mp_reason = ""

            try:
                cap_current = self.ocr_processor.extract_capacity(frame, rois, resolution)
                if cap_current is not None:
                    cap_method = "cap_ocr" if (hasattr(rois, "get") and rois.get("cap_ocr") is not None) else "skills_panel"
                    cap_reason = "ok"
                else:
                    cap_method = "cap_ocr" if (hasattr(rois, "get") and rois.get("cap_ocr") is not None) else "skills_panel"
                    cap_reason = "no_digits"
            except Exception:
                cap_current = None
                cap_method = "cap_ocr" if (hasattr(rois, "get") and rois.get("cap_ocr") is not None) else "skills_panel"
                cap_reason = "exception"

            self._ocr_last_ts = now
            self._last_hp_current = hp_current
            self._last_hp_max = hp_max
            self._last_mp_current = mp_current
            self._last_mp_max = mp_max
            self._last_cap_current = cap_current
        else:
            # Reusar lo último conocido
            hp_current = self._last_hp_current
            hp_max = self._last_hp_max
            mp_current = self._last_mp_current
            mp_max = self._last_mp_max
            cap_current = self._last_cap_current
            pos_x = self._last_pos_x
            pos_y = self._last_pos_y
            pos_z = self._last_pos_z
            hp_method = "cached"
            mp_method = "cached"
            hp_reason = "ocr_throttled"
            mp_reason = "ocr_throttled"
            cap_method = "cached"
            cap_reason = "ocr_throttled"

        # Coords provider: permite deshabilitar OCR coords (o usar fuente externa) cuando no hay coords visibles.
        coords_provider_kind = self._coords_provider_kind()
        try:
            coords, disabled = self._coords_from_provider(frame, rois, resolution, allow_ocr=bool(do_ocr))
            if disabled:
                pos_x, pos_y, pos_z = None, None, None
                self._last_pos_x, self._last_pos_y, self._last_pos_z = None, None, None
            elif coords is not None:
                pos_x, pos_y, pos_z = coords
                self._last_pos_x, self._last_pos_y, self._last_pos_z = pos_x, pos_y, pos_z
            else:
                # Keep last-known (already set above)
                self._last_pos_x, self._last_pos_y, self._last_pos_z = pos_x, pos_y, pos_z
        except Exception:
            self._last_pos_x, self._last_pos_y, self._last_pos_z = pos_x, pos_y, pos_z

        # Defaults de max (útiles si solo usamos barras)
        if hp_max is None:
            try:
                hp_max_env = os.getenv("TIBIA_HP_MAX", "").strip()
                hp_max = int(hp_max_env) if hp_max_env else None
            except Exception:
                hp_max = None
        if mp_max is None:
            try:
                mp_max_env = os.getenv("TIBIA_MP_MAX", "").strip()
                mp_max = int(mp_max_env) if mp_max_env else None
            except Exception:
                mp_max = None

        # (B) Fallback por barras: si current falta y hay max, estimar por fill ratio
        # Intentar usar boxes Roboflow si existen; si no, usar ROIs del config.
        hp_ratio: Optional[float] = None
        mp_ratio: Optional[float] = None
        hp_bar_reason = ""
        mp_bar_reason = ""
        hp_bar_roi = None
        mp_bar_roi = None
        hp_bar_roi_reason = ""
        mp_bar_roi_reason = ""
        if rf_hpmp_boxes and self._rf_hpmp is not None:
            hp_bar_classes = os.getenv("ROBOFLOW_HP_BAR_CLASSES", "hp_bar,hp_low_bar,health_bar").split(",")
            mp_bar_classes = os.getenv("ROBOFLOW_MP_BAR_CLASSES", "mp_bar,mp_low_bar,mana_bar").split(",")
            hp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in hp_bar_classes])
            mp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in mp_bar_classes])
            if hp_box:
                crop = self._rf_hpmp.crop_from_box(frame, hp_box)
                if crop is not None:
                    hp_bar_roi = (0, 0, int(crop.shape[1]), int(crop.shape[0]))
                    hp_ratio, hp_bar_reason = estimate_bar_fill_ratio_with_reason(
                        crop, hp_bar_roi, "hp"
                    )
            if mp_box:
                crop = self._rf_hpmp.crop_from_box(frame, mp_box)
                if crop is not None:
                    mp_bar_roi = (0, 0, int(crop.shape[1]), int(crop.shape[0]))
                    mp_ratio, mp_bar_reason = estimate_bar_fill_ratio_with_reason(
                        crop, mp_bar_roi, "mp"
                    )

        if hp_ratio is None and "hp_low_bar" in rois:
            rr = roi_to_px_result(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=rois["hp_low_bar"],
            )
            hp_bar_roi_reason = str(rr.reason or "")
            if rr.ok and rr.roi is not None:
                hp_bar_roi = rr.roi
                hp_ratio, hp_bar_reason = estimate_bar_fill_ratio_with_reason(frame, rr.roi, "hp")
        if mp_ratio is None and "mp_low_bar" in rois:
            rr = roi_to_px_result(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=rois["mp_low_bar"],
            )
            mp_bar_roi_reason = str(rr.reason or "")
            if rr.ok and rr.roi is not None:
                mp_bar_roi = rr.roi
                mp_ratio, mp_bar_reason = estimate_bar_fill_ratio_with_reason(frame, rr.roi, "mp")

        # Attach HUD debug snapshot (before fusion so we keep raw diagnostics).
        try:
            hud_debug["hp"] = {
                "ocr_method": hp_method,
                "ocr_reason": hp_reason,
                "bar_ratio": hp_ratio,
                "bar_reason": hp_bar_reason,
                "bar_roi": list(hp_bar_roi) if isinstance(hp_bar_roi, tuple) else None,
                "bar_roi_reason": hp_bar_roi_reason,
                "max": hp_max,
                "cur": hp_current,
            }
            hud_debug["mp"] = {
                "ocr_method": mp_method,
                "ocr_reason": mp_reason,
                "bar_ratio": mp_ratio,
                "bar_reason": mp_bar_reason,
                "bar_roi": list(mp_bar_roi) if isinstance(mp_bar_roi, tuple) else None,
                "bar_roi_reason": mp_bar_roi_reason,
                "max": mp_max,
                "cur": mp_current,
            }
            hud_debug["cap"] = {
                "method": cap_method,
                "reason": cap_reason,
                "cur": cap_current,
            }
        except Exception:
            pass

        # Fusion OCR vs barras: si difieren mucho, preferir barra.
        try:
            diff_thr = float(os.getenv("HPMP_BAR_FUSION_DIFF", "0.10").strip() or "0.10")
        except Exception:
            diff_thr = 0.10

        try:
            if hp_current is not None and hp_max and hp_ratio is not None:
                ocr_ratio = float(hp_current) / float(hp_max)
                if abs(float(ocr_ratio) - float(hp_ratio)) >= float(diff_thr):
                    hp_current = int(round(float(hp_ratio) * float(hp_max)))
                    hp_method = "bar_low_fusion"
                    hp_reason = "ratio_mismatch"
        except Exception:
            pass

        try:
            if mp_current is not None and mp_max and mp_ratio is not None:
                ocr_ratio = float(mp_current) / float(mp_max)
                if abs(float(ocr_ratio) - float(mp_ratio)) >= float(diff_thr):
                    mp_current = int(round(float(mp_ratio) * float(mp_max)))
                    mp_method = "bar_low_fusion"
                    mp_reason = "ratio_mismatch"
        except Exception:
            pass

        if hp_current is None and hp_max is not None and hp_ratio is not None:
            hp_current = int(round(hp_ratio * hp_max))
            hp_method = "bar_low"
            hp_reason = f"fallback:{hp_reason}" if hp_reason else "fallback_no_ocr"
        elif hp_current is None and not hp_method:
            hp_method = "none"
            hp_reason = "no_ocr_no_bar"

        if mp_current is None and mp_max is not None and mp_ratio is not None:
            mp_current = int(round(mp_ratio * mp_max))
            mp_method = "bar_low"
            mp_reason = f"fallback:{mp_reason}" if mp_reason else "fallback_no_ocr"
        elif mp_current is None and not mp_method:
            mp_method = "none"
            mp_reason = "no_ocr_no_bar"

        # Crear nuevo estado
        hp_pct: Optional[float] = None
        mp_pct: Optional[float] = None
        try:
            if hp_current is not None and hp_max:
                hp_pct = (float(hp_current) / float(hp_max)) * 100.0
            if mp_current is not None and mp_max:
                mp_pct = (float(mp_current) / float(mp_max)) * 100.0
        except Exception:
            hp_pct = None
            mp_pct = None

        gamestate = GameState(
            hp_current=hp_current,
            hp_max=hp_max,
            mp_current=mp_current,
            mp_max=mp_max,
            cap_current=cap_current,
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
            coords_provider=coords_provider_kind,
            coords_confidence=None,
            coords_provider_status=None,
            hp_pct=hp_pct,
            mp_pct=mp_pct,
            roboflow_boxes=rf_boxes,
            viewport_tile_offsets=None,
            hp_method=hp_method,
            hp_reason=hp_reason,
            mp_method=mp_method,
            mp_reason=mp_reason,
            cap_method=cap_method,
            cap_reason=cap_reason,
            hud_debug=hud_debug,
            frame_mean=frame_mean,
        )

        # Expose minimap tracker debug when minimap provider is selected (even if no step was accepted).
        try:
            if coords_provider_kind in {"minimap", "map", "minimap_motion"}:
                gamestate.minimap_mode_used = str(getattr(self._minimap_tracker, "last_mode_used", "") or "")
                try:
                    gamestate.minimap_response = float(getattr(self._minimap_tracker, "last_response", 0.0) or 0.0)
                except Exception:
                    gamestate.minimap_response = None

                try:
                    dx_f, dy_f = getattr(self._minimap_tracker, "last_delta_tiles_f", (0.0, 0.0)) or (0.0, 0.0)
                    gamestate.minimap_delta_dx = float(dx_f)
                    gamestate.minimap_delta_dy = float(dy_f)
                except Exception:
                    gamestate.minimap_delta_dx = None
                    gamestate.minimap_delta_dy = None

                try:
                    ax_f, ay_f = getattr(self._minimap_tracker, "last_acc_tiles_f", (0.0, 0.0)) or (0.0, 0.0)
                    gamestate.minimap_acc_dx = float(ax_f)
                    gamestate.minimap_acc_dy = float(ay_f)
                except Exception:
                    gamestate.minimap_acc_dx = None
                    gamestate.minimap_acc_dy = None

                try:
                    mdx, mdy = getattr(self._minimap_tracker, "last_marker_dpx", (0.0, 0.0)) or (0.0, 0.0)
                    gamestate.minimap_marker_dpx_dx = float(mdx)
                    gamestate.minimap_marker_dpx_dy = float(mdy)
                except Exception:
                    gamestate.minimap_marker_dpx_dx = None
                    gamestate.minimap_marker_dpx_dy = None

                try:
                    gamestate.coords_confidence = float(self._minimap_confidence)
                except Exception:
                    gamestate.coords_confidence = None

                try:
                    status = str(self._minimap_status or "")
                    level = None
                    try:
                        c = float(self._minimap_confidence)
                        if c >= 0.7:
                            level = "green"
                        elif c >= 0.4:
                            level = "amber"
                        else:
                            level = "red"
                    except Exception:
                        level = None
                    gamestate.coords_confidence_level = level
                    if level:
                        gamestate.coords_provider_status = f"{status}|{level}" if status else level
                    else:
                        gamestate.coords_provider_status = status
                except Exception:
                    gamestate.coords_provider_status = None

                # Structured provider state for UI/telemetry.
                try:
                    gamestate.coords_provider_state = {
                        "enabled": True,
                        "seed_ok": bool(self._minimap_seed is not None),
                        "coords_enabled": bool(gamestate.pos_x is not None and gamestate.pos_y is not None),
                        "confidence": gamestate.coords_confidence,
                        "last_update_ts": float(self._minimap_last_update_ts) if float(self._minimap_last_update_ts) > 0 else None,
                        "reason": str(self._minimap_status or ""),
                        "accept_streak": int(getattr(self, "_minimap_accept_streak", 0) or 0),
                        "low_conf_streak": int(getattr(self, "_minimap_low_conf_streak", 0) or 0),
                        "last_resp": float(getattr(self, "_minimap_last_resp", 0.0) or 0.0),
                    }
                except Exception:
                    gamestate.coords_provider_state = None
        except Exception:
            pass

        # (C) Equipment + status icons (best-effort, depends on calibrated ROIs)
        try:
            # Default: unknown until we can detect.
            gamestate.paralyzed = None
            gamestate.haste_active = None
            gamestate.utamo_active = None

            # These are *presence* flags; defaulting to False avoids UI confusion.
            # We only set True on a positive detection.
            gamestate.ring_equipped = False
            gamestate.amulet_equipped = False
            gamestate.hungry = False

            # Prefer dedicated ROIs if present.
            if isinstance(rois, dict):
                if rois.get("ring_slot") is not None:
                    rx, ry, rw, rh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["ring_slot"])
                    if rw >= 8 and rh >= 8:
                        try:
                            r_std = float(os.getenv("RING_ICON_MIN_STD", "18"))
                        except Exception:
                            r_std = 18.0
                        try:
                            r_mean = float(os.getenv("RING_ICON_MIN_MEAN", "8"))
                        except Exception:
                            r_mean = 8.0
                        try:
                            r_min_sat = float(os.getenv("RING_ICON_MIN_SAT_PCT", "0.006"))
                        except Exception:
                            r_min_sat = 0.006
                        try:
                            r_sat_thr = int(float(os.getenv("RING_ICON_SAT_THR", "30")))
                        except Exception:
                            r_sat_thr = 30
                        try:
                            r_v_thr = int(float(os.getenv("RING_ICON_V_THR", "40")))
                        except Exception:
                            r_v_thr = 40
                        try:
                            r_high_std = float(os.getenv("RING_ICON_HIGH_STD", "45"))
                        except Exception:
                            r_high_std = 45.0

                        gamestate.ring_equipped = bool(
                            is_nonempty_equipment_slot(
                                frame[ry : ry + rh, rx : rx + rw],
                                min_std=r_std,
                                min_mean=r_mean,
                                min_sat_pct=r_min_sat,
                                sat_thr=r_sat_thr,
                                v_thr=r_v_thr,
                                high_std=r_high_std,
                            )
                        )
                if rois.get("amulet_slot") is not None:
                    ax, ay, aw, ah = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["amulet_slot"])
                    if aw >= 8 and ah >= 8:
                        try:
                            a_std = float(os.getenv("AMULET_ICON_MIN_STD", "18"))
                        except Exception:
                            a_std = 18.0
                        try:
                            a_mean = float(os.getenv("AMULET_ICON_MIN_MEAN", "8"))
                        except Exception:
                            a_mean = 8.0
                        try:
                            a_min_sat = float(os.getenv("AMULET_ICON_MIN_SAT_PCT", "0.006"))
                        except Exception:
                            a_min_sat = 0.006
                        try:
                            a_sat_thr = int(float(os.getenv("AMULET_ICON_SAT_THR", "30")))
                        except Exception:
                            a_sat_thr = 30
                        try:
                            a_v_thr = int(float(os.getenv("AMULET_ICON_V_THR", "40")))
                        except Exception:
                            a_v_thr = 40
                        try:
                            a_high_std = float(os.getenv("AMULET_ICON_HIGH_STD", "45"))
                        except Exception:
                            a_high_std = 45.0

                        gamestate.amulet_equipped = bool(
                            is_nonempty_equipment_slot(
                                frame[ay : ay + ah, ax : ax + aw],
                                min_std=a_std,
                                min_mean=a_mean,
                                min_sat_pct=a_min_sat,
                                sat_thr=a_sat_thr,
                                v_thr=a_v_thr,
                                high_std=a_high_std,
                            )
                        )

                # Hunger icon: prefer tight ROI if available; else fall back to HSV heuristic on states_icons.
                if rois.get("hungry_icon") is not None:
                    hx, hy, hw, hh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["hungry_icon"])
                    if hw >= 6 and hh >= 6:
                        # Use color-band detection to avoid false positives from slot texture.
                        try:
                            min_pct = float(os.getenv("HUNGRY_MIN_PCT", "0.012"))
                        except Exception:
                            min_pct = 0.012
                        try:
                            low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
                        except Exception:
                            low_h = 8
                        try:
                            high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
                        except Exception:
                            high_h = 35
                        gamestate.hungry = bool(
                            is_hungry_hsv(
                                frame[hy : hy + hh, hx : hx + hw],
                                min_pct=min_pct,
                                low_h=low_h,
                                high_h=high_h,
                            )
                        )
                elif rois.get("states_icons") is not None:
                    sx, sy, sw, sh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["states_icons"])
                    crop = frame[sy : sy + sh, sx : sx + sw]

                    # Status icons (template matching, optional templates).
                    try:
                        confs = detect_status_icons(crop)
                    except Exception:
                        confs = {}

                    if confs:
                        # Debounce to reduce flicker: require K consecutive frames
                        # to toggle the state on/off.
                        k = int(getattr(self, "_status_persist_k", 3) or 3)
                        thr = float(getattr(self, "_status_match_thr", 0.65) or 0.65)

                        for key in ("paralyzed", "haste_active", "utamo_active"):
                            try:
                                detected = float(confs.get(key, 0.0) or 0.0) >= thr
                            except Exception:
                                detected = False

                            if detected:
                                self._status_on_streak[key] = int(self._status_on_streak.get(key, 0) or 0) + 1
                                self._status_off_streak[key] = 0
                                if int(self._status_on_streak[key]) >= int(k):
                                    self._status_state[key] = True
                            else:
                                self._status_off_streak[key] = int(self._status_off_streak.get(key, 0) or 0) + 1
                                self._status_on_streak[key] = 0
                                if int(self._status_off_streak[key]) >= int(k):
                                    self._status_state[key] = False

                        gamestate.paralyzed = bool(self._status_state.get("paralyzed", False))
                        gamestate.haste_active = bool(self._status_state.get("haste_active", False))
                        gamestate.utamo_active = bool(self._status_state.get("utamo_active", False))

                    # Tunables via env vars
                    min_pct = float(os.getenv("HUNGRY_MIN_PCT", "0.012"))
                    low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
                    high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
                    gamestate.hungry = bool(is_hungry_hsv(crop, min_pct=min_pct, low_h=low_h, high_h=high_h))
        except Exception:
            pass

        # (D) Battlelist rows (best-effort, depends on calibrated ROIs)
        try:
            # Always set fields every tick (even if empty/missing) to satisfy UI/telemetry assumptions.
            gamestate.battlelist_entries = []
            gamestate.battlelist_n_rows = 0
            gamestate.battlelist_n_valid = 0
            gamestate.battlelist_top_names = []
            gamestate.battlelist_confidence = 0.0
            gamestate.battlelist_source = "none"
            gamestate.battlelist_reason = "missing_roi"

            if isinstance(rois, dict) and rois.get("battlelist_rows") is not None:
                rr = roi_to_px_result(
                    frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                    rois=rois,
                    resolution=resolution,
                    roi_def=rois["battlelist_rows"],
                )
                gamestate.battlelist_source = "roi:battlelist_rows"
                gamestate.battlelist_reason = str(rr.reason or "")
                if not (rr.ok and rr.roi is not None):
                    try:
                        if isinstance(gamestate.hud_debug, dict):
                            gamestate.hud_debug["battlelist"] = {
                                "source": gamestate.battlelist_source,
                                "reason": gamestate.battlelist_reason,
                                "roi": None,
                            }
                    except Exception:
                        pass
                else:
                    bx, by, bw, bh = rr.roi
                    crop = frame[by : by + bh, bx : bx + bw]

                    rows = battlelist.extract_rows(crop)
                    parsed = [battlelist.parse_row(rimg, i) for i, rimg in enumerate(rows)]
                    stable = battlelist.stabilize(self._battlelist_buf, parsed, window_n=10)

                    # Global confidence = avg_conf_valid * pct_valid
                    valid = []
                    for e in stable:
                        try:
                            name_norm = str(e.get("name_norm", "") or "").strip()
                            conf = float(e.get("conf", 0.0) or 0.0)
                            if name_norm and conf > 0.0:
                                valid.append(e)
                        except Exception:
                            continue

                    n_rows = int(len(stable))
                    n_valid = int(len(valid))
                    avg_conf = 0.0
                    if n_valid:
                        try:
                            avg_conf = float(
                                sum(float(v.get("conf", 0.0) or 0.0) for v in valid) / float(n_valid)
                            )
                        except Exception:
                            avg_conf = 0.0
                    pct_valid = (float(n_valid) / float(n_rows)) if n_rows else 0.0
                    global_conf = float(avg_conf * pct_valid)

                    # Top names: stable order, first occurrences
                    top_names: list[str] = []
                    for e in valid:
                        try:
                            disp = str(e.get("name_raw", "") or e.get("name_display", "") or "").strip()
                            if not disp:
                                continue
                            if disp not in top_names:
                                top_names.append(disp)
                        except Exception:
                            continue

                    gamestate.battlelist_entries = stable
                    gamestate.battlelist_n_rows = n_rows
                    gamestate.battlelist_n_valid = n_valid
                    gamestate.battlelist_top_names = top_names[:10]
                    gamestate.battlelist_confidence = global_conf
                    gamestate.battlelist_reason = "ok" if n_rows else "empty_crop"
                    if n_rows and n_valid == 0:
                        gamestate.battlelist_reason = "no_valid_rows"

                    try:
                        if isinstance(gamestate.hud_debug, dict):
                            gamestate.hud_debug["battlelist"] = {
                                "source": gamestate.battlelist_source,
                                "reason": gamestate.battlelist_reason,
                                "roi": [int(bx), int(by), int(bw), int(bh)],
                                "n_rows": n_rows,
                                "n_valid": n_valid,
                                "confidence": float(global_conf),
                            }
                    except Exception:
                        pass
        except Exception:
            pass

        # Derivar obstáculos dinámicos (tile offsets) desde detecciones Roboflow.
        # Esto es útil para pathfinding local con replan.
        try:
            if rf_boxes and isinstance(rois, dict) and "game_viewport" in rois:
                vp = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["game_viewport"])
                tile_px_raw = os.getenv("TIBIA_TILE_PX", "32").strip()
                try:
                    tile_px = max(8, int(tile_px_raw))
                except Exception:
                    tile_px = 32
                viewport_offsets = compute_viewport_tile_offsets(
                    rf_boxes,
                    viewport_rect=vp,
                    tile_px=tile_px,
                    min_conf=float(os.getenv("OBSTACLE_MIN_CONF", "0.25")),
                    max_abs_offset=None,
                )
                gamestate.viewport_tile_offsets = viewport_offsets
        except Exception:
            pass

        # Aquí podríamos implementar lógica adicional para determinar hp_max/mp_max
        # Por ahora, los dejamos como None o podríamos estimarlos

        return gamestate