from typing import Optional, Tuple, List
import rapidfuzz
from rapidfuzz.process import extractOne
from rapidfuzz.fuzz import WRatio, token_set_ratio
import json

class BestiaryMatcher:
    def __init__(self):
        with open('data/creatures_registry.json', 'r') as f:
            self.registry: List[dict] = json.load(f)
        self.buckets: dict[str, List[dict]] = self.index()
        self.corrections = self.load_corrections()

    def load_corrections(self) -> dict:
        with open('data/ocr_corrections.json', 'r') as f:
            return json.load(f)

    def index(self) -> dict:
        buckets = {}
        for c in self.registry:
            first = c['name_key'][0]
            len_b = len(c['name_key'])
            key = f"{first}_{len_b-3}-{len_b+3}"
            buckets.setdefault(key, []).append(c)
        return buckets

    def normalize_ocr(self, text: str) -> str:
        text = text.casefold().strip().replace("'", "").replace("-", " ")
        for wrong, right in self.corrections.items():
            text = text.replace(wrong, right)
        text = text.replace('0', 'o').replace('1', 'i') if len(text) > 3 else text
        return text

    def match(self, ocr_text: str) -> Tuple[Optional[str], float]:
        norm = self.normalize_ocr(ocr_text)
        candidates = self.get_candidates(norm)
        best = extractOne(norm, [c['name_key'] for c in candidates], scorer=WRatio)
        if best[1] >= 92:
            return best[0], best[1]
        elif 85 <= best[1] < 92:
            top2 = extractOne(norm, [c['name_key'] for c in candidates], scorer=token_set_ratio, score_cutoff=85)
            if abs(best[1] - top2[1]) > 5:
                return best[0], best[1]
        return None, 0

    def get_candidates(self, norm: str) -> List[dict]:
        first = norm[0]
        len_n = len(norm)
        key = f"{first}_{len_n-3}-{len_n+3}"
        return self.buckets.get(key, [])