from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np


def _add_src_to_syspath() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


_add_src_to_syspath()

from vision.ocr import OCRProcessor

BASE = Path('logs/coords_sanity_cli2/1768156441.127796/crops')
IMGS = ['000_fail.png', '000_expanded.png', '000_expanded_scaled.png']


def main() -> None:
    ocr = OCRProcessor()

    for name in IMGS:
        p = BASE / name
        img = cv2.imread(str(p))
        print(f"\n{name} exists={p.exists()} shape={None if img is None else img.shape}")
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        print(f"gray min={int(g.min())} max={int(g.max())} std={float(g.std()):.2f}")

        try:
            res = ocr.reader.readtext(img, detail=0, allowlist="0123456789XYZxyz:,- ")
            print("readtext raw:", res)
        except Exception as e:
            print("readtext raw err:", e)

        try:
            proc = ocr.preprocess_image(img)
            res2 = ocr.reader.readtext(proc, detail=0, allowlist="0123456789XYZxyz:,- ")
            print("readtext preproc:", res2)
        except Exception as e:
            print("readtext preproc err:", e)


if __name__ == '__main__':
    main()
