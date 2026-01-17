from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np


try:
	import cv2  # type: ignore
except Exception as e:  # pragma: no cover
	raise SystemExit(f"OpenCV (cv2) is required to run this tool: {e}")


@dataclass(frozen=True)
class MatchResult:
	name: str
	score: float
	x: int
	y: int
	w: int
	h: int
	scale: float
	template_path: str


def _read_image(path: str) -> np.ndarray:
	p = str(path)
	img = cv2.imread(p, cv2.IMREAD_COLOR)
	if img is None:
		raise SystemExit(f"Could not read image: {p}")
	return img


def _to_gray(img_bgr: np.ndarray) -> np.ndarray:
	if img_bgr is None:
		return img_bgr
	if len(img_bgr.shape) == 2:
		return img_bgr
	try:
		return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
	except Exception:
		return img_bgr


def _parse_ts_prefix(filename: str) -> float:
	try:
		stem = Path(filename).stem
		head = stem.split("_", 1)[0]
		return float(head)
	except Exception:
		return 0.0


def _roi_name_from_filename(filename: str) -> str:
	stem = Path(filename).stem
	# Expected: "<ts>_<roi_name>". If no ts, fallback to whole stem.
	parts = stem.split("_", 1)
	if len(parts) == 2:
		return parts[1].strip()
	return stem.strip()


def _discover_templates(
	templates_dir: str,
	*,
	roi_names: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
	base = Path(templates_dir)
	if not base.exists():
		raise SystemExit(f"Templates dir not found: {templates_dir}")

	wanted = None
	if roi_names is not None:
		wanted = {str(x).strip() for x in roi_names if str(x).strip()}
		if not wanted:
			wanted = None

	picks: Dict[str, Tuple[float, str]] = {}
	for p in base.glob("*.png"):
		if not p.is_file():
			continue
		name = _roi_name_from_filename(p.name)
		if not name:
			continue
		if wanted is not None and name not in wanted:
			continue

		ts = _parse_ts_prefix(p.name)
		cur = picks.get(name)
		if cur is None or float(ts) >= float(cur[0]):
			picks[name] = (float(ts), str(p))

	return {k: v[1] for k, v in picks.items()}


def _load_prior_rois(prior_path: str) -> Tuple[Dict[str, Dict[str, float]], Optional[List[int]]]:
	p = Path(prior_path)
	if not p.exists():
		raise SystemExit(f"Prior config not found: {prior_path}")
	obj = json.loads(p.read_text(encoding="utf-8"))
	rois = obj.get("rois_guess_norm", obj.get("rois", obj))
	if not isinstance(rois, dict):
		raise SystemExit("Prior config does not contain a ROI dict")
	src = obj.get("source_resolution", None)
	if isinstance(src, (list, tuple)) and len(src) >= 2:
		try:
			src = [int(src[0]), int(src[1])]
		except Exception:
			src = None
	else:
		src = None
	out: Dict[str, Dict[str, float]] = {}
	for k, v in rois.items():
		if not isinstance(k, str):
			continue
		if not isinstance(v, dict):
			continue
		try:
			out[k] = {
				"x": float(v.get("x")),
				"y": float(v.get("y")),
				"w": float(v.get("w")),
				"h": float(v.get("h")),
			}
		except Exception:
			continue
	return out, src


def _expected_center_px(
	rois_norm: Mapping[str, Mapping[str, float]],
	name: str,
	hw: int,
	hh: int,
) -> Optional[Tuple[float, float]]:
	r = rois_norm.get(name)
	if not isinstance(r, Mapping):
		return None
	try:
		x = float(r.get("x", 0.0))
		y = float(r.get("y", 0.0))
		w = float(r.get("w", 0.0))
		h = float(r.get("h", 0.0))
	except Exception:
		return None
	if w <= 0 or h <= 0:
		return None
	return ((x + w * 0.5) * float(hw), (y + h * 0.5) * float(hh))


def _match_one(
	*,
	haystack_gray: np.ndarray,
	name: str,
	template_path: str,
	scales: List[float],
	min_score: float,
	prior_rois_norm: Optional[Mapping[str, Mapping[str, float]]],
	prior_sigma: float,
) -> Optional[MatchResult]:
	tmpl_bgr = _read_image(template_path)
	tmpl_gray = _to_gray(tmpl_bgr)

	hh, hw = int(haystack_gray.shape[0]), int(haystack_gray.shape[1])
	best: Optional[MatchResult] = None

	expected = None
	if prior_rois_norm is not None:
		expected = _expected_center_px(prior_rois_norm, name, hw, hh)

	diag = float(np.hypot(hw, hh)) if hw > 0 and hh > 0 else 1.0
	sigma_px = max(1.0, float(prior_sigma) * diag)

	for s in scales:
		try:
			if s <= 0:
				continue
			tw0, th0 = int(tmpl_gray.shape[1]), int(tmpl_gray.shape[0])
			tw = max(1, int(round(tw0 * float(s))))
			th = max(1, int(round(th0 * float(s))))
			if tw > hw or th > hh:
				continue

			if abs(float(s) - 1.0) < 1e-6:
				rs = tmpl_gray
			else:
				rs = cv2.resize(tmpl_gray, (tw, th), interpolation=cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC)

			res = cv2.matchTemplate(haystack_gray, rs, cv2.TM_CCOEFF_NORMED)
			_minv, maxv, _minloc, maxloc = cv2.minMaxLoc(res)
			score = float(maxv)
			x, y = int(maxloc[0]), int(maxloc[1])

			# Prior penalty (optional): prefer near expected position.
			if expected is not None:
				cx = float(x + tw / 2)
				cy = float(y + th / 2)
				dx = float(cx - expected[0])
				dy = float(cy - expected[1])
				dist = float(np.hypot(dx, dy))
				try:
					penalty = float(np.exp(-0.5 * (dist / sigma_px) ** 2))
				except Exception:
					penalty = 1.0
				score = float(score) * float(penalty)

			if best is None or score > best.score:
				best = MatchResult(
					name=name,
					score=float(score),
					x=int(x),
					y=int(y),
					w=int(tw),
					h=int(th),
					scale=float(s),
					template_path=str(template_path),
				)
		except Exception:
			continue

	if best is None:
		return None
	if float(best.score) < float(min_score):
		return None
	return best


def _apply_padding(
	*,
	x: int,
	y: int,
	w: int,
	h: int,
	pad_px: int,
	pad_frac: float,
	hw: int,
	hh: int,
) -> Tuple[int, int, int, int]:
	try:
		extra_x = int(pad_px) + int(round(float(pad_frac) * float(w)))
		extra_y = int(pad_px) + int(round(float(pad_frac) * float(h)))
	except Exception:
		extra_x = int(pad_px)
		extra_y = int(pad_px)

	x0 = max(0, int(x) - extra_x)
	y0 = max(0, int(y) - extra_y)
	x1 = min(int(hw), int(x) + int(w) + extra_x)
	y1 = min(int(hh), int(y) + int(h) + extra_y)
	return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def _draw_debug(
	hay_bgr: np.ndarray,
	matches: List[MatchResult],
	*,
	out_path: str,
) -> None:
	img = hay_bgr.copy()
	for m in matches:
		x0, y0, x1, y1 = int(m.x), int(m.y), int(m.x + m.w), int(m.y + m.h)
		cv2.rectangle(img, (x0, y0), (x1, y1), (0, 220, 0), 2)
		label = f"{m.name} {m.score:.3f}"
		cv2.putText(
			img,
			label,
			(x0, max(10, y0 - 6)),
			cv2.FONT_HERSHEY_SIMPLEX,
			0.45,
			(0, 220, 0),
			1,
			cv2.LINE_AA,
		)
	out = Path(out_path)
	out.parent.mkdir(parents=True, exist_ok=True)
	cv2.imwrite(str(out), img)


def main() -> None:
	p = argparse.ArgumentParser(
		description=(
			"Auto-generate ROIs by template matching. "
			"Inputs: a full screenshot (haystack) + template crops (one per ROI). "
			"Outputs: a ROIs JSON (normalized) + an optional debug overlay."
		)
	)

	p.add_argument("--haystack", type=str, required=True, help="Full screenshot path (PNG/JPG) to search in")
	p.add_argument(
		"--templates-dir",
		type=str,
		default="",
		help=(
			"Directory containing template PNGs. Filenames can be '<ts>_<roi>.png' (replay style) "
			"or '<roi>.png'. If multiple exist for the same ROI, latest timestamp is used."
		),
	)
	p.add_argument(
		"--template",
		action="append",
		default=[],
		help="Explicit template mapping 'roi_name=path/to/template.png'. Can be repeated.",
	)
	p.add_argument(
		"--roi-names",
		type=str,
		default="",
		help="Comma-separated list of ROI names to detect (filters templates-dir discovery)",
	)
	p.add_argument(
		"--prior-config",
		type=str,
		default="",
		help="Optional prior ROIs JSON to bias matches toward expected positions (recommended)",
	)
	p.add_argument(
		"--prior-sigma",
		type=float,
		default=0.12,
		help="Prior position tolerance as fraction of image diagonal (default: 0.12)",
	)

	p.add_argument("--min-score", type=float, default=0.70, help="Minimum match score to accept")
	p.add_argument(
		"--scales",
		type=str,
		default="1.00",
		help="Comma-separated template scales to try (e.g. '0.9,1.0,1.1')",
	)
	p.add_argument("--pad-px", type=int, default=0, help="Padding in pixels to expand matched ROI bbox")
	p.add_argument(
		"--pad-frac",
		type=float,
		default=0.0,
		help="Padding as fraction of matched bbox size (e.g. 0.05 expands 5%)",
	)

	p.add_argument("--out-json", type=str, default="logs/rois_auto.json", help="Output ROIs JSON path")
	p.add_argument(
		"--out-debug",
		type=str,
		default="logs/rois_auto_debug.png",
		help="Output debug overlay path (draw rectangles + scores)",
	)

	args = p.parse_args()

	hay_path = str(args.haystack)
	hay_bgr = _read_image(hay_path)
	hay_gray = _to_gray(hay_bgr)
	hh, hw = int(hay_gray.shape[0]), int(hay_gray.shape[1])
	if hw <= 0 or hh <= 0:
		raise SystemExit("Invalid haystack image")

	# ROI filter
	roi_filter: Optional[List[str]] = None
	if str(args.roi_names or "").strip():
		roi_filter = [s.strip() for s in str(args.roi_names).split(",") if s.strip()]
		if not roi_filter:
			roi_filter = None

	# Parse scales
	scales: List[float] = []
	for s in str(args.scales or "").split(","):
		s = s.strip()
		if not s:
			continue
		try:
			scales.append(float(s))
		except Exception:
			continue
	if not scales:
		scales = [1.0]

	# Load templates from explicit mappings
	templates: Dict[str, str] = {}
	for raw in list(args.template or []):
		if "=" not in str(raw):
			raise SystemExit(f"Invalid --template (expected roi=path): {raw}")
		k, v = str(raw).split("=", 1)
		k = k.strip()
		v = v.strip()
		if not k or not v:
			raise SystemExit(f"Invalid --template: {raw}")
		templates[k] = v

	# Discover from directory (replay crops are supported)
	if str(args.templates_dir or "").strip():
		discovered = _discover_templates(str(args.templates_dir), roi_names=roi_filter)
		for k, v in discovered.items():
			templates.setdefault(k, v)

	if not templates:
		raise SystemExit("No templates provided. Use --templates-dir and/or --template roi=path")

	# Prior config
	prior_rois_norm = None
	prior_src = None
	if str(args.prior_config or "").strip():
		prior_rois_norm, prior_src = _load_prior_rois(str(args.prior_config))

	# Match all templates
	matches: List[MatchResult] = []
	for name in sorted(templates.keys()):
		tmpl = templates[name]
		res = _match_one(
			haystack_gray=hay_gray,
			name=str(name),
			template_path=str(tmpl),
			scales=list(scales),
			min_score=float(args.min_score),
			prior_rois_norm=prior_rois_norm,
			prior_sigma=float(args.prior_sigma),
		)
		if res is not None:
			matches.append(res)

	if not matches:
		raise SystemExit(
			"No matches found above threshold. Try lowering --min-score, adding more --scales, "
			"or using a raw (non-annotated) haystack screenshot."
		)

	rois_out: Dict[str, Dict[str, float]] = {}
	for m in matches:
		x0, y0, w0, h0 = _apply_padding(
			x=m.x,
			y=m.y,
			w=m.w,
			h=m.h,
			pad_px=int(args.pad_px),
			pad_frac=float(args.pad_frac),
			hw=hw,
			hh=hh,
		)
		rois_out[m.name] = {
			"x": float(x0) / float(hw),
			"y": float(y0) / float(hh),
			"w": float(w0) / float(hw),
			"h": float(h0) / float(hh),
		}

	out_obj = {
		"source_resolution": [int(hw), int(hh)],
		"rois_guess_norm": rois_out,
		"_meta": {
			"haystack": str(Path(hay_path).as_posix()),
			"templates_dir": str(Path(str(args.templates_dir)).as_posix()) if str(args.templates_dir or "").strip() else "",
			"n_templates": int(len(templates)),
			"n_matches": int(len(matches)),
			"min_score": float(args.min_score),
			"scales": [float(x) for x in scales],
			"prior_config": str(Path(str(args.prior_config)).as_posix()) if str(args.prior_config or "").strip() else "",
			"prior_sigma": float(args.prior_sigma),
			"prior_source_resolution": prior_src,
		},
		"_matches": [
			{
				"name": m.name,
				"score": float(m.score),
				"x": int(m.x),
				"y": int(m.y),
				"w": int(m.w),
				"h": int(m.h),
				"scale": float(m.scale),
				"template_path": str(Path(m.template_path).as_posix()),
			}
			for m in sorted(matches, key=lambda r: (-float(r.score), r.name))
		],
	}

	out_json = Path(str(args.out_json))
	out_json.parent.mkdir(parents=True, exist_ok=True)
	out_json.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False), encoding="utf-8")

	# Debug overlay
	out_dbg = str(args.out_debug)
	if out_dbg:
		try:
			_draw_debug(hay_bgr, matches, out_path=out_dbg)
		except Exception:
			pass

	print("== ROI auto-detect complete ==")
	print(f"haystack: {hay_path} ({hw}x{hh})")
	print(f"templates: {len(templates)} | matched: {len(matches)}")
	print(f"out_json: {out_json.as_posix()}")
	if out_dbg:
		print(f"out_debug: {Path(out_dbg).as_posix()}")

	# Helpful summary
	for m in sorted(matches, key=lambda r: (-float(r.score), r.name))[:20]:
		print(f"{m.name:>20} score={m.score:.3f} xywh=({m.x},{m.y},{m.w},{m.h}) scale={m.scale:.2f}")


if __name__ == "__main__":
	# Allow running from repo root.
	try:
		os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
	except Exception:
		pass
	main()