from __future__ import annotations

import argparse
import hashlib
import re
import csv
import json
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_RE_REPLAY_PNG = re.compile(r"^(?P<ts>\d+(?:\.\d+)?)_(?P<roi>.+)\.png$", re.IGNORECASE)


def _parse_replay_name(path: Path) -> tuple[float, str] | None:
    """Parse replay crop file names like: <ts>_<roi>.png.

    Returns (ts, roi) or None if it doesn't match.
    """

    m = _RE_REPLAY_PNG.match(path.name)
    if not m:
        return None
    try:
        ts = float(m.group("ts"))
        roi = str(m.group("roi")).strip()
        if not roi:
            return None
        return ts, roi
    except Exception:
        return None


def _read_image(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"No pude leer: {path}")
    return img


def _common_hw(a: np.ndarray, b: np.ndarray) -> tuple[int, int]:
    try:
        h = min(int(a.shape[0]), int(b.shape[0]))
        w = min(int(a.shape[1]), int(b.shape[1]))
        return h, w
    except Exception:
        return 0, 0


def _compare_pair(prev_img: np.ndarray, cur_img: np.ndarray) -> tuple[float, float, float, float]:
    """Return (mean_abs_diff, shift_x, shift_y, resp)."""

    h, w = _common_hw(prev_img, cur_img)
    if h <= 0 or w <= 0:
        return float("nan"), float("nan"), float("nan"), float("nan")

    a32 = np.ascontiguousarray(prev_img[:h, :w], dtype=np.float32)
    b32 = np.ascontiguousarray(cur_img[:h, :w], dtype=np.float32)
    mean_abs_diff = float(np.mean(np.abs(a32 - b32)))

    g1 = np.ascontiguousarray(cv2.cvtColor(prev_img[:h, :w], cv2.COLOR_BGR2GRAY), dtype=np.float32)
    g2 = np.ascontiguousarray(cv2.cvtColor(cur_img[:h, :w], cv2.COLOR_BGR2GRAY), dtype=np.float32)
    (shift_x, shift_y), resp = cv2.phaseCorrelate(cast(Any, g1), cast(Any, g2))
    return mean_abs_diff, float(shift_x), float(shift_y), float(resp)


def _summarize(vals: list[float]) -> tuple[float, float, float]:
    """Return (min, mean, max) ignoring NaNs."""

    xs = [float(x) for x in vals if x == x]  # NaN check
    if not xs:
        return float("nan"), float("nan"), float("nan")
    return float(min(xs)), float(sum(xs) / len(xs)), float(max(xs))


def _safe_mkdir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def main() -> None:
    p = argparse.ArgumentParser(description="Compara una serie de PNGs: hash, diff y phase correlation.")
    p.add_argument("dir", type=str, help="Directorio con PNGs (p.ej. logs/minimap_motion_crops/<ts>)")
    p.add_argument("--pattern", type=str, default="*.png", help="Glob pattern (default: *.png)")
    p.add_argument(
        "--roi",
        type=str,
        default="",
        help="Filtra por ROI (requiere nombres tipo '<ts>_<roi>.png'). Ej: minimap_content",
    )
    p.add_argument(
        "--group-by-roi",
        action="store_true",
        help="Agrupa por ROI usando nombres '<ts>_<roi>.png' y compara dentro de cada ROI.",
    )
    p.add_argument(
        "--max",
        type=int,
        default=0,
        help="Máximo número de archivos a procesar (0 = todos)",
    )
    p.add_argument(
        "--show",
        type=int,
        default=12,
        help="Cuántas diffs imprimir por grupo (0 = solo resumen)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="No imprime la lista de archivos (md5/shape); solo imprime diffs/resúmenes.",
    )
    p.add_argument(
        "--out-csv",
        type=str,
        default="",
        help="Exporta métricas a CSV (pairwise diffs). Ej: logs/compare_crops.csv",
    )
    p.add_argument(
        "--out-json",
        type=str,
        default="",
        help="Exporta resumen por ROI a JSON. Ej: logs/compare_crops_summary.json",
    )
    p.add_argument(
        "--static-threshold",
        type=float,
        default=0.0,
        help="Marca ROI como 'static' si diff_mean <= threshold (solo group-by-roi). Default 0.0.",
    )
    args = p.parse_args()

    base = Path(args.dir)
    if not base.exists() or not base.is_dir():
        raise SystemExit(f"No existe dir: {base}")

    paths = sorted(base.glob(args.pattern))
    if args.max and args.max > 0:
        paths = paths[: int(args.max)]
    if not paths:
        raise SystemExit(f"No hay archivos con pattern={args.pattern} en {base}")

    # Replay-aware mode: group by ROI and compare meaningful sequences.
    if args.group_by_roi or (args.roi.strip() != ""):
        wanted_roi = args.roi.strip()
        groups: dict[str, list[tuple[float, Path]]] = {}
        skipped = 0
        for path in paths:
            parsed = _parse_replay_name(path)
            if parsed is None:
                skipped += 1
                continue
            ts, roi = parsed
            if wanted_roi and roi != wanted_roi:
                continue
            groups.setdefault(roi, []).append((ts, path))

        if not groups:
            raise SystemExit(
                "No encontré archivos con formato '<ts>_<roi>.png'. "
                "Prueba sin --group-by-roi o ajusta --pattern/--roi."
            )

        roi_names = sorted(groups.keys())
        print(f"groups={len(roi_names)} skipped_non_replay={skipped}")

        # Optional exports
        out_csv: Path | None = Path(args.out_csv).resolve() if args.out_csv.strip() else None
        out_json: Path | None = Path(args.out_json).resolve() if args.out_json.strip() else None
        csv_writer = None
        csv_fh = None

        if out_csv is not None:
            _safe_mkdir(out_csv.parent)
            csv_fh = out_csv.open("w", newline="", encoding="utf-8")
            csv_writer = csv.DictWriter(
                csv_fh,
                fieldnames=[
                    "roi",
                    "i",
                    "prev_ts",
                    "ts",
                    "dt_s",
                    "mean_abs_diff",
                    "shift_x",
                    "shift_y",
                    "resp",
                    "cropped",
                    "prev_name",
                    "name",
                ],
            )
            csv_writer.writeheader()

        summaries: dict[str, dict[str, Any]] = {}

        for roi in roi_names:
            items = sorted(groups[roi], key=lambda t: float(t[0]))
            if args.max and args.max > 0:
                items = items[: int(args.max)]

            print(f"\n== ROI: {roi} (n={len(items)}) ==")
            if len(items) < 2:
                continue

            diffs: list[float] = []
            resps: list[float] = []
            shift_manh: list[float] = []
            identical = 0
            n_cropped = 0

            prev_ts, prev_path = items[0]
            prev_img = _read_image(prev_path)
            if not args.quiet:
                print(f"{prev_path.name} md5={_md5(prev_path)} shape={tuple(int(x) for x in prev_img.shape)}")

            shown = 0
            for i in range(1, len(items)):
                ts, path = items[i]
                cur_img = _read_image(path)
                if not args.quiet:
                    print(f"{path.name} md5={_md5(path)} shape={tuple(int(x) for x in cur_img.shape)}")

                mad, sx, sy, resp = _compare_pair(prev_img, cur_img)
                diffs.append(float(mad))
                resps.append(float(resp))
                shift_manh.append(abs(float(sx)) + abs(float(sy)))
                if mad == 0.0:
                    identical += 1

                cropped = prev_img.shape != cur_img.shape
                if cropped:
                    n_cropped += 1

                if csv_writer is not None:
                    try:
                        csv_writer.writerow(
                            {
                                "roi": roi,
                                "i": int(i),
                                "prev_ts": float(prev_ts),
                                "ts": float(ts),
                                "dt_s": float(ts - prev_ts),
                                "mean_abs_diff": float(mad),
                                "shift_x": float(sx),
                                "shift_y": float(sy),
                                "resp": float(resp),
                                "cropped": int(1 if cropped else 0),
                                "prev_name": str(prev_path.name),
                                "name": str(path.name),
                            }
                        )
                    except Exception:
                        pass

                if args.show and args.show > 0 and shown < int(args.show):
                    h, w = _common_hw(prev_img, cur_img)
                    note = "" if prev_img.shape == cur_img.shape else f" (cropped -> {h}x{w})"
                    print(
                        f"diff[{i:02d}] dt={float(ts - prev_ts):.3f}s mean_abs_diff={float(mad):.6f} "
                        f"phase_shift=({float(sx):.3f},{float(sy):.3f}) resp={float(resp):.6f}{note}"
                    )
                    shown += 1

                prev_img = cur_img
                prev_ts, prev_path = ts, path

            dmin, dmean, dmax = _summarize(diffs)
            rmin, rmean, rmax = _summarize(resps)
            smin, smean, smax = _summarize(shift_manh)

            static = False
            try:
                static = bool(dmean == dmean and float(dmean) <= float(args.static_threshold))
            except Exception:
                static = False

            print(
                f"summary diffs(min/mean/max)={dmin:.6f}/{dmean:.6f}/{dmax:.6f} "
                f"resp(min/mean/max)={rmin:.6f}/{rmean:.6f}/{rmax:.6f} "
                f"|shift|_1(min/mean/max)={smin:.3f}/{smean:.3f}/{smax:.3f} "
                f"identical_pairs={identical}/{max(0, len(items)-1)}"
                + (" static=YES" if static else "")
            )

            summaries[roi] = {
                "roi": roi,
                "n": int(len(items)),
                "pairs": int(max(0, len(items) - 1)),
                "diff_min": float(dmin),
                "diff_mean": float(dmean),
                "diff_max": float(dmax),
                "resp_min": float(rmin),
                "resp_mean": float(rmean),
                "resp_max": float(rmax),
                "shift_l1_min": float(smin),
                "shift_l1_mean": float(smean),
                "shift_l1_max": float(smax),
                "identical_pairs": int(identical),
                "cropped_pairs": int(n_cropped),
                "static": bool(static),
                "static_threshold": float(args.static_threshold),
            }

        if csv_fh is not None:
            try:
                csv_fh.close()
            except Exception:
                pass

        if out_json is not None:
            _safe_mkdir(out_json.parent)
            try:
                out_json.write_text(
                    json.dumps(
                        {
                            "dir": str(base),
                            "pattern": str(args.pattern),
                            "roi_filter": str(args.roi or ""),
                            "max": int(args.max or 0),
                            "static_threshold": float(args.static_threshold),
                            "summaries": summaries,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(f"\nWrote JSON: {out_json}")
            except Exception as e:
                print(f"\nNo pude escribir JSON ({out_json}): {e}")

        return

    # Fallback: plain sequential compare in sorted order (works for arbitrary PNG names).
    print(f"n={len(paths)}")

    prev_img_seq: np.ndarray | None = None
    prev_name_seq = ""
    prev_shape_seq: tuple[int, ...] | None = None
    for idx, path in enumerate(paths):
        img = _read_image(path)
        shape = tuple(int(x) for x in img.shape)
        if not args.quiet:
            md5 = _md5(path)
            print(f"{path.name} md5={md5} shape={shape}")

        if prev_img_seq is None:
            prev_img_seq = img
            prev_name_seq = path.name
            prev_shape_seq = shape
            continue

        mad, sx, sy, resp = _compare_pair(prev_img_seq, img)
        h, w = _common_hw(prev_img_seq, img)
        if not (mad == mad):
            print(
                f"diff[{idx:02d}] SKIP incompatible shapes prev={prev_shape_seq} cur={shape} ({prev_name_seq} -> {path.name})"
            )
        else:
            note = "" if prev_shape_seq == shape else f" (cropped {prev_shape_seq} vs {shape} -> {h}x{w})"
            if args.show and args.show > 0:
                print(
                    f"diff[{idx:02d}] mean_abs_diff={float(mad):.6f} phase_shift=({float(sx):.3f},{float(sy):.3f}) resp={float(resp):.6f}{note}"
                )

        prev_img_seq = img
        prev_name_seq = path.name
        prev_shape_seq = shape


if __name__ == "__main__":
    main()
