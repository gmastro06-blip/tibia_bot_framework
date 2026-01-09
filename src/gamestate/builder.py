from typing import Optional, Dict, Any, Tuple, List
import os
import time
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor
from vision.roboflow_inference import RoboflowInference
from vision.bar_analysis import estimate_bar_fill_ratio
from vision.obstacles import compute_viewport_tile_offsets
from vision.presence import is_hungry_hsv, is_nonempty_icon

@dataclass
class GameState:
    """Estado actual del juego"""
    hp_current: Optional[int] = None
    hp_max: Optional[int] = None
    mp_current: Optional[int] = None
    mp_max: Optional[int] = None
    cap_current: Optional[int] = None
    ring_equipped: Optional[bool] = None
    amulet_equipped: Optional[bool] = None
    hungry: Optional[bool] = None
    # Señales derivadas (útiles para thresholds/decisiones, aunque no haya OCR perfecto)
    hp_pct: Optional[float] = None
    mp_pct: Optional[float] = None
    roboflow_boxes: Optional[List[Dict[str, Any]]] = None
    viewport_tile_offsets: Optional[List[Tuple[int, int]]] = None

    def __str__(self) -> str:
        rf_n = len(self.roboflow_boxes) if self.roboflow_boxes else 0
        return (
            f"HP: {self.hp_current}/{self.hp_max}, MP: {self.mp_current}/{self.mp_max}, "
            f"Cap: {self.cap_current}, ring: {self.ring_equipped}, amulet: {self.amulet_equipped}, hungry: {self.hungry}, RF: {rf_n}"
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

    def update_from_frame(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> GameState:
        """Actualiza el estado del juego desde un frame"""
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

        do_ocr = (now - self._ocr_last_ts) >= self._ocr_min_interval_s
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
                cap_current = self.ocr_processor.extract_capacity(frame, rois, resolution)
            except Exception:
                cap_current = None

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
        if rf_hpmp_boxes and self._rf_hpmp is not None:
            hp_bar_classes = os.getenv("ROBOFLOW_HP_BAR_CLASSES", "hp_bar,hp_low_bar,health_bar").split(",")
            mp_bar_classes = os.getenv("ROBOFLOW_MP_BAR_CLASSES", "mp_bar,mp_low_bar,mana_bar").split(",")
            hp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in hp_bar_classes])
            mp_box = self._rf_hpmp.best_box_by_class(rf_hpmp_boxes, [c.strip() for c in mp_bar_classes])
            if hp_box:
                crop = self._rf_hpmp.crop_from_box(frame, hp_box)
                if crop is not None:
                    hp_ratio = estimate_bar_fill_ratio(crop, (0, 0, crop.shape[1], crop.shape[0]), "hp")
            if mp_box:
                crop = self._rf_hpmp.crop_from_box(frame, mp_box)
                if crop is not None:
                    mp_ratio = estimate_bar_fill_ratio(crop, (0, 0, crop.shape[1], crop.shape[0]), "mp")

        if hp_ratio is None and "hp_low_bar" in rois:
            # Usar el mismo normalizador del OCR (ya soporta px/norm + letterboxing)
            hp_roi = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["hp_low_bar"])
            hp_ratio = estimate_bar_fill_ratio(frame, hp_roi, "hp")
        if mp_ratio is None and "mp_low_bar" in rois:
            mp_roi = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["mp_low_bar"])
            mp_ratio = estimate_bar_fill_ratio(frame, mp_roi, "mp")

        if hp_current is None and hp_max is not None and hp_ratio is not None:
            hp_current = int(round(hp_ratio * hp_max))
        if mp_current is None and mp_max is not None and mp_ratio is not None:
            mp_current = int(round(mp_ratio * mp_max))

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
            hp_pct=hp_pct,
            mp_pct=mp_pct,
            roboflow_boxes=rf_boxes,
            viewport_tile_offsets=None,
        )

        # (C) Equipment + status icons (best-effort, depends on calibrated ROIs)
        try:
            # Prefer dedicated ROIs if present.
            if isinstance(rois, dict):
                if rois.get("ring_slot") is not None:
                    rx, ry, rw, rh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["ring_slot"])
                    gamestate.ring_equipped = bool(is_nonempty_icon(frame[ry : ry + rh, rx : rx + rw]))
                if rois.get("amulet_slot") is not None:
                    ax, ay, aw, ah = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["amulet_slot"])
                    gamestate.amulet_equipped = bool(is_nonempty_icon(frame[ay : ay + ah, ax : ax + aw]))

                # Hunger icon: prefer tight ROI if available; else fall back to HSV heuristic on states_icons.
                if rois.get("hungry_icon") is not None:
                    hx, hy, hw, hh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["hungry_icon"])
                    gamestate.hungry = bool(is_nonempty_icon(frame[hy : hy + hh, hx : hx + hw]))
                elif rois.get("states_icons") is not None:
                    sx, sy, sw, sh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["states_icons"])
                    crop = frame[sy : sy + sh, sx : sx + sw]
                    # Tunables via env vars
                    min_pct = float(os.getenv("HUNGRY_MIN_PCT", "0.012"))
                    low_h = int(float(os.getenv("HUNGRY_H_LOW", "8")))
                    high_h = int(float(os.getenv("HUNGRY_H_HIGH", "35")))
                    gamestate.hungry = bool(is_hungry_hsv(crop, min_pct=min_pct, low_h=low_h, high_h=high_h))
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