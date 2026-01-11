from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tools.coords_to_route import _parse_coords_line


def _get_clipboard_text() -> str:
    """Read Windows clipboard text via PowerShell (no extra deps)."""

    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard | Out-String"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode != 0:
            return ""
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _safe_write_json(path: Path, data: object) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _format_wp(i: int, wp: dict) -> str:
    x = wp.get("x")
    y = wp.get("y")
    z = wp.get("z")
    name = wp.get("name")
    action = wp.get("action")
    pos = f"({x},{y})" if z is None else f"({x},{y},{z})"
    extra = []
    if name:
        extra.append(f"name={name}")
    if action:
        extra.append(f"action={action}")
    return f"#{i:04d} {pos}" + (" " + " ".join(extra) if extra else "")


@dataclass
class _NextMeta:
    name: Optional[str] = None
    action: Optional[str] = None
    once_action: Optional[str] = None


class RouteRecorder:
    def __init__(self, out_path: Path, *, name_prefix: str = "wp") -> None:
        self.out_path = out_path
        self.name_prefix = name_prefix
        self._waypoints: list[dict] = []
        self._meta = _NextMeta()
        self._lock = threading.Lock()

    @property
    def waypoints(self) -> list[dict]:
        with self._lock:
            return list(self._waypoints)

    def set_next_name(self, name: str | None) -> None:
        with self._lock:
            self._meta.name = (name or "").strip() or None

    def set_action(self, action: str | None) -> None:
        with self._lock:
            self._meta.action = (action or "").strip() or None

    def set_once_action(self, action: str | None) -> None:
        with self._lock:
            self._meta.once_action = (action or "").strip() or None

    def add_waypoint(self, x: int, y: int, z: int | None = None) -> dict:
        with self._lock:
            idx = len(self._waypoints)
            name = self._meta.name or f"{self.name_prefix}{idx:04d}"

            action = None
            if self._meta.once_action:
                action = self._meta.once_action
                self._meta.once_action = None
            elif self._meta.action:
                action = self._meta.action

            wp: dict = {"x": int(x), "y": int(y), "name": str(name)}
            if z is not None:
                wp["z"] = int(z)
            if action:
                wp["action"] = str(action)

            # Reset one-shot name after use.
            if self._meta.name is not None:
                self._meta.name = None

            self._waypoints.append(wp)
            _safe_write_json(self.out_path, self._waypoints)
            return wp

    def undo(self) -> dict | None:
        with self._lock:
            if not self._waypoints:
                return None
            wp = self._waypoints.pop()
            _safe_write_json(self.out_path, self._waypoints)
            return wp


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Interactive cavebot route recorder (safe): reads coordinates from clipboard and writes route JSON. "
            "Supports hotkeys + console commands. Output is compatible with navigation.route.load_route()."
        )
    )
    p.add_argument("--out", required=True, help="Output route JSON path")
    p.add_argument("--name-prefix", default=os.getenv("ROUTE_NAME_PREFIX", "wp"))

    p.add_argument("--hotkey-node", default=os.getenv("ROUTE_HOTKEY_NODE", "f12"))
    p.add_argument("--hotkey-stand", default=os.getenv("ROUTE_HOTKEY_STAND", "f11"))

    p.add_argument(
        "--no-hotkeys",
        action="store_true",
        help="Disable keyboard hotkeys (console-only).",
    )
    p.add_argument(
        "--poll-clipboard",
        action="store_true",
        help="Also auto-add when clipboard text changes and is parseable.",
    )
    p.add_argument("--poll-s", type=float, default=0.25, help="Clipboard poll interval (when --poll-clipboard)")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    out_path = Path(args.out)
    rec = RouteRecorder(out_path, name_prefix=str(args.name_prefix))

    print(f"📁 Route se guardará en: {out_path}")
    print("📋 Copia coordenadas al portapapeles, por ejemplo:")
    print("   - 'X: 32561 Y: 32496 Z: 7'")
    print("   - '32561 32496 7'")
    print("   - '32561,32496,7'\n")

    print("⌨️  Comandos (en consola):")
    print("   - label NOMBRE        -> nombre para el próximo waypoint")
    print("   - action TEXTO        -> action persistente (para todos los siguientes)")
    print("   - once TEXTO          -> action solo para el próximo waypoint")
    print("   - rope|shovel|loot     -> atajos (equivale a: once rope/shovel/loot)")
    print("   - clear_action         -> borra action persistente")
    print("   - add                 -> agrega waypoint usando el clipboard actual")
    print("   - undo                -> borra el último waypoint")
    print("   - list                -> muestra los últimos waypoints")
    print("   - stop                -> termina\n")

    use_hotkeys = not bool(args.no_hotkeys)

    def add_from_clipboard(*, once_action: str | None = None) -> None:
        if once_action:
            rec.set_once_action(once_action)
        text = _get_clipboard_text()
        parsed = _parse_coords_line(text)
        if parsed is None:
            print("❌ No hay coordenadas válidas en el portapapeles.")
            return
        x, y, z = parsed
        wp = rec.add_waypoint(x=int(x), y=int(y), z=(int(z) if z is not None else None))
        print("✅", _format_wp(len(rec.waypoints) - 1, wp))

    # Hotkeys (optional)
    if use_hotkeys:
        try:
            import keyboard  # type: ignore

            keyboard.add_hotkey(str(args.hotkey_node), lambda: add_from_clipboard())
            keyboard.add_hotkey(str(args.hotkey_stand), lambda: add_from_clipboard(once_action="stand"))
            print(f"🟢 Hotkeys activos: node={args.hotkey_node}, stand={args.hotkey_stand}")
        except Exception as e:
            print(f"⚠️  No pude activar hotkeys (keyboard): {e}")
            print("   Usa el modo consola: escribe 'add' y Enter.")

    stop_flag = threading.Event()

    # Clipboard polling mode (optional)
    def poll_thread() -> None:
        last = ""
        while not stop_flag.is_set():
            cur = _get_clipboard_text()
            if cur and cur != last:
                last = cur
                if _parse_coords_line(cur) is not None:
                    add_from_clipboard()
            time.sleep(max(0.05, float(args.poll_s)))

    if bool(args.poll_clipboard):
        t = threading.Thread(target=poll_thread, daemon=True)
        t.start()
        print(f"🟢 Polling clipboard cada {float(args.poll_s):.2f}s (auto-add cuando cambia)")

    # Console loop
    try:
        while True:
            try:
                raw = input().strip()
            except EOFError:
                break

            if not raw:
                continue

            cmd = raw.strip()
            low = cmd.lower()
            if low in {"stop", "exit", "quit"}:
                break
            if low == "add":
                add_from_clipboard()
                continue
            if low == "undo":
                wp = rec.undo()
                if wp is None:
                    print("ℹ️  No hay waypoints para borrar")
                else:
                    print("↩️  undo:", _format_wp(len(rec.waypoints), wp))
                continue
            if low == "list":
                wps = rec.waypoints
                tail = wps[-10:]
                base = max(0, len(wps) - len(tail))
                for i, wp in enumerate(tail, start=base):
                    print(_format_wp(i, wp))
                continue
            if low.startswith("label "):
                name = cmd[6:].strip()
                rec.set_next_name(name)
                print(f"✅ next name: {name}")
                continue
            if low.startswith("action "):
                action = cmd[7:].strip()
                rec.set_action(action)
                print(f"✅ action persistente: {action}")
                continue
            if low == "clear_action":
                rec.set_action(None)
                print("✅ action persistente borrada")
                continue
            if low.startswith("once "):
                action = cmd[5:].strip()
                rec.set_once_action(action)
                print(f"✅ once: {action}")
                continue
            if low in {"rope", "shovel", "loot", "quick_loot", "ql"}:
                act = "loot" if low in {"loot", "quick_loot", "ql"} else low
                rec.set_once_action(act)
                print(f"✅ once: {act}")
                continue

            print("❓ Comando no reconocido. Usa: label X, action Y, once Z, add, undo, list, stop")
    except KeyboardInterrupt:
        pass
    finally:
        stop_flag.set()

    print(f"\n✅ Route guardada: {out_path} ({len(rec.waypoints)} waypoints)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
