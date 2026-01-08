from typing import Optional, Dict, Any, Tuple, List
import os
import time
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor
from vision.roboflow_inference import RoboflowInference
from vision.bar_analysis import estimate_bar_fill_ratio

@dataclass
class GameState:
    """Estado actual del juego"""
    hp_current: Optional[int] = None
    hp_max: Optional[int] = None
    mp_current: Optional[int] = None
    mp_max: Optional[int] = None
    roboflow_boxes: Optional[List[Dict[str, Any]]] = None

    def __str__(self) -> str:
        rf_n = len(self.roboflow_boxes) if self.roboflow_boxes else 0
        return f"HP: {self.hp_current}/{self.hp_max}, MP: {self.mp_current}/{self.mp_max}, RF: {rf_n}"

class GameStateBuilder:
    def __init__(self):
        self.ocr_processor = OCRProcessor()
        self.frame_history = []  # Para suavizado de valores
        self._rf = RoboflowInference.from_env()
        self._rf_hpmp = RoboflowInference.from_env_hpmp()  # Separate for HP/MP
        self._rf_last_ts = 0.0
        self._rf_min_interval_s = float(os.getenv("ROBOFLOW_MIN_INTERVAL_S", "0.5"))

    def update_from_frame(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> GameState:
        """Actualiza el estado del juego desde un frame"""
        rf_boxes: Optional[List[Dict[str, Any]]] = None
        rf_hpmp_boxes: Optional[List[Dict[str, Any]]] = None
        if self._rf is not None:
            now = time.time()
            if now - self._rf_last_ts >= self._rf_min_interval_s:
                try:
                    pred = self._rf.predict(frame)
                    rf_boxes = self._rf.extract_boxes(pred)
                except Exception:
                    rf_boxes = None
                self._rf_last_ts = now

        # Separate inference for HP/MP
        if self._rf_hpmp is not None:
            try:
                pred_hpmp = self._rf_hpmp.predict(frame)
                rf_hpmp_boxes = self._rf_hpmp.extract_boxes(pred_hpmp)
            except Exception:
                rf_hpmp_boxes = None

        # (A) Extraer HP/MP usando OCR (y si hay Roboflow boxes, usarlas como override)
        hp_current, hp_max, mp_current, mp_max = self.ocr_processor.extract_hp_mp_full(
            frame,
            rois,
            resolution,
            rf_boxes=rf_boxes,
        )

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
        gamestate = GameState(
            hp_current=hp_current,
            hp_max=hp_max,
            mp_current=mp_current,
            mp_max=mp_max,
            roboflow_boxes=rf_boxes,
        )

        # Aquí podríamos implementar lógica adicional para determinar hp_max/mp_max
        # Por ahora, los dejamos como None o podríamos estimarlos

        return gamestate