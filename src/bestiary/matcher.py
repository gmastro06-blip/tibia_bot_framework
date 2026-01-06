# src/bestiary/matcher.py - Versión completa corregida con anotación para buckets

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from typing import Optional, Dict, List, Tuple, Any
import json
from rapidfuzz import fuzz

class BestiaryMatcher:
    def __init__(self, registry_path: str = "data/creatures_registry.json"):
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), registry_path)
        self.registry: Dict[str, Dict[str, Any]] = {}
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                self.registry = json.load(f)
            print(f"Bestiario cargado: {len(self.registry)} criaturas.")
        else:
            print(f"Advertencia: Registry no encontrado en {full_path}. Matching desactivado (unknown mobs).")
        
        # Anotación explícita para buckets: clave (letra inicial, longitud ±tol), valor lista de nombres
        self.buckets: Dict[Tuple[str, int], List[str]] = {}
        for key in self.registry:
            l = len(key)
            first = key[0] if key else ""
            for tol in range(-3, 4):
                bucket_key = (first, l + tol)
                if bucket_key not in self.buckets:
                    self.buckets[bucket_key] = []
                self.buckets[bucket_key].append(key)

    def normalize_ocr(self, text: str) -> str:
        if not text:
            return ""
        text = text.casefold().strip().replace("'", "").replace("-", " ")
        # Correcciones comunes (puedes expandir con ocr_corrections.json)
        corrections = {"0rc": "orc", "dr4g0n": "dragon", "rn4st3r": "master", "cycl0ps": "cyclops"}
        for wrong, right in corrections.items():
            text = text.replace(wrong, right)
        return text

    def match(self, ocr_text: str) -> Optional[str]:
        if not self.registry:
            return None
        norm = self.normalize_ocr(ocr_text)
        if not norm:
            return None
        candidates: List[str] = []
        l = len(norm)
        first = norm[0] if norm else ""
        for tol in range(-3, 4):
            bucket_key = (first, l + tol)
            candidates.extend(self.buckets.get(bucket_key, []))
        if not candidates:
            return None
        scores = [(key, fuzz.WRatio(norm, key)) for key in set(candidates)]
        scores.sort(key=lambda x: x[1], reverse=True)
        top_score = scores[0][1]
        if top_score >= 92:
            return scores[0][0]
        elif top_score >= 85 and (len(scores) == 1 or top_score - scores[1][1] > 5):
            return scores[0][0]
        return None  # Unknown o ambiguo