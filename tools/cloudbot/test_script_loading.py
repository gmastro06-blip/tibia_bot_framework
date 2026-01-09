from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import pytest


# Este módulo depende de datos externos (carpeta scripts-master, reports generados, etc.).
# Para evitar fallos/ruido en CI o en entornos donde no existen esos assets, es opt-in.
_RUN = os.getenv("RUN_CLOUDBOT_SCRIPTS_TESTS", "").strip().lower() in {"1", "true", "yes"}
if not _RUN:
    pytest.skip(
        "tools/cloudbot/test_script_loading.py es opt-in; setea RUN_CLOUDBOT_SCRIPTS_TESTS=1 para ejecutarlo",
        allow_module_level=True,
    )


@dataclass(frozen=True)
class WPLine:
    kind: str  # label|action|node|stand|rope|ladder|...
    raw: str
    label: Optional[str] = None
    action: Optional[str] = None
    pos: Optional[Tuple[int, int, int]] = None


_POS_RE = re.compile(r"\((\s*-?\d+\s*),(\s*-?\d+\s*),(\s*-?\d+\s*)\)")


def parse_waypoints_in(path: Path) -> List[WPLine]:
    out: List[WPLine] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue

        # Normalize whitespace
        line = re.sub(r"\s+", " ", line)

        if line.lower().startswith("label "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="label", raw=raw, label=name))
            continue

        if line.lower().startswith("action "):
            name = line.split(" ", 1)[1].strip()
            out.append(WPLine(kind="action", raw=raw, action=name))
            continue

        # Generic command with a position: node (x,y,z), stand (x,y,z), rope (x,y,z), ladder (x,y,z)...
        parts = line.split(" ", 1)
        cmd = parts[0].strip().lower()
        m = _POS_RE.search(line)
        if m:
            x = int(m.group(1))
            y = int(m.group(2))
            z = int(m.group(3))
            out.append(WPLine(kind=cmd, raw=raw, pos=(x, y, z)))
            continue

        # Fallback unknown
        out.append(WPLine(kind="unknown", raw=raw))

    return out


def test_all_scripts():
    scripts_root = Path("scripts-master")
    reports_root = Path("reports/cloudbot")

    if not scripts_root.exists():
        print(f"ERROR: {scripts_root} no existe")
        return

    # Encontrar todas las carpetas con waypoints.in
    scripts = []
    for folder in scripts_root.iterdir():
        if folder.is_dir() and (folder / "waypoints.in").exists():
            scripts.append(folder.name)

    scripts.sort()
    print(f"Encontrados {len(scripts)} scripts con waypoints.in")

    successful = 0
    total = len(scripts)

    for script_name in scripts:
        print(f"\n--- Probando {script_name} ---")
        try:
            script_path = scripts_root / script_name
            wp_file = script_path / "waypoints.in"
            waypoints = parse_waypoints_in(wp_file)

            # Verificar JSONs
            reports_path = reports_root / script_name
            json_files = ["setup_actions.json", "actions_in_waypoints.json", "route_stats.json"]
            json_ok = 0
            for json_file in json_files:
                if (reports_path / json_file).exists():
                    json_ok += 1

            if waypoints:
                successful += 1
                print(f"✅ Waypoints: {len(waypoints)} | JSONs: {json_ok}/3")
            else:
                print("❌ Sin waypoints")

        except Exception as e:
            print(f"❌ Error: {e}")

    percentage = (successful / total * 100) if total > 0 else 0
    print("\n=== RESULTADO FINAL ===")
    print(f"Scripts totales: {total}")
    print(f"Scripts exitosos: {successful}")
    print(f"Scripts fallidos: {total - successful}")
    print(f"Funcionalidad: {percentage:.1f}% (basado en carga de waypoints)")


def test_script_loading(script_name: str):
    scripts_root = Path("scripts-master")
    reports_root = Path("reports/cloudbot")

    # Verificar que existe el script
    script_path = scripts_root / script_name
    if not script_path.exists() or not script_path.is_dir():
        print(f"ERROR: Script '{script_name}' no encontrado en {scripts_root}")
        return

    wp_file = script_path / "waypoints.in"
    if not wp_file.exists():
        print(f"ERROR: waypoints.in no encontrado en {script_path}")
        return

    print(f"Cargando script: {script_name}")
    print(f"Waypoints file: {wp_file}")

    # Cargar waypoints
    waypoints = parse_waypoints_in(wp_file)
    print(f"Waypoints cargados: {len(waypoints)}")

    # Mostrar primeros 5 waypoints
    for i, w in enumerate(waypoints[:5]):
        print(f"  {i+1}: {w.kind} - {w.raw.strip()}")
    if len(waypoints) > 5:
        print(f"  ... y {len(waypoints) - 5} más")

    # Cargar JSONs desde reports
    reports_path = reports_root / script_name
    json_files = ["setup_actions.json", "actions_in_waypoints.json", "route_stats.json"]

    for json_file in json_files:
        json_path = reports_path / json_file
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                print(f"\nCargado {json_file}:")
                if json_file == "route_stats.json":
                    print(f"  Líneas: {data.get('counts', {}).get('lines', 0)}")
                    print(f"  Puntos de posición: {data.get('counts', {}).get('pos_points', 0)}")
                    print(f"  Distancia total (Manhattan): {data.get('distance', {}).get('total_manhattan', 0)}")
                elif json_file == "setup_actions.json":
                    for key, items in data.items():
                        if isinstance(items, list):
                            print(f"  {key}: {len(items)} items")
                elif json_file == "actions_in_waypoints.json":
                    print(f"  Acciones en waypoints: {len(data)}")
                    unique_actions = set(item['action'] for item in data if 'action' in item)
                    print(f"  Acciones únicas: {sorted(unique_actions)}")
            except Exception as e:
                print(f"ERROR cargando {json_file}: {e}")
        else:
            print(f"AVISO: {json_file} no encontrado en {reports_path} (ejecuta el analizador primero)")

    print(f"\nPrueba completada para {script_name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Prueba de carga de script sin UI gráfica")
    ap.add_argument("script_name", nargs='?', default="all", help="Nombre del script (ej: wasp_ab) o 'all' para todos")
    args = ap.parse_args()

    if args.script_name == "all":
        test_all_scripts()
    else:
        test_script_loading(args.script_name)
