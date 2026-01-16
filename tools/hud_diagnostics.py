from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path


def _add_src_to_syspath() -> Path:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    return repo_root


def _iter_images(inputs: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if any(ch in raw for ch in ["*", "?", "["]):
            out.extend([Path(x) for x in sorted(Path().glob(raw))])
            continue
        if p.is_dir():
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.bmp"):
                out.extend(sorted(p.glob(ext)))
            continue
        if p.exists():
            out.append(p)
    # de-dup
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p.resolve())
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def _load_roi_config(repo_root: Path, resolution: tuple[int, int], rois_config_override: str | None) -> tuple[dict, list[int]]:
    width, height = resolution

    if rois_config_override:
        cand = Path(rois_config_override)
        if not cand.is_absolute():
            cand = repo_root / rois_config_override
        cfg = json.loads(cand.read_text(encoding="utf-8"))
        rois = cfg.get("rois_guess_norm", cfg.get("rois", cfg))
        src_res = cfg.get("source_resolution", [width, height])
        return dict(rois), list(src_res)

    config_files = {
        (2048, 1076): "configs/rois_guess.json",
        (1920, 1080): "configs/rois_guess_1920x1080.json",
        (1920, 1009): "configs/rois_guess_1920x1080.json",
    }
    rel = config_files.get((width, height), "configs/rois_guess_1920x1080.json")
    cfg = json.loads((repo_root / rel).read_text(encoding="utf-8"))
    return dict(cfg["rois_guess_norm"]), list(cfg["source_resolution"])


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HUD diagnostics: run GameStateBuilder on images and export a summary.json + optional crops/overlays.")
    p.add_argument("inputs", nargs="+", help="Images, directories, or globs (*.png).")
    p.add_argument("--out-dir", type=str, default="reports/hud_diagnostics", help="Output directory.")
    p.add_argument("--rois-config", type=str, default=os.getenv("ROIS_CONFIG", "").strip() or "", help="ROI config override (JSON).")
    p.add_argument("--limit", type=int, default=0, help="Limit number of images.")
    p.add_argument("--save-crops", action="store_true", help="Save ROI crops into out_dir/crops/.")
    p.add_argument("--save-overlay", action="store_true", help="Save annotated overlays into out_dir/overlay/.")
    p.add_argument("--rois", type=str, default="hp_top_ocr,mp_top_ocr,cap_ocr,battlelist_rows,coords_ocr,hp_low_bar,mp_low_bar", help="Comma-separated ROI names to crop/draw.")
    return p.parse_args()


def main() -> int:
    repo_root = _add_src_to_syspath()

    # Lazy imports so the script can show arg errors without cv2 installed.
    import cv2

    from gamestate.builder import GameStateBuilder
    from vision.roi import roi_to_px_result

    args = _parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rois_names = [s.strip() for s in str(args.rois or "").split(",") if s.strip()]

    imgs = _iter_images(list(args.inputs))
    if not imgs:
        raise SystemExit("No input images found.")
    if args.limit and int(args.limit) > 0:
        imgs = imgs[: int(args.limit)]

    builder = GameStateBuilder()

    summary: dict = {
        "ts": time.time(),
        "n": len(imgs),
        "items": [],
    }

    crops_dir = out_dir / "crops"
    overlay_dir = out_dir / "overlay"
    if args.save_crops:
        crops_dir.mkdir(parents=True, exist_ok=True)
    if args.save_overlay:
        overlay_dir.mkdir(parents=True, exist_ok=True)

    for idx, p in enumerate(imgs):
        frame = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            summary["items"].append({"path": str(p), "error": "imread_failed"})
            continue

        resolution = (int(frame.shape[1]), int(frame.shape[0]))
        rois, src_res = _load_roi_config(repo_root, resolution, args.rois_config or None)
        rois["_source_resolution"] = src_res

        gs = builder.update_from_frame(frame, rois, resolution)

        item = {
            "path": str(p),
            "resolution": list(resolution),
            "hp": {
                "cur": getattr(gs, "hp_current", None),
                "max": getattr(gs, "hp_max", None),
                "pct": getattr(gs, "hp_pct", None),
                "method": getattr(gs, "hp_method", ""),
                "reason": getattr(gs, "hp_reason", ""),
            },
            "mp": {
                "cur": getattr(gs, "mp_current", None),
                "max": getattr(gs, "mp_max", None),
                "pct": getattr(gs, "mp_pct", None),
                "method": getattr(gs, "mp_method", ""),
                "reason": getattr(gs, "mp_reason", ""),
            },
            "cap": {
                "cur": getattr(gs, "cap_current", None),
                "method": getattr(gs, "cap_method", ""),
                "reason": getattr(gs, "cap_reason", ""),
            },
            "battlelist": {
                "n_rows": getattr(gs, "battlelist_n_rows", None),
                "n_valid": getattr(gs, "battlelist_n_valid", None),
                "top": getattr(gs, "battlelist_top_names", None),
                "confidence": getattr(gs, "battlelist_confidence", None),
                "source": getattr(gs, "battlelist_source", ""),
                "reason": getattr(gs, "battlelist_reason", ""),
            },
            "hud_debug": getattr(gs, "hud_debug", None),
        }

        summary["items"].append(item)

        # Optional crops
        if args.save_crops or args.save_overlay:
            for name in rois_names:
                if name not in rois:
                    continue
                res = roi_to_px_result(frame_shape=(int(frame.shape[0]), int(frame.shape[1])), rois=rois, resolution=resolution, roi_def=rois[name])
                if not res.ok or res.roi is None:
                    continue
                x, y, w, h = res.roi
                crop = frame[y : y + h, x : x + w].copy()
                if args.save_crops:
                    outp = crops_dir / f"{idx:04d}_{p.stem}_{name}.png"
                    cv2.imwrite(str(outp), crop)

        # Optional overlay
        if args.save_overlay:
            ov = frame.copy()
            pad = 10
            y0 = 25
            hp = item.get("hp") if isinstance(item, dict) else None
            mp = item.get("mp") if isinstance(item, dict) else None
            cap = item.get("cap") if isinstance(item, dict) else None
            bl = item.get("battlelist") if isinstance(item, dict) else None
            hp = hp if isinstance(hp, dict) else {}
            mp = mp if isinstance(mp, dict) else {}
            cap = cap if isinstance(cap, dict) else {}
            bl = bl if isinstance(bl, dict) else {}
            lines = [
                f"{p.name}",
                f"HP: {hp.get('cur')}/{hp.get('max')} ({hp.get('pct')}) {hp.get('method')}:{hp.get('reason')}",
                f"MP: {mp.get('cur')}/{mp.get('max')} ({mp.get('pct')}) {mp.get('method')}:{mp.get('reason')}",
                f"CAP: {cap.get('cur')} {cap.get('method')}:{cap.get('reason')}",
                f"BL: n={bl.get('n_rows')} valid={bl.get('n_valid')} conf={bl.get('confidence')} {bl.get('reason')}",
            ]
            for line in lines:
                cv2.putText(ov, line, (pad, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA)
                y0 += 22

            # ROI rectangles
            for name in rois_names:
                if name not in rois:
                    continue
                res = roi_to_px_result(frame_shape=(int(frame.shape[0]), int(frame.shape[1])), rois=rois, resolution=resolution, roi_def=rois[name])
                if not res.ok or res.roi is None:
                    continue
                x, y, w, h = res.roi
                cv2.rectangle(ov, (x, y), (x + w, y + h), (255, 0, 0), 2)
                cv2.putText(ov, name, (x, max(15, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1, cv2.LINE_AA)

            outp = overlay_dir / f"{idx:04d}_{p.stem}.png"
            cv2.imwrite(str(outp), ov)

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"OK: wrote {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
