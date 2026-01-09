from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping


QuickLootMode = Literal["accepted", "skipped"]


@dataclass(frozen=True)
class QuickLootProfile:
    mode: QuickLootMode
    accepted: List[str]
    skipped: List[str]
    container_categories: Dict[str, str]
    notes: str | None = None

    @staticmethod
    def _norm_item(name: str) -> str:
        return " ".join(name.strip().lower().split())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QuickLootProfile":
        raw_mode = str(data.get("mode", "skipped")).strip().lower()
        if raw_mode not in {"accepted", "skipped"}:
            raise ValueError(f"mode inválido: {raw_mode!r} (usa 'accepted' o 'skipped')")
        mode: QuickLootMode = raw_mode  # type: ignore[assignment]

        accepted_raw = data.get("accepted", [])
        skipped_raw = data.get("skipped", [])
        if not isinstance(accepted_raw, list) or not isinstance(skipped_raw, list):
            raise ValueError("'accepted' y 'skipped' deben ser listas")

        accepted = [cls._norm_item(x) for x in accepted_raw if isinstance(x, str) and x.strip()]
        skipped = [cls._norm_item(x) for x in skipped_raw if isinstance(x, str) and x.strip()]

        cc_raw = data.get("container_categories", {})
        if not isinstance(cc_raw, dict):
            raise ValueError("'container_categories' debe ser un objeto/dict")
        container_categories: Dict[str, str] = {}
        for k, v in cc_raw.items():
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                container_categories[k.strip()] = v.strip()

        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            notes = str(notes)

        return cls(
            mode=mode,
            accepted=accepted,
            skipped=skipped,
            container_categories=container_categories,
            notes=notes,
        )

    @classmethod
    def load(cls, path: Path) -> "QuickLootProfile":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("El JSON del perfil debe ser un objeto")
        return cls.from_dict(data)

    def should_loot(self, item_name: str) -> bool:
        """Aplica la lógica Accepted/Skipped.

        - mode=accepted: solo items en accepted
        - mode=skipped: todos excepto los de skipped
        """
        item = self._norm_item(item_name)
        if not item:
            return False

        if self.mode == "accepted":
            return item in set(self.accepted)
        return item not in set(self.skipped)

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "accepted_count": len(self.accepted),
            "skipped_count": len(self.skipped),
            "container_categories_count": len(self.container_categories),
        }
