from __future__ import annotations

from typing import Optional, Tuple, List, Dict, Any

import json
from rapidfuzz.process import extractOne
from rapidfuzz.fuzz import WRatio, token_set_ratio


class BestiaryMatcher:
    def __init__(self, registry_path: str = "data/creatures_registry.json", corrections_path: str = "data/ocr_corrections.json"):
        self.registry_entries: List[Dict[str, Any]] = self._load_registry(registry_path)
        self.corrections: Dict[str, str] = self._load_corrections(corrections_path)
        self.buckets: Dict[str, List[Dict[str, Any]]] = self._index(self.registry_entries)

    @staticmethod
    def _load_registry(path: str) -> List[Dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        entries: List[Dict[str, Any]] = []

        # Formato A: lista de objetos [{"name": "...", "name_key": "...", ...}, ...]
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("name_key")
                if not name:
                    continue
                name_key = item.get("name_key") or BestiaryMatcher._normalize_name(name)
                item = dict(item)
                item["name"] = item.get("name") or name
                item["name_key"] = name_key
                entries.append(item)
            return entries

        # Formato B: diccionario {"orc": {"hp": 150}, ...}
        if isinstance(raw, dict):
            for name, meta in raw.items():
                if not isinstance(name, str):
                    continue
                meta = meta if isinstance(meta, dict) else {}
                entries.append(
                    {
                        "name": name,
                        "name_key": BestiaryMatcher._normalize_name(name),
                        **meta,
                    }
                )
            return entries

        raise ValueError(f"Formato de registry no soportado: {type(raw).__name__}")

    @staticmethod
    def _load_corrections(path: str) -> Dict[str, str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return raw if isinstance(raw, dict) else {}
        except FileNotFoundError:
            return {}

    @staticmethod
    def _normalize_name(text: str) -> str:
        return text.casefold().strip().replace("'", "").replace("-", " ")

    def _index(self, entries: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for c in entries:
            name_key = c.get("name_key", "")
            if not isinstance(name_key, str) or len(name_key) < 1:
                continue
            first = name_key[0]
            ln = len(name_key)
            key = f"{first}_{ln-3}-{ln+3}"
            buckets.setdefault(key, []).append(c)
        return buckets

    def normalize_ocr(self, text: str) -> str:
        text = self._normalize_name(text)
        for wrong, right in self.corrections.items():
            text = text.replace(wrong, right)
        if len(text) > 3:
            text = text.replace("0", "o").replace("1", "i")
        return text

    def get_candidates(self, norm: str) -> List[Dict[str, Any]]:
        if not norm:
            return []
        first = norm[0]
        ln = len(norm)
        key = f"{first}_{ln-3}-{ln+3}"
        return self.buckets.get(key, [])

    def match(self, ocr_text: str) -> Tuple[Optional[str], float]:
        norm = self.normalize_ocr(ocr_text)
        cands = self.get_candidates(norm)
        if not cands:
            return None, 0.0

        keys = [c["name_key"] for c in cands if isinstance(c.get("name_key"), str)]
        if not keys:
            return None, 0.0

        best = extractOne(norm, keys, scorer=WRatio)
        if not best:
            return None, 0.0

        best_key, best_score, _ = best

        if best_score >= 92:
            return best_key, float(best_score)

        if 85 <= best_score < 92:
            top2 = extractOne(norm, keys, scorer=token_set_ratio, score_cutoff=85)
            if top2 and abs(best_score - top2[1]) > 5:
                return best_key, float(best_score)

        return None, 0.0
