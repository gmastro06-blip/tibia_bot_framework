from __future__ import annotations

import json
import re
import sys
import argparse
from pathlib import Path

import cv2


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def _scan_region(ocr, img, *, name: str, x0: int, y0: int, x1: int, y1: int) -> list[dict]:
    region = img[y0:y1, x0:x1]
    if region is None or region.size == 0:
        return []

    # Upscale a bit for tiny HUD fonts.
    try:
        region_up = cv2.resize(region, (0, 0), fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        scale = 2.0
    except Exception:
        region_up = region
        scale = 1.0

    try:
        res = ocr.reader.readtext(region_up, detail=1, allowlist="0123456789")
    except Exception:
        res = []

    out: list[dict] = []
    for bbox, text, conf in res or []:
        s = re.sub(r"[^0-9]", "", str(text or ""))
        if len(s) < 4:
            continue
        try:
            xs = [float(p[0]) for p in bbox]
            ys = [float(p[1]) for p in bbox]
            bb = (min(xs), min(ys), max(xs), max(ys))
            # map to frame coords
            fx0 = float(x0) + (bb[0] / scale)
            fy0 = float(y0) + (bb[1] / scale)
            fx1 = float(x0) + (bb[2] / scale)
            fy1 = float(y0) + (bb[3] / scale)
            out.append(
                {
                    "region": name,
                    "digits": s,
                    "len": len(s),
                    "conf": float(conf or 0.0),
                    "bbox": [fx0, fy0, fx1, fy1],
                }
            )
        except Exception:
            continue

    return out


def main() -> None:
    _add_src_to_syspath()

    from vision.ocr import OCRProcessor

    parser = argparse.ArgumentParser(description="Scan frame for OCR digit boxes and emit a debug overlay.")
    parser.add_argument(
        "--frame",
        default="logs/coords_sanity_suggest/1768156907.494064/frame.png",
        help="Path to frame.png captured by coords_sanity_check.",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="Directory to write outputs (default: alongside --frame).",
    )
    args = parser.parse_args()

    frame_path = Path(args.frame)
    img = cv2.imread(str(frame_path))
    if img is None:
        raise SystemExit(f"No pude leer {frame_path}")

    out_dir = Path(args.out_dir).resolve() if str(args.out_dir or "").strip() else frame_path.parent

    h, w = img.shape[:2]
    print(f"frame {w}x{h}")

    ocr = OCRProcessor()

    regions = [
        ("right_top", int(w * 0.75), 0, w, int(h * 0.45)),
        ("right_mid", int(w * 0.75), int(h * 0.45), w, int(h * 0.85)),
        ("top_bar", 0, 0, w, int(h * 0.12)),
    ]

    items: list[dict] = []
    for name, x0, y0, x1, y1 in regions:
        items.extend(_scan_region(ocr, img, name=name, x0=x0, y0=y0, x1=x1, y1=y1))

    items.sort(key=lambda d: (d.get("len", 0), d.get("conf", 0.0)), reverse=True)

    print(f"found {len(items)} digit boxes with >=4 digits")
    for d in items[:25]:
        print(f"{d['region']:9s} conf={d['conf']:.2f} digits={d['digits']} bbox={tuple(int(x) for x in d['bbox'])}")

    # Optional: overlay for quick visual inspection.
    try:
        overlay = img.copy()

        # Draw OCR digit boxes.
        for d in items:
            try:
                x0, y0, x1, y1 = [int(round(float(v))) for v in d.get("bbox", [])]
                conf = float(d.get("conf", 0.0) or 0.0)
                digits = str(d.get("digits", ""))
                region = str(d.get("region", ""))
                color = (0, 255, 0) if conf >= 0.6 else (0, 200, 255) if conf >= 0.3 else (0, 0, 255)
                cv2.rectangle(overlay, (x0, y0), (x1, y1), color, 2)
                label = f"{region} {digits} {conf:.2f}"
                cv2.putText(
                    overlay,
                    label,
                    (x0, max(0, y0 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    color,
                    1,
                    cv2.LINE_AA,
                )
            except Exception:
                continue

        # If report.json exists next to the frame, draw coords ROI and expanded ROI.
        try:
            report_path = frame_path.parent / "report.json"
            if report_path.exists():
                rep = json.loads(report_path.read_text(encoding="utf-8"))
                roi_dbg = rep.get("roi_debug", {}) if isinstance(rep, dict) else {}
                for key, color in (
                    ("coords_ocr_px", (255, 0, 0)),
                    ("coords_ocr_expanded_px", (255, 0, 255)),
                ):
                    r = roi_dbg.get(key)
                    if isinstance(r, (list, tuple)) and len(r) >= 4:
                        x, y, rw, rh = [int(round(float(v))) for v in r[:4]]
                        cv2.rectangle(overlay, (x, y), (x + rw, y + rh), color, 2)
                        cv2.putText(
                            overlay,
                            key,
                            (x, max(0, y - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.5,
                            color,
                            1,
                            cv2.LINE_AA,
                        )
        except Exception:
            pass

        overlay_path = out_dir / "scan_digits_overlay.png"
        try:
            overlay_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        cv2.imwrite(str(overlay_path), overlay)
        print(f"Wrote: {overlay_path}")
    except Exception:
        pass

    out_path = out_dir / "scan_digits.json"
    out_path.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
