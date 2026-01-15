from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Inspect a replay snapshot (JSON + ROI crops).")
    p.add_argument("json_path", type=str, help="Path to replay JSON (e.g. logs/replay/123.456789.json)")
    p.add_argument("--out", type=str, default="", help="Output PNG path (default: alongside JSON)")
    p.add_argument("--cols", type=int, default=3, help="Columns in montage")
    p.add_argument("--scale", type=float, default=2.0, help="Scale factor for small crops")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    json_path = Path(args.json_path)
    if not json_path.exists():
        raise SystemExit(f"JSON no existe: {json_path}")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rois_dir = json_path.parent / "rois"

    ts = json_path.stem
    pngs = sorted(rois_dir.glob(f"{ts}_*.png"))

    # Lazy import cv2/numpy
    import cv2
    import numpy as np

    tiles: list[tuple[str, np.ndarray]] = []
    for p in pngs:
        name = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            continue
        tiles.append((name, img))

    if not tiles:
        raise SystemExit(f"No encontré crops para ts={ts} en {rois_dir}")

    cols = max(1, int(args.cols))
    scale = max(1.0, float(args.scale))

    # Normalize tile sizes
    max_h = max(t[1].shape[0] for t in tiles)
    max_w = max(t[1].shape[1] for t in tiles)
    tile_h = int(max_h * scale)
    tile_w = int(max_w * scale)

    rows = (len(tiles) + cols - 1) // cols
    pad = 10
    header_h = 170

    canvas_h = header_h + rows * tile_h + (rows + 1) * pad
    canvas_w = cols * tile_w + (cols + 1) * pad

    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = (20, 20, 20)

    # Header text
    def put(line: str, y: int) -> None:
        cv2.putText(canvas, line, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1, cv2.LINE_AA)

    def clip(s: object, n: int = 140) -> str:
        try:
            t = str(s or "")
        except Exception:
            return ""
        t = t.replace("\n", " ").replace("\r", " ").strip()
        if len(t) > n:
            return t[: n - 3] + "..."
        return t

    def fmt_actions(tel: dict) -> str:
        try:
            raw = tel.get("action_requests")
            if isinstance(raw, list) and raw:
                parts: list[str] = []
                for r in raw:
                    if not isinstance(r, dict):
                        continue
                    kind = clip(r.get("kind", ""), 40)
                    value = clip(r.get("value", ""), 60)
                    committed = bool(r.get("committed", False))
                    note = str(r.get("note", "") or "").strip().lower()
                    star = "*" if committed or note == "committed" else ""
                    if kind or value:
                        parts.append(f"{kind}:{value}{star}" if kind else f"{value}{star}")
                s = ";".join(parts)
                if s:
                    return s
        except Exception:
            pass

        # Legacy fallback
        try:
            return clip(tel.get("action_request", ""), 200)
        except Exception:
            return ""

    put(f"Replay: {json_path.name}", 25)
    res = payload.get("resolution")
    put(f"Resolution: {res}", 50)
    tel = payload.get("telemetry", {})
    if not isinstance(tel, dict):
        tel = {}
    put(
        "Telemetry: "
        + f"hp_pct={tel.get('hp_pct')} mp_pct={tel.get('mp_pct')} low_hp={tel.get('low_hp')} low_mp={tel.get('low_mp')}",
        75,
    )
    put(
        f"Target={tel.get('target','')}  Reco={tel.get('recommendation','')}  Cavebot={tel.get('cavebot_next','')}",
        100,
    )

    act = fmt_actions(tel)
    if act:
        committed = tel.get("action_committed", None)
        committed_s = "committed" if bool(committed) else "preview"
        src = clip(tel.get("action_source", ""), 60)
        src_s = f"  planner={src}" if src else ""
        put(f"Actions ({committed_s}): {clip(act, 200)}{src_s}", 125)
    ip = clip(tel.get("input_plan", ""), 180)
    if ip:
        put(f"Inputs: {ip}", 150)

    # Tiles
    for idx, (name, img) in enumerate(tiles):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (tile_w + pad)
        y0 = header_h + pad + r * (tile_h + pad)

        # Resize into tile box keeping aspect ratio
        h, w = img.shape[:2]
        sf = min(tile_w / max(1, w), tile_h / max(1, h))
        nw = max(1, int(w * sf))
        nh = max(1, int(h * sf))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_NEAREST)

        tile = np.zeros((tile_h, tile_w, 3), dtype=np.uint8)
        tile[:] = (30, 30, 30)
        ox = (tile_w - nw) // 2
        oy = (tile_h - nh) // 2
        tile[oy : oy + nh, ox : ox + nw] = resized

        canvas[y0 : y0 + tile_h, x0 : x0 + tile_w] = tile
        cv2.putText(
            canvas,
            name,
            (x0, y0 - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

    out = Path(args.out) if args.out else (json_path.with_suffix(".montage.png"))
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), canvas)
    print(f"OK: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
