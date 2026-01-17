from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def _iso_now() -> str:
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:
        # Fallback (still ISO-ish)
        return datetime.utcnow().isoformat() + "Z"


def _safe_int(v: Any) -> int | None:
    try:
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            if v != v:  # NaN
                return None
            return int(v)
        s = str(v).strip()
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _extract_hotkey(kind: str, value: str) -> str:
    k = (kind or "").strip().lower()
    v = (value or "").strip()
    if not v:
        return ""

    # spell_* actions may encode "spell:HOTKEY".
    if k.startswith("spell_") and ":" in v:
        try:
            return v.split(":", 1)[1].strip()
        except Exception:
            return v
    return v


@dataclass
class MacroRecorderState:
    last_pos: tuple[int, int, int] | None = None
    last_action_sig: str = ""


class MacroRecorder:
    """Macro recorder (puro, sin UI) para nodos y acciones.

    - Graba nodes cuando cambia posición (si está habilitado).
    - Graba acciones con anti-duplicado por firma.
    - Puede enriquecer acciones usando un mapa hotkey -> {action_name,use}.
    """

    def __init__(self) -> None:
        self.active = False
        self.created_at = ""
        self.route_dir = ""
        self._start_ts: float | None = None
        self._state = MacroRecorderState()
        self.record_nodes = True
        self.committed_only = True
        self.events: list[dict[str, Any]] = []
        self._hotkey_map: dict[str, dict[str, str]] = {}
        self.last_event_summary = "-"

    def start(self, *, route_dir: str, ts: float | None = None) -> None:
        self.active = True
        self.created_at = _iso_now()
        self.route_dir = str(route_dir or "")
        self._start_ts = float(ts) if ts is not None else None
        self._state = MacroRecorderState()
        self.events = []
        self.last_event_summary = "-"

    def stop(self) -> None:
        self.active = False

    def set_hotkey_map(self, mapping: dict[str, dict[str, str]] | None) -> None:
        try:
            self._hotkey_map = dict(mapping or {})
        except Exception:
            self._hotkey_map = {}

    def _rel_ts(self, ts: float) -> float:
        try:
            if self._start_ts is None:
                self._start_ts = float(ts)
            return max(0.0, float(ts) - float(self._start_ts))
        except Exception:
            return 0.0

    def on_tick(
        self,
        ts: float,
        pos_tuple_or_none: tuple[Any, Any, Any] | None,
        action_requests_list: Iterable[dict[str, Any]] | None,
    ) -> None:
        if not self.active:
            return

        rts = self._rel_ts(float(ts))

        # Position
        pos: tuple[int, int, int] | None = None
        try:
            if pos_tuple_or_none is not None:
                x = _safe_int(pos_tuple_or_none[0])
                y = _safe_int(pos_tuple_or_none[1])
                z = _safe_int(pos_tuple_or_none[2])
                if x is not None and y is not None and z is not None:
                    pos = (x, y, z)
        except Exception:
            pos = None

        if self.record_nodes and pos is not None and pos != self._state.last_pos:
            ev = {"ts": float(rts), "type": "node", "x": pos[0], "y": pos[1], "z": pos[2]}
            self.events.append(ev)
            self._state.last_pos = pos
            self.last_event_summary = f"node ({pos[0]},{pos[1]},{pos[2]})"

        # Actions
        actions: list[dict[str, Any]] = []
        try:
            if action_requests_list is not None:
                for a in action_requests_list:
                    if isinstance(a, dict):
                        actions.append(a)
        except Exception:
            actions = []

        picked: dict[str, Any] | None = None
        for a in actions:
            try:
                committed = bool(a.get("committed", False))
            except Exception:
                committed = False
            if self.committed_only and not committed:
                continue
            picked = a
            break

        if picked is None:
            return

        kind = str(picked.get("kind", "") or "")
        value = str(picked.get("value", "") or "")
        committed = bool(picked.get("committed", False))

        sig = f"{kind}|{value}|{committed}"
        if sig == self._state.last_action_sig:
            return

        hotkey = _extract_hotkey(kind, value)
        action_name = ""
        use = ""
        try:
            meta = self._hotkey_map.get(str(hotkey).strip(), None)
            if isinstance(meta, dict):
                action_name = str(meta.get("action_name", "") or "")
                use = str(meta.get("use", "") or "")
        except Exception:
            action_name = ""
            use = ""

        x = pos[0] if pos is not None else None
        y = pos[1] if pos is not None else None
        z = pos[2] if pos is not None else None

        ev = {
            "ts": float(rts),
            "type": "action",
            "x": x,
            "y": y,
            "z": z,
            "kind": kind,
            "value": value,
            "committed": bool(committed),
            "action_name": action_name,
            "use": use,
            "source": "telemetry",
        }
        self.events.append(ev)
        self._state.last_action_sig = sig
        self.last_event_summary = f"action {kind}:{value}{'*' if committed else ''}"

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "created_at": str(self.created_at or _iso_now()),
            "route_dir": str(self.route_dir or ""),
            "events": list(self.events),
        }
