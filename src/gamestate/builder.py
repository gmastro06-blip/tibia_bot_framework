from typing import Optional, Dict, Any, Tuple, List
import os
import time
import json
from pathlib import Path
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor
from vision.roboflow_inference import RoboflowInference
from vision.bar_analysis import estimate_bar_fill_ratio
from vision.obstacles import compute_viewport_tile_offsets
from vision.presence import is_hungry_hsv, is_nonempty_icon
from vision.minimap_motion import MinimapMotionTracker

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
            f"Cap: {self.cap_current}{pos}, ring: {self.ring_equipped}, amulet: {self.amulet_equipped}, hungry: {self.hungry}, RF: {rf_n}"
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

        # Coords por minimapa (requiere seed, NO hace OCR de coords).
        self._minimap_tracker = MinimapMotionTracker()
        self._minimap_seed: tuple[int, int, int | None] | None = None
        self._minimap_coords: tuple[int, int, int | None] | None = None

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
        """Seed de coords para COORDS_PROVIDER=minimap.

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
    ) -> tuple[int, int, int | None] | None:
        """Infiera coords absolutas trackeando la traslación del minimapa.

        Requisitos:
        - Debe existir la ROI `minimap_content`.
        - Debe haber seed vía COORDS_SEED_X/Y[/Z] o COORDS_SEED_FILE.
        """

        if not (isinstance(rois, dict) and rois.get("minimap_content") is not None):
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
                return None

            self._minimap_seed = seed
            self._minimap_coords = seed

        # Cortar minimapa y actualizar motion tracker.
        try:
            mx, my, mw, mh = self.ocr_processor._roi_to_px(frame, rois, resolution, rois["minimap_content"])
            crop = frame[my : my + mh, mx : mx + mw]
            if crop is None or crop.size == 0:
                return self._minimap_coords
        except Exception:
            return self._minimap_coords

        step = None
        try:
            step = self._minimap_tracker.update(crop)
        except Exception:
            step = None

        if step is None:
            return self._minimap_coords

        dx_tiles, dy_tiles, _resp = step
        if self._minimap_coords is None:
            self._minimap_coords = self._minimap_seed

        if self._minimap_coords is None:
            return None

        x0, y0, z0 = self._minimap_coords
        x1 = int(x0) + int(dx_tiles)
        y1 = int(y0) + int(dy_tiles)
        self._minimap_coords = (x1, y1, z0)
        return self._minimap_coords

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
                return self._coords_from_minimap(frame, rois, resolution), False
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
            pos_x = self._last_pos_x
            pos_y = self._last_pos_y
            pos_z = self._last_pos_z

        # Coords provider: permite deshabilitar OCR coords (o usar fuente externa) cuando no hay coords visibles.
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
            pos_x=pos_x,
            pos_y=pos_y,
            pos_z=pos_z,
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