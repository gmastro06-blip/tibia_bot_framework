# src/gamestate/models.py - Versión corregida

from dataclasses import dataclass
from typing import List, Dict, Tuple

@dataclass
class Entity:
    name: str
    hp_pct: float  # Ya float
    position: Tuple[int, int] = (0, 0)

@dataclass
class GameState:
    hp: float  # Cambiado a float (barra ratio * max_hp puede ser decimal)
    mp: float
    position: Tuple[int, int]
    entities: List[Entity]
    states: List[str]
    equipment: Dict[str, str]
    timestamp: float