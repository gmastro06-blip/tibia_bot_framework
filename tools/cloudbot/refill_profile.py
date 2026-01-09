from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping


@dataclass(frozen=True)
class RefillItem:
    name: str
    qty: int


@dataclass(frozen=True)
class RefillProfile:
    items: List[RefillItem]
    notes: str | None = None

    @staticmethod
    def _norm_name(name: str) -> str:
        return " ".join(name.strip().lower().split())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RefillProfile":
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("'items' debe ser una lista")

        items: List[RefillItem] = []
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            qty = entry.get("qty")
            if not isinstance(name, str) or not name.strip():
                continue

            if qty is None:
                continue
            try:
                qty_i = int(qty)
            except Exception:
                continue
            if qty_i < 0:
                continue
            items.append(RefillItem(name=cls._norm_name(name), qty=qty_i))

        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            notes = str(notes)

        return cls(items=items, notes=notes)

    @classmethod
    def load(cls, path: Path) -> "RefillProfile":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("El JSON del refill profile debe ser un objeto")
        return cls.from_dict(data)

    def summary(self) -> Dict[str, Any]:
        preview = [f"{it.name}:{it.qty}" for it in self.items[:8]]
        return {
            "items_count": len(self.items),
            "preview": preview,
        }
