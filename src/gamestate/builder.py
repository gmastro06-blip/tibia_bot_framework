from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, field
import numpy as np

@dataclass
class GameState:
    hp_cur: int = 0
    hp_max: int = 0
    mp_cur: int = 0
    mp_max: int = 0
    player_pos: Tuple[int, int] = (0, 0)
    battlelist: List[Dict[str, Any]] = field(default_factory=list)
    states: List[str] = field(default_factory=list)
    equipment: Dict[str, str] = field(default_factory=dict)

class GameStateBuilder:
    def __init__(self, history_len: int = 5):
        self.history: List[GameState] = []
        self.history_len = history_len

    def fuse_hp_mp(self, ocr_hp: str, ocr_mp: str, bar_low_hp: float, bar_low_mp: float) -> Tuple[int, int, int, int]:
        cur_hp, max_hp = 0, 100
        cur_mp, max_mp = 0, 100
        if ocr_hp and '/' in ocr_hp:
            cur_hp, max_hp = map(int, ocr_hp.split('/'))
            if cur_hp > max_hp or max_hp <= 0:
                cur_hp, max_hp = int(bar_low_hp * 100), 100
        else:
            cur_hp = int(bar_low_hp * 100)
        if ocr_mp and '/' in ocr_mp:
            cur_mp, max_mp = map(int, ocr_mp.split('/'))
            if cur_mp > max_mp or max_mp <= 0:
                cur_mp, max_mp = int(bar_low_mp * 100), 100
        else:
            cur_mp = int(bar_low_mp * 100)
        if abs(cur_hp / max_hp - bar_low_hp) >= 0.1:
            cur_hp = int(bar_low_hp * max_hp)
        if abs(cur_mp / max_mp - bar_low_mp) >= 0.1:
            cur_mp = int(bar_low_mp * max_mp)
        return cur_hp, max_hp, cur_mp, max_mp

    def smooth(self, new_partial: Dict) -> GameState:
        new_gs = GameState(**new_partial)
        self.history.append(new_gs)
        if len(self.history) > self.history_len:
            self.history.pop(0)
        hp_cur = int(np.median([s.hp_cur for s in self.history]))
        hp_max = int(np.median([s.hp_max for s in self.history]))
        mp_cur = int(np.median([s.mp_cur for s in self.history]))
        mp_max = int(np.median([s.mp_max for s in self.history]))
        player_pos = self.history[-1].player_pos
        battlelist = self.history[-1].battlelist
        states = self.history[-1].states
        equipment = self.history[-1].equipment
        return GameState(hp_cur=hp_cur, hp_max=hp_max, mp_cur=mp_cur, mp_max=mp_max, player_pos=player_pos, battlelist=battlelist, states=states, equipment=equipment)

    def build(self, vision_output: Dict) -> GameState:
        partial = {}
        ocr_hp = vision_output.get('ocr_hp', '')
        ocr_mp = vision_output.get('ocr_mp', '')
        bar_hp = vision_output.get('bar_hp', 0.0)
        bar_mp = vision_output.get('bar_mp', 0.0)
        partial['hp_cur'], partial['hp_max'], partial['mp_cur'], partial['mp_max'] = self.fuse_hp_mp(ocr_hp, ocr_mp, bar_hp, bar_mp)
        partial['player_pos'] = vision_output.get('player_pos', (0, 0))
        partial['battlelist'] = vision_output.get('battlelist', [])
        partial['states'] = vision_output.get('states', [])
        partial['equipment'] = vision_output.get('equipment', {})
        return self.smooth(partial)