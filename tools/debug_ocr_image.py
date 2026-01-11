from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


def main() -> int:
    _add_src_to_syspath()

    from vision.ocr import OCRProcessor

    p = argparse.ArgumentParser(description="Debug OCR on a single image file (raw + preprocessed).")
    p.add_argument("image", type=str, help="Path to an image (png/jpg)")
    p.add_argument(
        "--allowlist",
        type=str,
        default="0123456789XYZxyz:,- /",
        help="EasyOCR allowlist (default allows digits + coords separators).",
    )
    args = p.parse_args()

    img_path = Path(args.image)
    img = cv2.imread(str(img_path))
    if img is None:
        print(f"FAIL: could not read {img_path}")
        return 2

    h, w = img.shape[:2]
    print(f"image={img_path} size={w}x{h}")

    ocr = OCRProcessor()

    def dump(label: str, mat) -> None:
        try:
            res = ocr.reader.readtext(mat, detail=1, allowlist=args.allowlist)
        except Exception as e:
            print(f"{label}: readtext error: {e}")
            res = []

        print(f"{label}: {len(res)} boxes")
        for bbox, text, conf in res[:20]:
            try:
                xs = [float(p[0]) for p in bbox]
                ys = [float(p[1]) for p in bbox]
                bb = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
            except Exception:
                bb = (0, 0, 0, 0)
            print(f"  conf={float(conf or 0.0):.2f} bbox={bb} text={str(text).strip()}")

        # Also show the joined string (what extract_coords effectively parses).
        try:
            joined = " ".join(str(t).strip() for _, t, _ in res if str(t).strip())
        except Exception:
            joined = ""
        if joined:
            print(f"{label}: joined='{joined}'")

    dump("raw", img)

    try:
        processed = ocr.preprocess_image(img)
        dump("preprocessed", processed)
    except Exception as e:
        print(f"preprocessed: error: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
