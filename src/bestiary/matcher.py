from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Any
import json

from rapidfuzz.process import extractOne
from rapidfuzz.fuzz import WRatio, token_set_ratio


class BestiaryMatcher:
    """
    Soporta 2 formatos de registry:
      A) dict: { "orc": {...}, "dragon": {...} }
      B) list: [ {"name_key": "orc", ...}, {"name_key": "dragon", ...} ]
    Devuelve (nombre_canonico, score).
    """

    def __init__(
        self,
        registry_path: str = "data/creatures_registry.json",
        corrections_path: str = "configs/ocr_corrections.json",
    ):
        with open(registry_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        self.registry_raw: Any = raw
        self.corrections: Dict[str, str] = self._load_corrections(corrections_path)

        # Normalizamos a una lista de nombres "name_key"
        if isinstance(raw, dict):
            self.names: List[str] = list(raw.keys())
            self.meta_by_name: Dict[str, Any] = raw
        elif isinstance(raw, list):
            self.names = [c.get("name_key", "").strip() for c in raw if isinstance(c, dict)]
            self.names = [n for n in self.names if n]
            self.meta_by_name = {c["name_key"]: c for c in raw if isinstance(c, dict) and "name_key" in c}
        else:
            raise TypeError(f"Formato no soportado en registry: {type(raw)}")

        self.buckets: Dict[str, List[str]] = self._index(self.names)

    @staticmethod
    def _load_corrections(path: str) -> Dict[str, str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}

    def _index(self, names: List[str]) -> Dict[str, List[str]]:
        """
        Bucket por (primera_letra + rango_de_longitud) para reducir candidatos.
        """
        buckets: Dict[str, List[str]] = {}
        for name in names:
            key = self._bucket_key(name)
            buckets.setdefault(key, []).append(name)
        return buckets

    @staticmethod
    def _bucket_key(text: str) -> str:
        t = text.strip().casefold()
        if not t:
            return "_0-0"
        first = t[0]
        ln = len(t)
        return f"{first}_{ln-3}-{ln+3}"

    def normalize_ocr(self, text: str) -> str:
        t = (text or "").casefold().strip()
        t = t.replace("'", "").replace("-", " ")

        for wrong, right in self.corrections.items():
            if wrong:
                t = t.replace(wrong.casefold(), right.casefold())

        # Heurísticas típicas OCR
        if len(t) > 3:
            t = t.replace("0", "o").replace("1", "i")

        # Espacios múltiples
        t = " ".join(t.split())
        return t

    def get_candidates(self, norm: str) -> List[str]:
        if not norm:
            return []
        key = self._bucket_key(norm)
        cands = self.buckets.get(key)
        if cands:
            return cands
        # fallback: si no hay bucket exacto, usa todo
        return self.names

    def match(self, ocr_text: str) -> Tuple[Optional[str], float]:
        norm = self.normalize_ocr(ocr_text)
        if not norm:
            return None, 0.0

        candidates = self.get_candidates(norm)
        if not candidates:
            return None, 0.0

        best = extractOne(norm, candidates, scorer=WRatio)
        if not best:
            return None, 0.0

        best_name, best_score, _ = best

        if best_score >= 92:
            return best_name, float(best_score)

        if 85 <= best_score < 92:
            top2 = extractOne(norm, candidates, scorer=token_set_ratio, score_cutoff=85)
            if top2:
                name2, score2, _ = top2
                if abs(best_score - score2) > 5:
                    return best_name, float(best_score)

        return None, 0.0
