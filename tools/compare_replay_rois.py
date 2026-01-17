from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


def _parse_ts(name: str) -> float | None:
    try:
        # 123.456789_ring_slot.png -> 123.456789
        stem = Path(name).stem
        head = stem.split("_", 1)[0]
        return float(head)
    except Exception:
        return None


def _find_latest_file(rois_dir: Path, roi_name: str) -> Path | None:
    try:
        candidates = list(rois_dir.glob(f"*_{roi_name}.png"))
    except Exception:
        candidates = []

    best: tuple[float, Path] | None = None
    for p in candidates:
        ts = _parse_ts(p.name)
        if ts is None:
            continue
        if best is None or ts > best[0]:
            best = (ts, p)
    return best[1] if best else None


def _try_load_image(path: Path):
    # Returns (img_rgb, backend) where img_rgb is uint8 HxWx3.
    try:
        import cv2

        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            return None, "cv2"
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return rgb, "cv2"
    except Exception:
        pass

    try:
        from PIL import Image

        im = Image.open(path).convert("RGB")
        import numpy as np

        return np.array(im), "pil"
    except Exception:
        return None, "none"


def _ensure_uint8_rgb(img):
    try:
        import numpy as np

        if img is None:
            return None
        if not isinstance(img, np.ndarray):
            return None
        if img.dtype != np.uint8:
            img = img.astype("uint8", copy=False)
        if img.ndim == 2:
            img = np.stack([img, img, img], axis=-1)
        if img.ndim != 3 or img.shape[2] != 3:
            return None
        return img
    except Exception:
        return None


def _resize_nearest(img, *, scale: int):
    if scale <= 1:
        return img
    try:
        import numpy as np

        return np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)
    except Exception:
        return img


def _pad_to_height(img, h: int, *, pad_value: int = 18):
    try:
        import numpy as np

        if img.shape[0] == h:
            return img
        if img.shape[0] > h:
            return img[:h, :, :]
        pad = np.full((h - img.shape[0], img.shape[1], 3), pad_value, dtype=img.dtype)
        return np.concatenate([img, pad], axis=0)
    except Exception:
        return img


def _pad_to_width(img, w: int, *, pad_value: int = 18):
    try:
        import numpy as np

        if img.shape[1] == w:
            return img
        if img.shape[1] > w:
            return img[:, :w, :]
        pad = np.full((img.shape[0], w - img.shape[1], 3), pad_value, dtype=img.dtype)
        return np.concatenate([img, pad], axis=1)
    except Exception:
        return img


def _put_text(img, text: str) -> None:
    # Best-effort overlay. No-op if drawing libs unavailable.
    try:
        import cv2

        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        cv2.putText(
            bgr,
            text,
            (6, 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img[:, :, :] = rgb
        return
    except Exception:
        pass

    try:
        from PIL import Image, ImageDraw, ImageFont

        pil = Image.fromarray(img)
        draw = ImageDraw.Draw(pil)
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
        draw.text((6, 4), text, fill=(255, 255, 255), font=font)
        import numpy as np

        out = np.array(pil)
        img[:, :, :] = out
    except Exception:
        return


def _hstack(imgs: Iterable, *, pad_value: int = 18):
    imgs = [i for i in imgs if i is not None]
    if not imgs:
        return None
    try:
        import numpy as np

        h = max(i.shape[0] for i in imgs)
        norm = [_pad_to_height(i, h, pad_value=pad_value) for i in imgs]
        return np.concatenate(norm, axis=1)
    except Exception:
        return None


def _vstack(imgs: Iterable, *, pad_value: int = 18):
    imgs = [i for i in imgs if i is not None]
    if not imgs:
        return None
    try:
        import numpy as np

        w = max(i.shape[1] for i in imgs)
        norm = [_pad_to_width(i, w, pad_value=pad_value) for i in imgs]
        return np.concatenate(norm, axis=0)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-rois-dir", required=True)
    ap.add_argument("--b-rois-dir", required=True)
    ap.add_argument("--out", default="logs/replay_compare/compare_rois.png")
    ap.add_argument(
        "--names",
        default="ring_slot,amulet_slot,hp_top_ocr,hpmp_top_strip,hp_low_bar,mp_top_ocr,mp_low_bar,hungry_icon,states_icons",
        help="Comma-separated roi png suffixes to include",
    )
    ap.add_argument("--scale", type=int, default=6, help="Nearest-neighbor scale for readability")
    args = ap.parse_args()

    a_dir = Path(args.a_rois_dir)
    b_dir = Path(args.b_rois_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    names = [s.strip() for s in str(args.names).split(",") if s.strip()]

    rows = []
    for name in names:
        a_file = _find_latest_file(a_dir, name)
        b_file = _find_latest_file(b_dir, name)

        a_img = None
        b_img = None

        if a_file is not None:
            a_raw, _ = _try_load_image(a_file)
            a_img = _ensure_uint8_rgb(a_raw)
        if b_file is not None:
            b_raw, _ = _try_load_image(b_file)
            b_img = _ensure_uint8_rgb(b_raw)

        if a_img is None and b_img is None:
            continue

        if a_img is not None:
            a_img = _resize_nearest(a_img, scale=int(args.scale))
            _put_text(a_img, f"A {name} | {a_file.name if a_file else ''}")
        if b_img is not None:
            b_img = _resize_nearest(b_img, scale=int(args.scale))
            _put_text(b_img, f"B {name} | {b_file.name if b_file else ''}")

        row = _hstack([a_img, b_img])
        if row is not None:
            rows.append(row)

    out = _vstack(rows)
    if out is None:
        print("No images found to compare.")
        return 2

    # Save
    try:
        import cv2

        bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        ok = cv2.imwrite(str(out_path), bgr)
        if not ok:
            raise RuntimeError("cv2.imwrite returned False")
    except Exception:
        try:
            from PIL import Image

            Image.fromarray(out).save(out_path)
        except Exception as e:
            print(f"Failed to save output: {e}")
            return 3

    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
