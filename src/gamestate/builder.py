from typing import Optional, Dict, Any, Tuple, List
import os
import time
import json
from pathlib import Path
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor, get_shared_ocr_processor
from vision.roboflow_inference import RoboflowInference
from vision.bar_analysis import estimate_bar_fill_ratio_with_reason
from vision.obstacles import compute_viewport_tile_offsets
from vision.presence import detect_equipment_slot, is_hungry_hsv, is_nonempty_equipment_slot, detect_status_icons
from vision.minimap_motion import MinimapMotionTracker
from vision import battlelist
from vision.roi import roi_to_px_result
from layout_tracker import LayoutTracker


def _is_ocr_confident(reason: str) -> bool:
    """Best-effort classification for OCR reliability.

    We only use this to decide whether to let bar ratios override OCR.
    """

    r = str(reason or "").strip().lower()
    if not r:
        return False
    # Explicit failure modes
    bad_tokens = ["parse_fail", "single_number", "invalid", "exception", "no_digits"]
    if any(t in r for t in bad_tokens):
        return False
    # Accept common OK markers.
    if r == "ok" or r.startswith("ok|") or r.startswith("ok_") or r.startswith("ok:"):
        return True
    # Some paths prefix info like "reuse_last_max:ok".
    if "ok" in r and "parse_fail" not in r:
        return True
    return False


def _should_override_ocr_with_bar(*, ocr_reason: str, bar_presence: float, diff: float) -> bool:
    """Decide if we should override OCR with bar ratio in fusion step."""

    try:
        min_pres = float(os.getenv("HPMP_BAR_FUSION_MIN_PRESENCE", "0.06").strip() or "0.06")
    except Exception:
        min_pres = 0.06
    min_pres = float(max(0.0, min(0.5, min_pres)))

    # If OCR looks good, don't override (bars can drift with ROI misalignment).
    if _is_ocr_confident(ocr_reason):
        return False

    # Only trust bars when we have a decent amount of bar-colored pixels.
    return float(bar_presence or 0.0) >= float(min_pres) and float(diff or 0.0) > 0.0


def _sanitize_current_max(
    cur: Optional[int],
    mx: Optional[int],
    *,
    last_cur: Optional[int],
    last_mx: Optional[int],
) -> tuple[Optional[int], Optional[int], str]:
    """Sanitize OCR values to avoid impossible states like current > max.

    Returns (cur, mx, reason_suffix).
    """

    if cur is None or mx is None:
        return cur, mx, ""
    try:
        cur_i = int(cur)
        mx_i = int(mx)
    except Exception:
        return None, mx, "invalid_int"

    if mx_i <= 0:
        return None, None, "invalid_max"
    if cur_i < 0:
        return None, mx_i, "invalid_cur"
    if cur_i <= mx_i:
        return cur_i, mx_i, ""

    # cur > max: prefer last-known sane value when it matches this max.
    try:
        if last_cur is not None and last_mx is not None:
            lc = int(last_cur)
            lm = int(last_mx)
            if lm == mx_i and 0 <= lc <= mx_i:
                return lc, mx_i, "cur_gt_max_use_last"
    except Exception:
        pass

    return None, mx_i, "cur_gt_max_drop_cur"


@dataclass
class DebouncedBoolean:
    """Debounce a boolean signal with tri-state inputs.

    - `observed=True/False`: updates streaks and may toggle stable state.
    - `observed=None`: no new info; stable state may decay from True to None
      after `max_hold_ms` to avoid stickiness.
    """

    on_frames: int = 2
    off_frames: int = 2
    max_hold_ms: int = 700
    stable: bool | None = None
    on_streak: int = 0
    off_streak: int = 0
    last_true_ts: float = 0.0
    last_observed: bool | None = None

    def _apply_hold_decay(self, *, now_ts: float) -> None:
        try:
            if self.stable is True and int(self.max_hold_ms) > 0 and float(self.last_true_ts) > 0.0:
                if (float(now_ts) - float(self.last_true_ts)) * 1000.0 >= float(self.max_hold_ms):
                    self.stable = None
        except Exception:
            pass

    def update(self, observed: bool | None, *, now_ts: float) -> bool | None:
        self.last_observed = observed
        if observed is None:
            self._apply_hold_decay(now_ts=float(now_ts))
            return self.stable

        try:
            if bool(observed):
                self.on_streak = int(self.on_streak) + 1
                self.off_streak = 0
                if int(self.on_streak) >= int(max(1, self.on_frames)):
                    self.stable = True
                    self.last_true_ts = float(now_ts)
            else:
                self.off_streak = int(self.off_streak) + 1
                self.on_streak = 0
                if int(self.off_streak) >= int(max(1, self.off_frames)):
                    self.stable = False
        except Exception:
            pass
        return self.stable


@dataclass
class GameState:
    """Estado actual del juego"""
    hp_current: Optional[int] = None
    hp_max: Optional[int] = None
    mp_current: Optional[int] = None
    mp_max: Optional[int] = None
    cap_current: Optional[int] = None
    soul_current: Optional[int] = None
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

    # SOUL extraction observability
    soul_method: Optional[str] = None
    soul_reason: Optional[str] = None

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
            f"Cap: {self.cap_current}, Soul: {self.soul_current}{pos}, ring: {self.ring_equipped}, amulet: {self.amulet_equipped}, "
            f"paralyzed: {self.paralyzed}, haste: {self.haste_active}, utamo: {self.utamo_active}, hungry: {self.hungry}, RF: {rf_n}"
        )

class GameStateBuilder:
    def __init__(self):
        # Reuse a single OCR instance across subsystems (avoids repeated GPU init).
        self.ocr_processor = get_shared_ocr_processor()
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
        self._last_soul_current: Optional[int] = None
        self._last_pos_x: Optional[int] = None
        self._last_pos_y: Optional[int] = None
        self._last_pos_z: Optional[int] = None

        # HP/MP top-strip signature for cheap OCR caching.
        self._last_hpmp_strip_sig: bytes | None = None

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
        self._minimap_last_update_ts: float = 0.0
        self._minimap_low_conf_streak: int = 0
        self._minimap_force_disable_coords: bool = False

        # Battlelist stabilization buffer: row_index -> last N parsed entries
        self._battlelist_buf: Dict[int, List[Dict[str, Any]]] = {}

        # Battlelist OCR is expensive (per-row EasyOCR). Throttle it aggressively
        # to keep the vision thread real-time.
        #
        # Tunables:
        #   - BATTLELIST_MIN_INTERVAL_S (default 10s)
        #   - BATTLELIST_PARSE_MAX_ROWS (default 3)
        #
        # Set BATTLELIST_MIN_INTERVAL_S=0 to restore "every frame" behavior.
        self._battlelist_last_ts = 0.0
        try:
            self._battlelist_min_interval_s = float(
                (os.getenv("BATTLELIST_MIN_INTERVAL_S", "10.0") or "10.0").strip() or "10.0"
            )
        except Exception:
            self._battlelist_min_interval_s = 10.0
        self._battlelist_min_interval_s = float(max(0.0, self._battlelist_min_interval_s))
        try:
            self._battlelist_parse_max_rows = int(
                float((os.getenv("BATTLELIST_PARSE_MAX_ROWS", "3") or "3").strip() or "3")
            )
        except Exception:
            self._battlelist_parse_max_rows = 3
        self._battlelist_parse_max_rows = int(max(0, min(30, self._battlelist_parse_max_rows)))
        self._battlelist_cache: Dict[str, Any] = {}

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

        # Presence flags (ring/amulet/hungry): real-time debounce + anti-stickiness.
        # Defaults are chosen to meet the UI latency target (<200ms @ ~10fps).
        try:
            self._presence_persist_k = int(float(os.getenv("PRESENCE_PERSIST_K", "0").strip() or "0"))
        except Exception:
            self._presence_persist_k = 0
        self._presence_persist_k = max(0, min(20, int(self._presence_persist_k)))

        def _env_int(name: str, default: int) -> int:
            raw = (os.getenv(name, "") or "").strip()
            if raw:
                try:
                    return int(float(raw))
                except Exception:
                    return int(default)
            return int(default)

        def _env_ms(name: str, default: int) -> int:
            try:
                return int(float((os.getenv(name, str(default)) or str(default)).strip() or str(default)))
            except Exception:
                return int(default)

        # Back-compat: if PRESENCE_PERSIST_K was used previously, map it to
        # on/off frames only when explicit PRESENCE_ON_FRAMES/OFF_FRAMES are unset.
        on_frames = _env_int("PRESENCE_ON_FRAMES", 2)
        off_frames = _env_int("PRESENCE_OFF_FRAMES", 2)
        if int(self._presence_persist_k) > 0:
            if (os.getenv("PRESENCE_ON_FRAMES", "") or "").strip() == "":
                on_frames = int(self._presence_persist_k)
            if (os.getenv("PRESENCE_OFF_FRAMES", "") or "").strip() == "":
                off_frames = int(self._presence_persist_k)
        on_frames = max(1, min(20, int(on_frames)))
        off_frames = max(1, min(20, int(off_frames)))
        max_hold_ms = max(0, min(5000, int(_env_ms("PRESENCE_MAX_HOLD_MS", 700))))

        self._presence_debouncers: Dict[str, DebouncedBoolean] = {
            "ring_equipped": DebouncedBoolean(on_frames=on_frames, off_frames=off_frames, max_hold_ms=max_hold_ms),
            "amulet_equipped": DebouncedBoolean(on_frames=on_frames, off_frames=off_frames, max_hold_ms=max_hold_ms),
            "hungry": DebouncedBoolean(on_frames=on_frames, off_frames=off_frames, max_hold_ms=max_hold_ms),
        }

        # Vision real-time guard: if a vision update becomes too slow (often due to
        # OCR/EasyOCR or Roboflow inference), the pipeline can effectively stall and
        # produce stale/no GameState.
        #
        # This guard is intentionally conservative and fail-safe:
        # - It never raises.
        # - It skips expensive work for a short cooldown window.
        # - HP/MP % can still be derived from bars even without OCR.
        #
        # Env tunables:
        #   - VISION_GUARD_ENABLED=1|0 (default: 1)
        #   - VISION_GUARD_SLOW_MS (default: 800)
        #   - VISION_GUARD_COOLDOWN_S (default: 2.0)
        #   - VISION_GUARD_FORCE=1 (default: 0) force skip expensive work always
        try:
            raw = (os.getenv("VISION_GUARD_ENABLED", "1") or "1").strip().lower()
            self._vision_guard_enabled = raw not in {"0", "false", "no", "off"}
        except Exception:
            self._vision_guard_enabled = True
        try:
            self._vision_guard_slow_ms = float((os.getenv("VISION_GUARD_SLOW_MS", "800") or "800").strip() or "800")
        except Exception:
            self._vision_guard_slow_ms = 800.0
        self._vision_guard_slow_ms = float(max(1.0, self._vision_guard_slow_ms))
        try:
            self._vision_guard_cooldown_s = float((os.getenv("VISION_GUARD_COOLDOWN_S", "2.0") or "2.0").strip() or "2.0")
        except Exception:
            self._vision_guard_cooldown_s = 2.0
        self._vision_guard_cooldown_s = float(max(0.0, self._vision_guard_cooldown_s))
        self._vision_guard_until_ts = 0.0
        self._vision_last_update_ms = 0.0

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
            rr = roi_to_px_result(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=rois["minimap_content"],
            )
            if not (rr.ok and rr.roi is not None):
                self._minimap_status = "minimap_no_crop"
                return self._minimap_coords

            x, y, w, h = rr.roi
            crop = frame[y : y + h, x : x + w].copy()
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

    def _hpmp_strip_signature(self, crop: np.ndarray) -> bytes | None:
        """Compute a small signature for the hpmp_top_strip crop.

        Used to skip expensive OCR when the strip didn't change.
        """

        if crop is None or not isinstance(crop, np.ndarray) or crop.size == 0:
            return None
        try:
            import cv2

            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            # Very small downsample is enough to detect UI changes.
            small = cv2.resize(gray, (32, 8), interpolation=cv2.INTER_AREA)
            return small.tobytes()
        except Exception:
            try:
                # Fallback: mean/std only (less reliable but safe).
                m = float(np.mean(crop))
                s = float(np.std(crop))
                return f"{m:.2f}|{s:.2f}".encode("utf-8")
            except Exception:
                return None

    def update_from_frame(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> GameState:
        """Actualiza el estado del juego desde un frame"""
        t0 = time.time()
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

        # Real-time guard: temporarily skip expensive work on slow frames.
        guard_force = False
        try:
            guard_force = (os.getenv("VISION_GUARD_FORCE", "0") or "0").strip().lower() in {"1", "true", "yes"}
        except Exception:
            guard_force = False
        guard_active = False
        try:
            guard_active = bool(self._vision_guard_enabled) and (bool(guard_force) or (float(now) < float(self._vision_guard_until_ts or 0.0)))
        except Exception:
            guard_active = bool(guard_force)

        if self._rf is not None:
            if (not guard_active) and (now - self._rf_last_ts >= self._rf_min_interval_s):
                try:
                    pred = self._rf.predict(frame)
                    rf_boxes = self._rf.extract_boxes(pred)
                except Exception:
                    rf_boxes = None
                self._rf_last_ts = now

        # Separate inference for HP/MP
        if self._rf_hpmp is not None:
            if (not guard_active) and (now - self._rf_hpmp_last_ts >= self._rf_hpmp_min_interval_s):
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
        soul_current: Optional[int] = None
        pos_x: Optional[int] = None
        pos_y: Optional[int] = None
        pos_z: Optional[int] = None

        do_ocr = (now - self._ocr_last_ts) >= self._ocr_min_interval_s
        if guard_active:
            do_ocr = False

        # Battlelist OCR throttling (separate from general OCR throttle).
        do_battlelist = True
        try:
            if float(getattr(self, "_battlelist_min_interval_s", 0.0) or 0.0) > 0.0:
                do_battlelist = (now - float(getattr(self, "_battlelist_last_ts", 0.0) or 0.0)) >= float(
                    getattr(self, "_battlelist_min_interval_s", 0.0) or 0.0
                )
        except Exception:
            do_battlelist = True
        if guard_active:
            do_battlelist = False
        # Observability of OCR path
        hp_method = ""
        hp_reason = ""
        mp_method = ""
        mp_reason = ""

        soul_method = ""
        soul_reason = ""

        # Strip signature caching: when the strip ROI exists and hasn't changed,
        # reuse last-known values and skip OCR (keeps the pipeline real-time).
        if do_ocr:
            try:
                if isinstance(rois, dict) and rois.get("hpmp_top_strip") is not None:
                    rr = roi_to_px_result(
                        frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                        rois=rois,
                        resolution=resolution,
                        roi_def=rois["hpmp_top_strip"],
                    )
                    if rr.ok and rr.roi is not None:
                        x, y, w, h = rr.roi
                        if int(w) >= 12 and int(h) >= 6:
                            crop = frame[int(y) : int(y) + int(h), int(x) : int(x) + int(w)]
                            sig = self._hpmp_strip_signature(crop)
                            if sig is not None and sig == getattr(self, "_last_hpmp_strip_sig", None):
                                if any(
                                    v is not None
                                    for v in [
                                        getattr(self, "_last_hp_current", None),
                                        getattr(self, "_last_hp_max", None),
                                        getattr(self, "_last_mp_current", None),
                                        getattr(self, "_last_mp_max", None),
                                    ]
                                ):
                                    do_ocr = False
                                    hp_method = "cached_strip"
                                    mp_method = "cached_strip"
                                    hp_reason = "strip_unchanged"
                                    mp_reason = "strip_unchanged"
                            self._last_hpmp_strip_sig = sig
            except Exception:
                pass
        cap_method = ""
        cap_reason = ""

        hud_debug: Dict[str, Any] = {
            "ts": float(now),
            "resolution": [int(resolution[0]), int(resolution[1])],
            "frame_mean": frame_mean,
            "vision_guard": {"active": bool(guard_active), "force": bool(guard_force)},
            "hp": {},
            "mp": {},
            "cap": {},
            "soul": {},
            "battlelist": {},
            "presence": {},
        }

        # Layout snapshot + px conversion for critical ROIs (debug/telemetry).
        try:
            layout_state = None
            if hasattr(rois, "get"):
                ls = rois.get("_layout_state")
                if isinstance(ls, dict):
                    layout_state = dict(ls)
            hud_debug["layout"] = layout_state
        except Exception:
            hud_debug["layout"] = None

        try:
            hud_debug["rois_px"] = LayoutTracker.apply_layout(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                names=[
                    "hp_top_ocr",
                    "mp_top_ocr",
                    "hp_low_bar",
                    "mp_low_bar",
                    "cap_ocr",
                    "skills_panel",
                    "battlelist_rows",
                    "coords_ocr",
                    "game_viewport",
                ],
            )
        except Exception:
            hud_debug["rois_px"] = {}

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
                dbg = None
                try:
                    dbg = getattr(self.ocr_processor, "last_cap_debug", None)
                except Exception:
                    dbg = None

                if isinstance(dbg, dict) and dbg:
                    try:
                        cap_method = str(dbg.get("chosen_source", "") or "")
                        cap_reason = str(dbg.get("decision", "") or "")
                    except Exception:
                        cap_method = ""
                        cap_reason = ""
                else:
                    # Fallback (legacy): we only know whether cap_ocr exists.
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

            try:
                soul_current = self.ocr_processor.extract_soul(frame, rois, resolution)
                dbg = None
                try:
                    dbg = getattr(self.ocr_processor, "last_soul_debug", None)
                except Exception:
                    dbg = None

                if isinstance(dbg, dict) and dbg:
                    try:
                        soul_method = str(dbg.get("chosen_source", "") or "")
                        soul_reason = str(dbg.get("decision", "") or "")
                    except Exception:
                        soul_method = ""
                        soul_reason = ""
                else:
                    if soul_current is not None:
                        soul_method = "soul_ocr" if (hasattr(rois, "get") and rois.get("soul_ocr") is not None) else "skills_panel"
                        soul_reason = "ok"
                    else:
                        soul_method = "soul_ocr" if (hasattr(rois, "get") and rois.get("soul_ocr") is not None) else "skills_panel"
                        soul_reason = "no_digits"
            except Exception:
                soul_current = None
                soul_method = "soul_ocr" if (hasattr(rois, "get") and rois.get("soul_ocr") is not None) else "skills_panel"
                soul_reason = "exception"

            # If OCR returned no SOUL this tick, prefer last-known to avoid UI jitter.
            try:
                if soul_current is None and getattr(self, "_last_soul_current", None) is not None:
                    soul_current = int(getattr(self, "_last_soul_current"))
                    if not soul_method:
                        soul_method = "cached"
                    soul_reason = f"reuse_last:{soul_reason}" if soul_reason else "reuse_last"
            except Exception:
                pass

            self._ocr_last_ts = now
            # Cache only when we have a value, so intermittent OCR failures
            # don't erase a previously known max/current.
            if hp_current is not None:
                self._last_hp_current = hp_current
            if hp_max is not None:
                self._last_hp_max = hp_max
            if mp_current is not None:
                self._last_mp_current = mp_current
            if mp_max is not None:
                self._last_mp_max = mp_max
            if cap_current is not None:
                self._last_cap_current = cap_current
            if soul_current is not None:
                self._last_soul_current = soul_current
        else:
            # Reusar lo último conocido
            hp_current = self._last_hp_current
            hp_max = self._last_hp_max
            mp_current = self._last_mp_current
            mp_max = self._last_mp_max
            cap_current = self._last_cap_current
            soul_current = self._last_soul_current
            pos_x = self._last_pos_x
            pos_y = self._last_pos_y
            pos_z = self._last_pos_z
            hp_method = "cached"
            mp_method = "cached"
            hp_reason = "ocr_throttled"
            mp_reason = "ocr_throttled"
            cap_method = "cached"
            cap_reason = "ocr_throttled"
            soul_method = "cached"
            soul_reason = "ocr_throttled"

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

        # Compatibility: if OCR intermittently misses the '/max' part, reuse
        # the last known max (do not erase stable maxima due to transient OCR).
        try:
            if hp_max is None and hp_current is not None and self._last_hp_max is not None:
                if 0 < int(hp_current) <= int(self._last_hp_max):
                    hp_max = int(self._last_hp_max)
                    if not hp_method or hp_method in {"top_ocr", "top_strip", "rf_box"}:
                        hp_method = "cached_max"
                    hp_reason = f"reuse_last_max:{hp_reason}" if hp_reason else "reuse_last_max"
        except Exception:
            pass
        try:
            if mp_max is None and mp_current is not None and self._last_mp_max is not None:
                if 0 < int(mp_current) <= int(self._last_mp_max):
                    mp_max = int(self._last_mp_max)
                    if not mp_method or mp_method in {"top_ocr", "top_strip", "rf_box"}:
                        mp_method = "cached_max"
                    mp_reason = f"reuse_last_max:{mp_reason}" if mp_reason else "reuse_last_max"
        except Exception:
            pass

        # Final sanity: prevent impossible OCR like 1215/215.
        try:
            hp_current, hp_max, why = _sanitize_current_max(
                hp_current,
                hp_max,
                last_cur=getattr(self, "_last_hp_current", None),
                last_mx=getattr(self, "_last_hp_max", None),
            )
            if why:
                hp_reason = f"{hp_reason}|{why}" if hp_reason else why
        except Exception:
            pass
        try:
            mp_current, mp_max, why = _sanitize_current_max(
                mp_current,
                mp_max,
                last_cur=getattr(self, "_last_mp_current", None),
                last_mx=getattr(self, "_last_mp_max", None),
            )
            if why:
                mp_reason = f"{mp_reason}|{why}" if mp_reason else why
        except Exception:
            pass

        # (B) Fallback por barras: si current falta y hay max, estimar por fill ratio
        # Intentar usar boxes Roboflow si existen; si no, usar ROIs del config.
        hp_ratio: Optional[float] = None
        mp_ratio: Optional[float] = None
        hp_bar_reason = ""
        mp_bar_reason = ""
        hp_bar_presence: float = 0.0
        mp_bar_presence: float = 0.0
        # Always compute both sources when possible for observability.
        hp_low_ratio: Optional[float] = None
        hp_low_reason: str = ""
        hp_low_presence: float = 0.0
        mp_low_ratio: Optional[float] = None
        mp_low_reason: str = ""
        mp_low_presence: float = 0.0

        hp_top_ratio: Optional[float] = None
        hp_top_reason: str = ""
        hp_top_presence: float = 0.0
        mp_top_ratio: Optional[float] = None
        mp_top_reason: str = ""
        mp_top_presence: float = 0.0

        chosen_bar_source: str = ""
        hp_bar_roi = None
        mp_bar_roi = None
        hp_bar_roi_reason = ""
        mp_bar_roi_reason = ""

        def _bar_est(img, roi, kind: str) -> tuple[Optional[float], str, float]:
            """Normalize bar estimation return shape.

            New API returns (ratio, reason, presence). Older mocks/tests may
            return (ratio, reason).
            """

            try:
                out = estimate_bar_fill_ratio_with_reason(img, roi, kind)
            except Exception:
                return None, "exception", 0.0

            try:
                if isinstance(out, tuple) and len(out) == 3:
                    r0, rs0, p0 = out
                    return (r0, str(rs0 or ""), float(p0 or 0.0))
                if isinstance(out, tuple) and len(out) == 2:
                    r0, rs0 = out
                    return (r0, str(rs0 or ""), 0.0)
            except Exception:
                return None, "invalid_return", 0.0
            return None, "invalid_return", 0.0
        rf_hp_ratio: Optional[float] = None
        rf_hp_reason: str = ""
        rf_hp_presence: float = 0.0
        rf_mp_ratio: Optional[float] = None
        rf_mp_reason: str = ""
        rf_mp_presence: float = 0.0

        if rf_hpmp_boxes and self._rf_hpmp is not None:
            hp_bar_classes = os.getenv("ROBOFLOW_HP_BAR_CLASSES", "hp_bar,hp_low_bar,health_bar").split(",")
            mp_bar_classes = os.getenv("ROBOFLOW_MP_BAR_CLASSES", "mp_bar,mp_low_bar,mana_bar").split(",")
            hp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in hp_bar_classes])
            mp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in mp_bar_classes])
            if hp_box:
                crop = self._rf_hpmp.crop_from_box(frame, hp_box)
                if crop is not None:
                    rr_roi = (0, 0, int(crop.shape[1]), int(crop.shape[0]))
                    rf_hp_ratio, rf_hp_reason, rf_hp_presence = _bar_est(crop, rr_roi, "hp")
            if mp_box:
                crop = self._rf_hpmp.crop_from_box(frame, mp_box)
                if crop is not None:
                    rr_roi = (0, 0, int(crop.shape[1]), int(crop.shape[0]))
                    rf_mp_ratio, rf_mp_reason, rf_mp_presence = _bar_est(crop, rr_roi, "mp")

        # Legacy low bars (ROI-driven)
        if "hp_low_bar" in rois:
            rr = roi_to_px_result(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=rois["hp_low_bar"],
                min_w=2,
                min_h=2,
            )
            low_reason = str(rr.reason or "")
            if rr.ok and rr.roi is not None:
                hp_low_ratio, hp_low_reason, hp_low_presence = _bar_est(frame, rr.roi, "hp")
                if not hp_low_reason and low_reason:
                    hp_low_reason = low_reason

        if "mp_low_bar" in rois:
            rr = roi_to_px_result(
                frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                rois=rois,
                resolution=resolution,
                roi_def=rois["mp_low_bar"],
                min_w=2,
                min_h=2,
            )
            low_reason = str(rr.reason or "")
            if rr.ok and rr.roi is not None:
                mp_low_ratio, mp_low_reason, mp_low_presence = _bar_est(frame, rr.roi, "mp")
                if not mp_low_reason and low_reason:
                    mp_low_reason = low_reason

        # Top strip bars (ROI-driven). Useful for logging + alternate HUD layouts.
        try:
            if isinstance(rois, dict) and "hpmp_top_strip" in rois:
                rr = roi_to_px_result(
                    frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                    rois=rois,
                    resolution=resolution,
                    roi_def=rois["hpmp_top_strip"],
                )
                if rr.ok and rr.roi is not None:
                    x, y, w, h = rr.roi
                    crop = frame[int(y) : int(y + h), int(x) : int(x + w)]
                    if crop is not None and getattr(crop, "size", 0) > 0:
                        try:
                            top_roi = (0, 0, int(crop.shape[1]), int(crop.shape[0]))
                            hp_top_ratio, hp_top_reason, hp_top_presence = _bar_est(crop, top_roi, "hp")
                            mp_top_ratio, mp_top_reason, mp_top_presence = _bar_est(crop, top_roi, "mp")
                        except Exception:
                            pass
        except Exception:
            pass

        # Choose a single bar source for the main HP/MP estimation.
        try:
            if rf_hp_ratio is not None:
                hp_ratio, hp_bar_reason, hp_bar_presence = rf_hp_ratio, rf_hp_reason, rf_hp_presence
                chosen_bar_source = "rf"
            elif hp_low_ratio is not None:
                hp_ratio, hp_bar_reason, hp_bar_presence = hp_low_ratio, hp_low_reason, hp_low_presence
                chosen_bar_source = "low"
                if "hp_low_bar" in rois:
                    rr = roi_to_px_result(
                        frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                        rois=rois,
                        resolution=resolution,
                        roi_def=rois["hp_low_bar"],
                        min_w=2,
                        min_h=2,
                    )
                    if rr.ok and rr.roi is not None:
                        hp_bar_roi = rr.roi
                        hp_bar_roi_reason = str(rr.reason or "")
            elif hp_top_ratio is not None:
                hp_ratio, hp_bar_reason, hp_bar_presence = hp_top_ratio, hp_top_reason, hp_top_presence
                chosen_bar_source = "top"
                hp_bar_roi_reason = "from_top_strip"
        except Exception:
            pass

        try:
            if rf_mp_ratio is not None:
                mp_ratio, mp_bar_reason, mp_bar_presence = rf_mp_ratio, rf_mp_reason, rf_mp_presence
                if not chosen_bar_source:
                    chosen_bar_source = "rf"
            elif mp_low_ratio is not None:
                mp_ratio, mp_bar_reason, mp_bar_presence = mp_low_ratio, mp_low_reason, mp_low_presence
                if not chosen_bar_source:
                    chosen_bar_source = "low"
                if "mp_low_bar" in rois:
                    rr = roi_to_px_result(
                        frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                        rois=rois,
                        resolution=resolution,
                        roi_def=rois["mp_low_bar"],
                        min_w=2,
                        min_h=2,
                    )
                    if rr.ok and rr.roi is not None:
                        mp_bar_roi = rr.roi
                        mp_bar_roi_reason = str(rr.reason or "")
            elif mp_top_ratio is not None:
                mp_ratio, mp_bar_reason, mp_bar_presence = mp_top_ratio, mp_top_reason, mp_top_presence
                if not chosen_bar_source:
                    chosen_bar_source = "top"
                mp_bar_roi_reason = "from_top_strip"
        except Exception:
            pass

        # (B0) Conservative max inference: if OCR gave a plausible *current* but
        # missed the '/max', and we have a reliable bar ratio, infer max once
        # and cache it. This improves UI/telemetry stability without requiring
        # manual TIBIA_HP_MAX/TIBIA_MP_MAX.
        # Special-case: if the bar is essentially full, we can safely assume
        # max == current (restores classic HUD readings like 215/215, 100/100
        # when OCR drops the slash/max).
        def _infer_max_if_full(cur: Optional[int], ratio: Optional[float]) -> Optional[int]:
            try:
                if cur is None or ratio is None:
                    return None
                c = int(cur)
                if c <= 0:
                    return None
                r = float(ratio)
                try:
                    full_thr = float((os.getenv("HPMP_FULL_BAR_THR", "0.98") or "0.98").strip() or "0.98")
                except Exception:
                    full_thr = 0.98
                full_thr = float(max(0.90, min(0.999, full_thr)))
                if r < full_thr:
                    return None

                try:
                    min_max = int(float(os.getenv("HPMP_MIN_OCR_MAX", "50").strip() or "50"))
                except Exception:
                    min_max = 50
                min_max = int(max(1, min_max))
                if c < min_max:
                    return None
                return int(c)
            except Exception:
                return None

        try:
            if hp_max is None and hp_current is not None and hp_ratio is not None:
                est_full = _infer_max_if_full(hp_current, hp_ratio)
                if est_full is not None:
                    hp_max = int(est_full)
                    try:
                        self._last_hp_max = hp_max
                    except Exception:
                        pass
                    if not hp_method or hp_method in {"top_ocr", "top_strip", "rf_box"}:
                        hp_method = "infer_max_full_bar"
                    hp_reason = f"infer_max_full:{hp_reason}" if hp_reason else "infer_max_full"
        except Exception:
            pass

        try:
            if mp_max is None and mp_current is not None and mp_ratio is not None:
                est_full = _infer_max_if_full(mp_current, mp_ratio)
                if est_full is not None:
                    mp_max = int(est_full)
                    try:
                        self._last_mp_max = mp_max
                    except Exception:
                        pass
                    if not mp_method or mp_method in {"top_ocr", "top_strip", "rf_box"}:
                        mp_method = "infer_max_full_bar"
                    mp_reason = f"infer_max_full:{mp_reason}" if mp_reason else "infer_max_full"
        except Exception:
            pass

        def _infer_max_from_cur_ratio(cur: Optional[int], ratio: Optional[float]) -> Optional[int]:
            try:
                if cur is None or ratio is None:
                    return None
                c = int(cur)
                r = float(ratio)
                if c <= 0:
                    return None
                # Avoid unstable inference near empty/full bars.
                if r < 0.20 or r > 0.90:
                    return None
                if r <= 0.0:
                    return None
                est = int(round(float(c) / float(r)))

                # Sanity bounds
                try:
                    max_value = int(float(os.getenv("HPMP_MAX_OCR", "100000").strip() or "100000"))
                except Exception:
                    max_value = 100000
                max_value = int(max(1000, max_value))
                try:
                    min_max = int(float(os.getenv("HPMP_MIN_OCR_MAX", "50").strip() or "50"))
                except Exception:
                    min_max = 50
                min_max = int(max(1, min_max))

                if est < min_max or est > max_value:
                    return None
                if est < c:
                    return None
                return est
            except Exception:
                return None

        if hp_max is None and hp_current is not None and hp_ratio is not None:
            try:
                est = _infer_max_from_cur_ratio(hp_current, hp_ratio)
                if est is not None:
                    hp_max = int(est)
                    try:
                        self._last_hp_max = hp_max
                    except Exception:
                        pass
                    if not hp_method or hp_method in {"top_ocr", "top_strip", "rf_box"}:
                        hp_method = "infer_max_from_bar"
                    hp_reason = f"infer_max:{hp_reason}" if hp_reason else "infer_max"
            except Exception:
                pass

        if mp_max is None and mp_current is not None and mp_ratio is not None:
            try:
                est = _infer_max_from_cur_ratio(mp_current, mp_ratio)
                if est is not None:
                    mp_max = int(est)
                    try:
                        self._last_mp_max = mp_max
                    except Exception:
                        pass
                    if not mp_method or mp_method in {"top_ocr", "top_strip", "rf_box"}:
                        mp_method = "infer_max_from_bar"
                    mp_reason = f"infer_max:{mp_reason}" if mp_reason else "infer_max"
            except Exception:
                pass

        # Attach HUD debug snapshot (before fusion so we keep raw diagnostics).
        try:
            hud_debug["hp"] = {
                "ocr_method": hp_method,
                "ocr_reason": hp_reason,
                "bar_ratio": hp_ratio,
                "bar_reason": hp_bar_reason,
                "bar_presence": hp_bar_presence,
                "bar_source": chosen_bar_source,
                "bar_low_ratio": hp_low_ratio,
                "bar_low_reason": hp_low_reason,
                "bar_low_presence": hp_low_presence,
                "bar_top_ratio": hp_top_ratio,
                "bar_top_reason": hp_top_reason,
                "bar_top_presence": hp_top_presence,
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
                "bar_presence": mp_bar_presence,
                "bar_source": chosen_bar_source,
                "bar_low_ratio": mp_low_ratio,
                "bar_low_reason": mp_low_reason,
                "bar_low_presence": mp_low_presence,
                "bar_top_ratio": mp_top_ratio,
                "bar_top_reason": mp_top_reason,
                "bar_top_presence": mp_top_presence,
                "bar_roi": list(mp_bar_roi) if isinstance(mp_bar_roi, tuple) else None,
                "bar_roi_reason": mp_bar_roi_reason,
                "max": mp_max,
                "cur": mp_current,
            }
            hud_debug["cap"] = {
                "method": cap_method,
                "reason": cap_reason,
                "cur": cap_current,
                "roi": (dbg.get("roi") if isinstance(dbg, dict) else None),
                "panel": (dbg.get("panel") if isinstance(dbg, dict) else None),
                "panel_source": (dbg.get("panel_source") if isinstance(dbg, dict) else None),
            }

            dbg_soul = None
            try:
                dbg_soul = getattr(self.ocr_processor, "last_soul_debug", None)
            except Exception:
                dbg_soul = None
            hud_debug["soul"] = {
                "method": soul_method,
                "reason": soul_reason,
                "cur": soul_current,
                "roi": (dbg_soul.get("roi") if isinstance(dbg_soul, dict) else None),
            }
        except Exception:
            pass

        # Fusion OCR vs barras: conservador.
        # - If OCR is confident, keep OCR (bars can drift with ROI alignment).
        # - If OCR is not confident and bars have strong presence, allow bar override.
        try:
            diff_thr = float(os.getenv("HPMP_BAR_FUSION_DIFF", "0.10").strip() or "0.10")
        except Exception:
            diff_thr = 0.10

        try:
            if hp_current is not None and hp_max and hp_ratio is not None:
                ocr_ratio = float(hp_current) / float(hp_max)
                diff = abs(float(ocr_ratio) - float(hp_ratio))
                if diff >= float(diff_thr):
                    if _should_override_ocr_with_bar(
                        ocr_reason=str(hp_reason or ""),
                        bar_presence=float(hp_bar_presence or 0.0),
                        diff=float(diff),
                    ):
                        hp_current = int(round(float(hp_ratio) * float(hp_max)))
                        hp_method = "bar_low_fusion"
                        hp_reason = "ratio_mismatch_bar_override"
                    else:
                        hp_reason = f"{hp_reason}|ratio_mismatch_keep_ocr" if hp_reason else "ratio_mismatch_keep_ocr"
        except Exception:
            pass

        try:
            if mp_current is not None and mp_max and mp_ratio is not None:
                ocr_ratio = float(mp_current) / float(mp_max)
                diff = abs(float(ocr_ratio) - float(mp_ratio))
                if diff >= float(diff_thr):
                    if _should_override_ocr_with_bar(
                        ocr_reason=str(mp_reason or ""),
                        bar_presence=float(mp_bar_presence or 0.0),
                        diff=float(diff),
                    ):
                        mp_current = int(round(float(mp_ratio) * float(mp_max)))
                        mp_method = "bar_low_fusion"
                        mp_reason = "ratio_mismatch_bar_override"
                    else:
                        mp_reason = f"{mp_reason}|ratio_mismatch_keep_ocr" if mp_reason else "ratio_mismatch_keep_ocr"
        except Exception:
            pass

        if hp_current is None and hp_max is not None and hp_ratio is not None:
            hp_current = int(round(hp_ratio * hp_max))
            hp_method = "bar_low"
            hp_reason = f"fallback:{hp_reason}" if hp_reason else "fallback_no_ocr"
        elif hp_current is None and hp_ratio is not None and hp_max is None:
            # We can still estimate HP% from the bar even if max isn't known.
            hp_method = "bar_low_pct"
            hp_reason = "pct_only"
        elif hp_current is None and not hp_method:
            hp_method = "none"
            hp_reason = "no_ocr_no_bar"

        # Percent outputs (may be filled from OCR/bar/inference).
        hp_pct: Optional[float] = None
        mp_pct: Optional[float] = None

        # If OCR returned a single number (no max) and it's in 0..100, treat it
        # as a percentage rather than an absolute "current". This matches real
        # HUDs that show percents or OCR misreads that drop the slash.
        try:
            if hp_max is None and hp_current is not None:
                r = str(hp_reason or "")
                if "single_number" in r and 0 <= int(hp_current) <= 100 and hp_ratio is None:
                    hp_pct = float(int(hp_current))
                    hp_current = None
                    hp_method = "pct_ocr_single"
                    hp_reason = "single_number_pct"
        except Exception:
            pass

        if mp_current is None and mp_max is not None and mp_ratio is not None:
            mp_current = int(round(mp_ratio * mp_max))
            mp_method = "bar_low"
            mp_reason = f"fallback:{mp_reason}" if mp_reason else "fallback_no_ocr"
        elif mp_current is None and mp_ratio is not None:
            # We can still estimate MP% from the bar even if max isn't known.
            mp_method = "bar_low_pct"
            mp_reason = "pct_only"
        elif mp_current is None and not mp_method:
            mp_method = "none"
            mp_reason = "no_ocr_no_bar"

        try:
            if mp_max is None and mp_current is not None:
                r = str(mp_reason or "")
                if "single_number" in r and 0 <= int(mp_current) <= 100 and mp_ratio is None:
                    mp_pct = float(int(mp_current))
                    mp_current = None
                    mp_method = "pct_ocr_single"
                    mp_reason = "single_number_pct"
        except Exception:
            pass

        # Crear nuevo estado
        try:
            if hp_current is not None and hp_max:
                hp_pct = (float(hp_current) / float(hp_max)) * 100.0
            if mp_current is not None and mp_max:
                mp_pct = (float(mp_current) / float(mp_max)) * 100.0
        except Exception:
            hp_pct = None
            mp_pct = None

        # If we don't have cur/max but we do have a bar ratio, still expose %.
        try:
            if hp_pct is None and hp_ratio is not None:
                hp_pct = float(hp_ratio) * 100.0
        except Exception:
            pass
        try:
            if mp_pct is None and mp_ratio is not None:
                mp_pct = float(mp_ratio) * 100.0
        except Exception:
            pass

        gamestate = GameState(
            hp_current=hp_current,
            hp_max=hp_max,
            mp_current=mp_current,
            mp_max=mp_max,
            cap_current=cap_current,
            soul_current=soul_current,
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
            soul_method=soul_method,
            soul_reason=soul_reason,
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
            presence_dbg: Dict[str, Any] = {}
            now_p = time.time()
            try:
                if isinstance(gamestate.hud_debug, dict):
                    presence_dbg["rois_px"] = LayoutTracker.apply_layout(
                        frame_shape=(int(frame.shape[0]), int(frame.shape[1])),
                        rois=rois,
                        resolution=resolution,
                        names=[
                            "equipment_slots",
                            "ring_slot",
                            "amulet_slot",
                            "hungry_icon",
                            "states_icons",
                        ],
                    )
            except Exception:
                pass

            # Default: unknown until we can detect.
            gamestate.paralyzed = None
            gamestate.haste_active = None
            gamestate.utamo_active = None

            # These are *presence* flags. Use None for "unknown" when we can't
            # reliably detect (e.g., ROI missing) to avoid false "N" reports.
            gamestate.ring_equipped = None
            gamestate.amulet_equipped = None
            gamestate.hungry = None

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

                        try:
                            r_border = float(os.getenv("RING_ICON_BORDER_FRAC", "0.22"))
                        except Exception:
                            r_border = 0.22

                        detected, _conf = detect_equipment_slot(
                            frame[ry : ry + rh, rx : rx + rw],
                            kind="ring",
                            min_std=r_std,
                            min_mean=r_mean,
                            min_sat_pct=r_min_sat,
                            sat_thr=r_sat_thr,
                            v_thr=r_v_thr,
                            high_std=r_high_std,
                            border_frac=r_border,
                        )

                        # Require strong evidence for "present" to avoid empty-slot false positives.
                        try:
                            min_conf = float(os.getenv("RING_ICON_MIN_CONF", "0.80"))
                        except Exception:
                            min_conf = 0.80
                        try:
                            if detected is True and float(_conf) < float(min_conf):
                                detected = None
                        except Exception:
                            pass

                        try:
                            presence_dbg["ring"] = {
                                "roi": [int(rx), int(ry), int(rw), int(rh)],
                                "detected_raw": (None if detected is None else bool(detected)),
                                "conf": (None if _conf is None else float(_conf)),
                                "min_conf": float(min_conf),
                                "min_std": float(r_std),
                                "min_mean": float(r_mean),
                                "min_sat_pct": float(r_min_sat),
                                "sat_thr": int(r_sat_thr),
                                "v_thr": int(r_v_thr),
                                "high_std": float(r_high_std),
                                "border_frac": float(r_border),
                            }
                        except Exception:
                            pass

                        # Debounce to reduce flicker / rare false positives.
                        key = "ring_equipped"
                        try:
                            deb = self._presence_debouncers.get(key)
                            if deb is not None:
                                gamestate.ring_equipped = deb.update(detected, now_ts=float(now_p))
                        except Exception:
                            pass

                        try:
                            if "ring" in presence_dbg:
                                presence_dbg["ring"]["state"] = gamestate.ring_equipped
                                try:
                                    deb = self._presence_debouncers.get(key)
                                    if deb is not None:
                                        presence_dbg["ring"]["stable_state"] = deb.stable
                                        presence_dbg["ring"]["on_streak"] = int(deb.on_streak)
                                        presence_dbg["ring"]["off_streak"] = int(deb.off_streak)
                                        presence_dbg["ring"]["max_hold_ms"] = int(deb.max_hold_ms)
                                except Exception:
                                    pass
                        except Exception:
                            pass
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

                        try:
                            a_border = float(os.getenv("AMULET_ICON_BORDER_FRAC", "0.22"))
                        except Exception:
                            a_border = 0.22

                        detected, _conf = detect_equipment_slot(
                            frame[ay : ay + ah, ax : ax + aw],
                            kind="amulet",
                            min_std=a_std,
                            min_mean=a_mean,
                            min_sat_pct=a_min_sat,
                            sat_thr=a_sat_thr,
                            v_thr=a_v_thr,
                            high_std=a_high_std,
                            border_frac=a_border,
                        )

                        # Require strong evidence for "present" to avoid empty-slot false positives.
                        try:
                            min_conf = float(os.getenv("AMULET_ICON_MIN_CONF", "0.80"))
                        except Exception:
                            min_conf = 0.80
                        try:
                            if detected is True and float(_conf) < float(min_conf):
                                detected = None
                        except Exception:
                            pass

                        try:
                            presence_dbg["amulet"] = {
                                "roi": [int(ax), int(ay), int(aw), int(ah)],
                                "detected_raw": (None if detected is None else bool(detected)),
                                "conf": (None if _conf is None else float(_conf)),
                                "min_conf": float(min_conf),
                                "min_std": float(a_std),
                                "min_mean": float(a_mean),
                                "min_sat_pct": float(a_min_sat),
                                "sat_thr": int(a_sat_thr),
                                "v_thr": int(a_v_thr),
                                "high_std": float(a_high_std),
                                "border_frac": float(a_border),
                            }
                        except Exception:
                            pass

                        key = "amulet_equipped"
                        try:
                            deb = self._presence_debouncers.get(key)
                            if deb is not None:
                                gamestate.amulet_equipped = deb.update(detected, now_ts=float(now_p))
                        except Exception:
                            pass

                        try:
                            if "amulet" in presence_dbg:
                                presence_dbg["amulet"]["state"] = gamestate.amulet_equipped
                                try:
                                    deb = self._presence_debouncers.get(key)
                                    if deb is not None:
                                        presence_dbg["amulet"]["stable_state"] = deb.stable
                                        presence_dbg["amulet"]["on_streak"] = int(deb.on_streak)
                                        presence_dbg["amulet"]["off_streak"] = int(deb.off_streak)
                                        presence_dbg["amulet"]["max_hold_ms"] = int(deb.max_hold_ms)
                                except Exception:
                                    pass
                        except Exception:
                            pass

                # Hunger icon: prefer tight ROI if available; else fall back to HSV heuristic on states_icons.
                if rois.get("hungry_icon") is not None:
                    hx, hy, hw, hh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["hungry_icon"])
                    if hw >= 6 and hh >= 6:
                        # Use color-band detection to avoid false positives from slot texture.
                        try:
                            min_pct = float(os.getenv("HUNGRY_MIN_PCT", "0.030"))
                        except Exception:
                            min_pct = 0.030
                        try:
                            min_comp = float(os.getenv("HUNGRY_MIN_COMPONENT_PCT", "0.010"))
                        except Exception:
                            min_comp = 0.010
                        try:
                            border_frac = float(os.getenv("HUNGRY_BORDER_FRAC", "0.18"))
                        except Exception:
                            border_frac = 0.18
                        try:
                            low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
                        except Exception:
                            low_h = 8
                        try:
                            high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
                        except Exception:
                            high_h = 35
                        detected = is_hungry_hsv(
                            frame[hy : hy + hh, hx : hx + hw],
                            min_pct=min_pct,
                            min_component_pct=min_comp,
                            strip_border_frac=border_frac,
                            low_h=low_h,
                            high_h=high_h,
                        )

                        try:
                            presence_dbg["hungry"] = {
                                "roi": [int(hx), int(hy), int(hw), int(hh)],
                                "detected_raw": (None if detected is None else bool(detected)),
                                "min_pct": float(min_pct),
                                "min_component_pct": float(min_comp),
                                "border_frac": float(border_frac),
                                "low_h": int(low_h),
                                "high_h": int(high_h),
                                "source": "hungry_icon",
                            }
                        except Exception:
                            pass

                        key = "hungry"
                        try:
                            deb = self._presence_debouncers.get(key)
                            if deb is not None:
                                gamestate.hungry = deb.update(detected, now_ts=float(now_p))
                        except Exception:
                            pass

                        try:
                            if "hungry" in presence_dbg:
                                presence_dbg["hungry"]["state"] = gamestate.hungry
                                try:
                                    deb = self._presence_debouncers.get(key)
                                    if deb is not None:
                                        presence_dbg["hungry"]["stable_state"] = deb.stable
                                        presence_dbg["hungry"]["on_streak"] = int(deb.on_streak)
                                        presence_dbg["hungry"]["off_streak"] = int(deb.off_streak)
                                        presence_dbg["hungry"]["max_hold_ms"] = int(deb.max_hold_ms)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                elif rois.get("states_icons") is not None:
                    sx, sy, sw, sh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["states_icons"])
                    crop = frame[sy : sy + sh, sx : sx + sw]

                    # Status icons (template matching, optional templates).
                    try:
                        confs = detect_status_icons(crop)
                    except Exception:
                        confs = {}

                    if confs:
                        try:
                            presence_dbg["status_icons"] = {
                                "roi": [int(sx), int(sy), int(sw), int(sh)],
                                "confs": dict(confs),
                                "thr": float(getattr(self, "_status_match_thr", 0.65) or 0.65),
                                "k": int(getattr(self, "_status_persist_k", 3) or 3),
                            }
                        except Exception:
                            pass
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

                    # Tunables via env vars (separate defaults for broader ROI).
                    try:
                        min_pct = float(os.getenv("HUNGRY_STATES_MIN_PCT", os.getenv("HUNGRY_MIN_PCT", "0.020")))
                    except Exception:
                        min_pct = 0.020
                    try:
                            min_comp = float(os.getenv("HUNGRY_STATES_MIN_COMPONENT_PCT", os.getenv("HUNGRY_MIN_COMPONENT_PCT", "0.010")))
                    except Exception:
                            min_comp = 0.010
                    try:
                        border_frac = float(os.getenv("HUNGRY_BORDER_FRAC", "0.10"))
                    except Exception:
                        border_frac = 0.10
                    low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
                    high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
                    detected = is_hungry_hsv(
                        crop,
                        min_pct=min_pct,
                        min_component_pct=min_comp,
                        strip_border_frac=border_frac,
                        low_h=low_h,
                        high_h=high_h,
                    )
                    key = "hungry"
                    try:
                        deb = self._presence_debouncers.get(key)
                        if deb is not None:
                            gamestate.hungry = deb.update(detected, now_ts=float(now_p))
                        else:
                            gamestate.hungry = detected
                    except Exception:
                        gamestate.hungry = detected

                    try:
                        # If hungry_icon ROI is missing, this is the fallback source.
                        presence_dbg.setdefault("hungry", {})
                        if isinstance(presence_dbg.get("hungry"), dict):
                            presence_dbg["hungry"].update(
                                {
                                    "roi": [int(sx), int(sy), int(sw), int(sh)],
                                    "detected_raw": (None if detected is None else bool(detected)),
                                    "min_pct": float(min_pct),
                                    "min_component_pct": float(min_comp),
                                    "border_frac": float(border_frac),
                                    "low_h": int(low_h),
                                    "high_h": int(high_h),
                                    "source": "states_icons",
                                }
                            )
                    except Exception:
                        pass

                    try:
                        if isinstance(presence_dbg.get("hungry"), dict):
                            presence_dbg["hungry"]["state"] = gamestate.hungry
                            try:
                                deb = self._presence_debouncers.get(key)
                                if deb is not None:
                                    presence_dbg["hungry"]["stable_state"] = deb.stable
                                    presence_dbg["hungry"]["on_streak"] = int(deb.on_streak)
                                    presence_dbg["hungry"]["off_streak"] = int(deb.off_streak)
                                    presence_dbg["hungry"]["max_hold_ms"] = int(deb.max_hold_ms)
                            except Exception:
                                pass
                    except Exception:
                        pass

            # Attach presence diagnostics to HUD debug.
            try:
                if isinstance(gamestate.hud_debug, dict) and isinstance(presence_dbg, dict) and presence_dbg:
                    gamestate.hud_debug["presence"] = presence_dbg
            except Exception:
                pass
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

            # If throttled, reuse cached output (latest-wins, avoids vision stalls).
            if not bool(do_battlelist):
                try:
                    cache = dict(getattr(self, "_battlelist_cache", {}) or {})
                    gamestate.battlelist_entries = list(cache.get("entries", []) or [])
                    gamestate.battlelist_n_rows = int(cache.get("n_rows", 0) or 0)
                    gamestate.battlelist_n_valid = int(cache.get("n_valid", 0) or 0)
                    gamestate.battlelist_top_names = list(cache.get("top_names", []) or [])
                    gamestate.battlelist_confidence = float(cache.get("confidence", 0.0) or 0.0)
                    gamestate.battlelist_source = str(cache.get("source", "cache") or "cache")
                    gamestate.battlelist_reason = "throttled"
                    if isinstance(gamestate.hud_debug, dict):
                        gamestate.hud_debug["battlelist"] = {
                            "source": gamestate.battlelist_source,
                            "reason": gamestate.battlelist_reason,
                            "roi": cache.get("roi"),
                            "n_rows": gamestate.battlelist_n_rows,
                            "n_valid": gamestate.battlelist_n_valid,
                            "confidence": float(gamestate.battlelist_confidence or 0.0),
                        }
                except Exception:
                    pass

            if do_battlelist and isinstance(rois, dict) and rois.get("battlelist_rows") is not None:
                # Mark start time early so long OCR work doesn't immediately retrigger.
                try:
                    self._battlelist_last_ts = float(now)
                except Exception:
                    pass

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
                    try:
                        if self._battlelist_parse_max_rows > 0:
                            rows = list(rows[: int(self._battlelist_parse_max_rows)])
                    except Exception:
                        pass
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

                    # Cache for throttled frames.
                    try:
                        self._battlelist_cache = {
                            "entries": stable,
                            "n_rows": int(n_rows),
                            "n_valid": int(n_valid),
                            "top_names": list(top_names[:10]),
                            "confidence": float(global_conf),
                            "source": str(gamestate.battlelist_source or ""),
                            "reason": str(gamestate.battlelist_reason or ""),
                            "roi": [int(bx), int(by), int(bw), int(bh)],
                        }
                    except Exception:
                        pass

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
            # Keep previous cache on any failure.
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

        # Update guard state based on observed duration.
        try:
            dt_ms = float((time.time() - float(t0)) * 1000.0)
            self._vision_last_update_ms = dt_ms
            if bool(self._vision_guard_enabled) and (not bool(guard_force)):
                if dt_ms >= float(self._vision_guard_slow_ms):
                    self._vision_guard_until_ts = float(time.time()) + float(self._vision_guard_cooldown_s)
        except Exception:
            pass

        return gamestate