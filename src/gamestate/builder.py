# src/gamestate/builder.py - Versión completa corregida (copia y reemplaza el archivo)

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
import numpy as np
import time
from .models import GameState, Entity
from ..bestiary.matcher import BestiaryMatcher

@dataclass
class GameStateBuilder:
    history: List[Dict[str, Any]] = field(default_factory=list)
    matcher: BestiaryMatcher = field(default_factory=BestiaryMatcher)
    max_history: int = 10
    ema_alpha: float = 0.3
    ema_hp: Optional[float] = None
    ema_mp: Optional[float] = None

    def build(self, detections: Dict[str, Any]) -> GameState:
        self.history.append(detections)
        if len(self.history) > self.max_history:
            self.history.pop(0)

        hp_current = 0
        mp_current = 0
        max_hp = 100

        hp_ocr_tuple = detections.get('hp_top')
        if hp_ocr_tuple and len(hp_ocr_tuple) == 2:
            cur_ocr, max_ocr = hp_ocr_tuple
            if 0 < cur_ocr <= max_ocr:
                hp_current = int(cur_ocr)
                max_hp = int(max_ocr)

        # Fallback low bar si top falla (positional default, no keyword después)
        hp_bar_ratio = detections.get('hp_bar_ratio', 0.0)
        if hp_current == 0:
            hp_current = int(hp_bar_ratio * max_hp)

        # MP similar (positional default)
        mp_ocr = detections.get('mp_top', (0, 100))[0]
        if mp_ocr > 0:
            mp_current = int(mp_ocr)
        else:
            mp_bar_ratio = detections.get('mp_bar_ratio', 0.0)
            mp_current = int(mp_bar_ratio * 100)

        # Smoothing temporal (median de history)
        hp_values = []
        for d in self.history:
            ocr = d.get('hp_top')
            if ocr and len(ocr) == 2 and 0 < ocr[0] <= ocr[1]:
                hp_values.append(int(ocr[0]))
            else:
                bar = d.get('hp_bar_ratio', 0.0)
                hp_values.append(int(bar * max_hp))

        if hp_values:
            median_hp = int(np.median(hp_values))
            hp_current = median_hp

        # EMA para suavizado
        if self.ema_hp is None:
            self.ema_hp = float(hp_current)
        else:
            self.ema_hp = self.ema_alpha * hp_current + (1 - self.ema_alpha) * self.ema_hp
        hp_current = int(round(self.ema_hp))

        # Entidades battlelist
        entities: List[Entity] = []
        battlelist = detections.get('battlelist', [])
        for row in battlelist:
            raw_text = row.get('ocr_text', '')
            matched_name = self.matcher.match(raw_text)
            if matched_name:
                entities.append(Entity(
                    name=matched_name,
                    hp_pct=float(row.get('hp_pct', 0.0)),
                    position=(0, 0)
                ))

        states = detections.get('states', [])
        equipment: Dict[str, str] = {}

        position = (0, 0)

        return GameState(
            hp=hp_current,
            mp=mp_current,
            position=position,
            entities=entities,
            states=states,
            equipment=equipment,
            timestamp=time.time()
        )