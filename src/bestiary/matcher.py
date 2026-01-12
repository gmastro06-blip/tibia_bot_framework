from __future__ import annotations

import json
import os
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from rapidfuzz import fuzz


@dataclass(frozen=True)
class BestiaryMatchConfig:
    reliable: int = 92
    probable: int = 85
    ambiguous_delta: int = 5
    len_tolerance: int = 3
    bucket_first_letter: bool = True

    @staticmethod
    def from_mapping(d: Mapping[str, Any]) -> "BestiaryMatchConfig":
        thresholds = d.get("thresholds", {}) if isinstance(d, Mapping) else {}
        indexing = d.get("indexing", {}) if isinstance(d, Mapping) else {}

        def _int(v: Any, default: int) -> int:
            try:
                return int(v)
            except Exception:
                return int(default)

        def _bool(v: Any, default: bool) -> bool:
            try:
                if isinstance(v, bool):
                    return v
                s = str(v).strip().lower()
                if s in {"1", "true", "yes", "y", "on"}:
                    return True
                if s in {"0", "false", "no", "n", "off"}:
                    return False
            except Exception:
                pass
            return bool(default)

        return BestiaryMatchConfig(
            reliable=_int(thresholds.get("reliable", 92), 92),
            probable=_int(thresholds.get("probable", 85), 85),
            ambiguous_delta=_int(thresholds.get("ambiguous_delta", 5), 5),
            len_tolerance=_int(indexing.get("len_tolerance", 3), 3),
            bucket_first_letter=_bool(indexing.get("first_letter", True), True),
        )

    @staticmethod
    def load_yaml(path: str | os.PathLike[str]) -> "BestiaryMatchConfig":
        p = Path(path)
        data: Any = {}
        try:
            y = importlib.import_module("yaml")
            safe_load = getattr(y, "safe_load", None)
            if callable(safe_load):
                data = safe_load(p.read_text(encoding="utf-8"))
            else:
                data = {}
        except Exception:
            data = {}
        if not isinstance(data, Mapping):
            data = {}
        return BestiaryMatchConfig.from_mapping(data)


@dataclass(frozen=True)
class BestiaryRules:
    aliases: Mapping[str, str]
    ignore: frozenset[str]
    priority: tuple[str, ...]

    @staticmethod
    def from_mapping(d: Mapping[str, Any]) -> "BestiaryRules":
        rules = d.get("rules", {}) if isinstance(d, Mapping) else {}
        if not isinstance(rules, Mapping):
            rules = {}

        aliases_raw = rules.get("aliases", {})
        aliases: dict[str, str] = {}
        if isinstance(aliases_raw, Mapping):
            for k, v in aliases_raw.items():
                try:
                    kk = _norm_key(str(k))
                    vv = _norm_key(str(v))
                    if kk and vv:
                        aliases[kk] = vv
                except Exception:
                    continue

        ignore_raw = rules.get("ignore", [])
        ignore: set[str] = set()
        if isinstance(ignore_raw, (list, tuple)):
            for x in ignore_raw:
                try:
                    kk = _norm_key(str(x))
                    if kk:
                        ignore.add(kk)
                except Exception:
                    continue

        prio_raw = rules.get("priority", [])
        prio: list[str] = []
        if isinstance(prio_raw, (list, tuple)):
            for x in prio_raw:
                try:
                    kk = _norm_key(str(x))
                    if kk:
                        prio.append(kk)
                except Exception:
                    continue

        return BestiaryRules(
            aliases=aliases,
            ignore=frozenset(ignore),
            priority=tuple(prio),
        )

    @staticmethod
    def load_yaml(path: str | os.PathLike[str]) -> "BestiaryRules":
        p = Path(path)
        data: Any = {}
        try:
            y = importlib.import_module("yaml")
            safe_load = getattr(y, "safe_load", None)
            if callable(safe_load):
                data = safe_load(p.read_text(encoding="utf-8"))
            else:
                data = {}
        except Exception:
            data = {}
        if not isinstance(data, Mapping):
            data = {}
        return BestiaryRules.from_mapping(data)


@dataclass(frozen=True)
class MatchResult:
    name_key: str | None
    score: int
    tier: str  # reliable|probable|weak|none
    ambiguous: bool
    top2: tuple[tuple[str, int], tuple[str, int] | None]


def _norm_key(s: str) -> str:
    # Keep it close to how detector labels tend to look.
    out = (s or "").strip().lower()
    out = out.replace("-", " ").replace("_", " ")
    out = " ".join(out.split())
    return out


def _first_letter_bucket(s: str) -> str:
    s = _norm_key(s)
    return s[0] if s else ""


class BestiaryMatcher:
    """Fuzzy match noisy labels to a canonical creature registry."""

    def __init__(
        self,
        registry: Mapping[str, Any] | Sequence[Mapping[str, Any]],
        *,
        cfg: BestiaryMatchConfig | None = None,
        rules: BestiaryRules | None = None,
        ocr_corrections: Mapping[str, str] | None = None,
    ) -> None:
        self.cfg = cfg or BestiaryMatchConfig()
        self.rules = rules or BestiaryRules(aliases={}, ignore=frozenset(), priority=())
        self._ocr_corrections = {str(k).strip().lower(): str(v).strip().lower() for k, v in (ocr_corrections or {}).items()}

        self._keys: list[str] = []
        self._key_to_payload: dict[str, Any] = {}

        if isinstance(registry, Mapping):
            for k, payload in registry.items():
                name_key = str(k).strip().lower()
                if not name_key:
                    continue
                self._keys.append(name_key)
                self._key_to_payload[name_key] = payload
        else:
            for item in registry:
                if not isinstance(item, Mapping):
                    continue
                nk = item.get("name_key", None)
                if nk is None:
                    nk = item.get("key", None)
                if nk is None:
                    nk = item.get("name", None)
                name_key = str(nk or "").strip().lower()
                if not name_key:
                    continue
                self._keys.append(name_key)
                self._key_to_payload[name_key] = dict(item)

        # Pre-bucket for speed.
        self._buckets: dict[str, list[str]] = {}
        if self.cfg.bucket_first_letter:
            for k in self._keys:
                b = _first_letter_bucket(k)
                self._buckets.setdefault(b, []).append(k)

    @staticmethod
    def load_registry_json(path: str | os.PathLike[str]) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping) or isinstance(raw, list):
            return raw  # type: ignore[return-value]
        raise ValueError("Unsupported registry format")

    @staticmethod
    def load_ocr_corrections_json(path: str | os.PathLike[str]) -> Mapping[str, str]:
        p = Path(path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            try:
                out[str(k).strip().lower()] = str(v).strip().lower()
            except Exception:
                continue
        return out

    @classmethod
    def from_files(
        cls,
        *,
        registry_path: str,
        config_path: str | None = None,
        ocr_corrections_path: str | None = None,
    ) -> "BestiaryMatcher":
        reg = cls.load_registry_json(registry_path)
        cfg = BestiaryMatchConfig.load_yaml(config_path) if config_path else BestiaryMatchConfig()
        rules = BestiaryRules.load_yaml(config_path) if config_path else BestiaryRules(aliases={}, ignore=frozenset(), priority=())
        corr = cls.load_ocr_corrections_json(ocr_corrections_path) if ocr_corrections_path else {}
        return cls(reg, cfg=cfg, rules=rules, ocr_corrections=corr)

    def payload_for(self, name_key: str) -> Any:
        return self._key_to_payload.get(str(name_key).strip().lower())

    def _candidates(self, query: str) -> Iterable[str]:
        if not self._keys:
            return []

        qn = _norm_key(query)
        if not qn:
            return []

        # Bucket by first letter when enabled.
        if self.cfg.bucket_first_letter:
            b = _first_letter_bucket(qn)
            cands = self._buckets.get(b, [])
        else:
            cands = self._keys

        # Length-based pruning.
        lt = max(0, int(self.cfg.len_tolerance))
        qlen = len(qn)
        if lt <= 0:
            return cands

        out: list[str] = []
        for k in cands:
            kn = _norm_key(k)
            if abs(len(kn) - qlen) <= lt:
                out.append(k)
        return out

    def match(self, label: str) -> MatchResult:
        raw = (label or "").strip()
        if not raw or not self._keys:
            return MatchResult(name_key=None, score=0, tier="none", ambiguous=False, top2=(("", 0), None))

        # Apply exact OCR corrections on normalized text.
        qn = _norm_key(raw)
        qn = self._ocr_corrections.get(qn, qn)

        # Apply aliases and ignore.
        try:
            qn = _norm_key(self.rules.aliases.get(qn, qn))
        except Exception:
            qn = _norm_key(qn)
        if qn in self.rules.ignore:
            return MatchResult(name_key=None, score=0, tier="none", ambiguous=False, top2=(("", 0), None))

        cands = list(self._candidates(qn))
        if not cands:
            cands = list(self._keys)

        # Score all candidates (small registry, fast enough).
        scored: list[tuple[str, int]] = []
        for k in cands:
            score = int(fuzz.WRatio(qn, _norm_key(k)))
            scored.append((k, score))

        scored.sort(key=lambda t: t[1], reverse=True)
        top1 = scored[0]
        top2 = scored[1] if len(scored) > 1 else None

        ambiguous = False
        try:
            if top2 is not None and (int(top1[1]) - int(top2[1])) < int(self.cfg.ambiguous_delta):
                ambiguous = True
        except Exception:
            ambiguous = False

        tier = "weak"
        if top1[1] >= int(self.cfg.reliable):
            tier = "reliable"
        elif top1[1] >= int(self.cfg.probable):
            tier = "probable"

        return MatchResult(
            name_key=str(top1[0]),
            score=int(top1[1]),
            tier=tier,
            ambiguous=bool(ambiguous),
            top2=(top1, top2),
        )

    def canonicalize(self, label: str) -> str | None:
        """Return canonical name_key if match is at least probable and not ambiguous."""
        r = self.match(label)
        if r.name_key is None:
            return None
        if r.tier in {"reliable", "probable"} and not r.ambiguous:
            return r.name_key
        return None
