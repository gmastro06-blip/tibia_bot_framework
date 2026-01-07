from typing import Optional, Dict, Any, Tuple
import numpy as np
from dataclasses import dataclass
from vision.ocr import OCRProcessor

@dataclass
class GameState:
    """Estado actual del juego"""
    hp_current: Optional[int] = None
    hp_max: Optional[int] = None
    mp_current: Optional[int] = None
    mp_max: Optional[int] = None

    def __str__(self) -> str:
        return f"HP: {self.hp_current}/{self.hp_max}, MP: {self.mp_current}/{self.mp_max}"

class GameStateBuilder:
    def __init__(self):
        self.ocr_processor = OCRProcessor()
        self.frame_history = []  # Para suavizado de valores

    def update_from_frame(self, frame: np.ndarray, rois: Dict[str, Dict[str, float]], resolution: Tuple[int, int]) -> GameState:
        """Actualiza el estado del juego desde un frame"""
        # Extraer HP/MP usando OCR
        hp_current, mp_current = self.ocr_processor.extract_hp_mp(frame, rois, resolution)

        # Crear nuevo estado
        gamestate = GameState(
            hp_current=hp_current,
            mp_current=mp_current
        )

        # Aquí podríamos implementar lógica adicional para determinar hp_max/mp_max
        # Por ahora, los dejamos como None o podríamos estimarlos

        return gamestate