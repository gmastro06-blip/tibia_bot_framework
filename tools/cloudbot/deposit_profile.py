from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping


DepositMode = Literal["deposit_all_except_keep", "deposit_only_list"]


@dataclass(frozen=True)
class DepositProfile:
    mode: DepositMode
    keep: List[str]
    deposit: List[str]
    notes: str | None = None

    @staticmethod
    def _norm_item(name: str) -> str:
        return " ".join(name.strip().lower().split())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DepositProfile":
        raw_mode = str(data.get("mode", "deposit_all_except_keep")).strip().lower()
        if raw_mode not in {"deposit_all_except_keep", "deposit_only_list"}:
            raise ValueError(
                f"mode inválido: {raw_mode!r} (usa 'deposit_all_except_keep' o 'deposit_only_list')"
            )
        mode: DepositMode = raw_mode  # type: ignore[assignment]

        keep_raw = data.get("keep", [])
        deposit_raw = data.get("deposit", [])
        if not isinstance(keep_raw, list) or not isinstance(deposit_raw, list):
            raise ValueError("'keep' y 'deposit' deben ser listas")

        keep = [cls._norm_item(x) for x in keep_raw if isinstance(x, str) and x.strip()]
        deposit = [cls._norm_item(x) for x in deposit_raw if isinstance(x, str) and x.strip()]

        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            notes = str(notes)

        return cls(mode=mode, keep=keep, deposit=deposit, notes=notes)

    @classmethod
    def load(cls, path: Path) -> "DepositProfile":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            raise ValueError("El JSON del deposit profile debe ser un objeto")
        return cls.from_dict(data)

    def should_deposit(self, item_name: str) -> bool:
        item = self._norm_item(item_name)
        if not item:
            return False

        keep_set = set(self.keep)
        deposit_set = set(self.deposit)

        if self.mode == "deposit_only_list":
            return item in deposit_set

        # deposit_all_except_keep
        if item in keep_set:
            return False
        return True

    def summary(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "keep_count": len(self.keep),
            "deposit_count": len(self.deposit),
        }
