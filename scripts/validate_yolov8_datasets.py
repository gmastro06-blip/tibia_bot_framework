from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import random
import shutil
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@dataclass
class YoloDatasetSpec:
    yaml_path: Path
    root_dir: Path
    names: List[str]
    nc: Optional[int]
    train_images: Optional[Path]
    val_images: Optional[Path]
    test_images: Optional[Path]


def _write_local_yaml(spec: YoloDatasetSpec) -> Path:
    """Writes a Ultralytics-friendly yaml next to data.yaml with stable relative paths."""
    yaml_out = spec.yaml_path.parent / "data.local.yaml"

    # Compute relative paths from yaml folder.
    def rel(p: Optional[Path]) -> Optional[str]:
        if p is None:
            return None
        try:
            return str(p.relative_to(spec.yaml_path.parent)).replace("\\", "/")
        except Exception:
            return str(p).replace("\\", "/")

    train = rel(spec.train_images)
    val = rel(spec.val_images) if spec.val_images and spec.val_images.exists() else train
    test = rel(spec.test_images) if spec.test_images and spec.test_images.exists() else None

    lines: List[str] = []
    lines.append(f"nc: {len(spec.names) if spec.names else (spec.nc or 0)}")
    lines.append("names:")
    for n in (spec.names or []):
        lines.append(f"- {n}")
    if train:
        lines.append(f"train: {train}")
    if val:
        lines.append(f"val: {val}")
    if test:
        lines.append(f"test: {test}")

    yaml_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return yaml_out


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if (s.startswith("\"") and s.endswith("\"")) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    return s


def _read_simple_data_yaml(path: Path) -> Dict[str, object]:
    """Minimal YAML reader for Roboflow/Ultralytics data.yaml.

    Supports keys: names (list), nc (int), train/val/test (str).
    Avoids extra dependencies (PyYAML).
    """

    names: List[str] = []
    nc: Optional[int] = None
    train: Optional[str] = None
    val: Optional[str] = None
    test: Optional[str] = None

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            i += 1
            continue

        if line.strip() == "names:" or line.strip().startswith("names:"):
            # Two formats: 'names:' then '- x' lines, or 'names: [a,b]'.
            if ":" in line and line.strip() != "names:":
                # Inline list case
                after = line.split(":", 1)[1].strip()
                after = after.strip("[]")
                if after:
                    parts = [p.strip() for p in after.split(",")]
                    names.extend([_strip_quotes(p) for p in parts if p])
                i += 1
                continue

            # Multiline list case
            i += 1
            while i < len(lines):
                raw2 = lines[i]
                line2 = raw2.split("#", 1)[0].strip()
                if not line2:
                    i += 1
                    continue
                if not line2.startswith("-"):
                    break
                names.append(_strip_quotes(line2[1:].strip()))
                i += 1
            continue

        if line.strip().startswith("nc:"):
            value = line.split(":", 1)[1].strip()
            try:
                nc = int(value)
            except Exception:
                nc = None
            i += 1
            continue

        for key in ("train", "val", "test"):
            if line.strip().startswith(f"{key}:"):
                value = _strip_quotes(line.split(":", 1)[1].strip())
                if key == "train":
                    train = value
                elif key == "val":
                    val = value
                else:
                    test = value
                break
        i += 1

    out: Dict[str, object] = {"names": names, "nc": nc, "train": train, "val": val, "test": test}
    return out


def _resolve_images_dir(yaml_path: Path, rel: Optional[str]) -> Optional[Path]:
    if not rel:
        return None
    # Ultralytics resolves relative paths from the yaml file location.
    p = Path(rel)
    if p.is_absolute():
        return p

    resolved = (yaml_path.parent / p).resolve()
    if resolved.exists():
        return resolved

    # Roboflow exports sometimes write train/val/test folders next to data.yaml,
    # but keep paths like ../train/images. If that doesn't exist, try stripping leading "../".
    if rel.startswith("../"):
        alt = (yaml_path.parent / rel[3:]).resolve()
        if alt.exists():
            return alt

    return resolved


def _find_data_yaml(dataset_root: Path) -> Path:
    if dataset_root.is_file() and dataset_root.name.lower().endswith(".yaml"):
        return dataset_root

    direct = dataset_root / "data.yaml"
    if direct.exists():
        return direct

    # Roboflow downloads typically: <root>/<project>_vX_yolov8/data.yaml
    candidates = list(dataset_root.glob("**/data.yaml"))
    if not candidates:
        raise FileNotFoundError(f"No data.yaml found under {dataset_root}")

    # Prefer shallowest
    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return candidates[0]


def _iter_images(images_dir: Path) -> Iterable[Path]:
    if not images_dir.exists():
        return
    for p in images_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            yield p


def _labels_dir_for_images_dir(images_dir: Path) -> Path:
    # Common YOLO layout: ../labels relative to ../images
    # train/images -> train/labels
    return images_dir.parent / "labels"


def _ensure_valid_split_from_train(
    *,
    dataset_dir: Path,
    train_images_dir: Path,
    val_fraction: float,
    seed: int,
) -> Tuple[Path, int]:
    """Create valid/images + valid/labels by copying a subset from train, if missing.

    Returns (val_images_dir, copied_count)
    """
    valid_images = dataset_dir / "valid" / "images"
    valid_labels = dataset_dir / "valid" / "labels"
    if valid_images.exists() and any(valid_images.iterdir()):
        return valid_images, 0

    train_labels_dir = _labels_dir_for_images_dir(train_images_dir)
    images = sorted(list(_iter_images(train_images_dir)))
    if not images:
        return valid_images, 0

    rnd = random.Random(seed)
    k = int(round(len(images) * val_fraction))
    k = max(1, min(k, len(images)))
    chosen = rnd.sample(images, k)

    valid_images.mkdir(parents=True, exist_ok=True)
    valid_labels.mkdir(parents=True, exist_ok=True)

    copied = 0
    for img in chosen:
        dst_img = valid_images / img.name
        shutil.copy2(img, dst_img)
        lbl = train_labels_dir / f"{img.stem}.txt"
        if lbl.exists():
            shutil.copy2(lbl, valid_labels / lbl.name)
        copied += 1

    return valid_images, copied


@dataclass
class SplitStats:
    images: int
    labels: int
    missing_labels: int
    orphan_labels: int


def _compute_split_stats(images_dir: Path) -> SplitStats:
    labels_dir = _labels_dir_for_images_dir(images_dir)

    image_files = list(_iter_images(images_dir))
    image_stems = {p.stem for p in image_files}

    label_files: List[Path] = []
    if labels_dir.exists():
        label_files = list(labels_dir.rglob("*.txt"))
    label_stems = {p.stem for p in label_files}

    missing_labels = len(image_stems - label_stems)
    orphan_labels = len(label_stems - image_stems)

    return SplitStats(
        images=len(image_files),
        labels=len(label_files),
        missing_labels=missing_labels,
        orphan_labels=orphan_labels,
    )


def load_dataset(dataset_root: Path) -> YoloDatasetSpec:
    yaml_path = _find_data_yaml(dataset_root)
    data = _read_simple_data_yaml(yaml_path)

    raw_names = data.get("names")
    names: List[str] = []
    if isinstance(raw_names, (list, tuple)):
        names = [str(x) for x in raw_names]
    elif isinstance(raw_names, dict):
        # Some YOLO datasets use a mapping like {0: "person", 1: "car"}
        try:
            keys = list(raw_names.keys())
            keys.sort(key=lambda k: int(k) if isinstance(k, (int, str)) and str(k).isdigit() else str(k))
            names = [str(raw_names[k]) for k in keys]
        except Exception:
            names = [str(v) for v in raw_names.values()]

    nc = data.get("nc")
    nc_int: Optional[int]
    try:
        if isinstance(nc, (int, str)):
            nc_int = int(nc)
        else:
            nc_int = None
    except Exception:
        nc_int = None

    train_rel = data.get("train")
    val_rel = data.get("val")
    test_rel = data.get("test")

    train_images = _resolve_images_dir(yaml_path, train_rel if isinstance(train_rel, str) else None)
    val_images = _resolve_images_dir(yaml_path, val_rel if isinstance(val_rel, str) else None)
    test_images = _resolve_images_dir(yaml_path, test_rel if isinstance(test_rel, str) else None)

    return YoloDatasetSpec(
        yaml_path=yaml_path,
        root_dir=yaml_path.parent,
        names=names,
        nc=nc_int,
        train_images=train_images,
        val_images=val_images,
        test_images=test_images,
    )


def _print_dataset_report(
    spec: YoloDatasetSpec,
    *,
    emit_local_yaml: bool,
    autosplit_missing_val: bool,
    val_fraction: float,
    seed: int,
) -> int:
    print("\n" + "=" * 80)
    print(f"Dataset: {spec.root_dir.name}")
    print(f"data.yaml: {spec.yaml_path}")
    print(f"classes (nc={spec.nc}): {spec.names}")

    exit_code = 0

    if spec.nc is not None and spec.names and spec.nc != len(spec.names):
        print(f"WARNING: nc={spec.nc} but names has {len(spec.names)} entries")
        exit_code = 2

    # Optional conversion: if val is missing but train exists, create val from train.
    if autosplit_missing_val and spec.train_images is not None:
        val_dir = spec.val_images
        if val_dir is None or not val_dir.exists():
            created_val_dir, copied = _ensure_valid_split_from_train(
                dataset_dir=spec.yaml_path.parent,
                train_images_dir=spec.train_images,
                val_fraction=val_fraction,
                seed=seed,
            )
            if copied > 0:
                print(f"\n[convert] created valid split from train: {copied} images")
            spec.val_images = created_val_dir

    for split_name, images_dir in (("train", spec.train_images), ("val", spec.val_images), ("test", spec.test_images)):
        if images_dir is None:
            continue

        print(f"\n[{split_name}] images_dir: {images_dir}")
        if not images_dir.exists():
            if split_name == "train":
                print("  ERROR: images_dir does not exist")
                exit_code = 2
            else:
                print("  WARNING: images_dir does not exist")
            continue

        stats = _compute_split_stats(images_dir)
        print(f"  images: {stats.images}")
        print(f"  labels: {stats.labels}")
        print(f"  missing_labels (image without .txt): {stats.missing_labels}")
        print(f"  orphan_labels (label without image): {stats.orphan_labels}")

        if stats.images == 0:
            print("  WARNING: no images found")
            exit_code = 2
        if stats.missing_labels > 0 or stats.orphan_labels > 0:
            exit_code = 2

    return exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Roboflow-exported YOLOv8 datasets")
    parser.add_argument(
        "datasets",
        nargs="*",
        help="Dataset folder(s) or data.yaml path(s). If empty, defaults to the repo temp_* downloads.",
    )

    parser.add_argument(
        "--emit-local-yaml",
        action="store_true",
        help="Write data.local.yaml next to each data.yaml with stable relative paths.",
    )
    parser.add_argument(
        "--auto-split-missing-val",
        action="store_true",
        help="If valid/images is missing, copy a subset from train/images to create a validation split.",
    )
    parser.add_argument("--val-fraction", type=float, default=0.2, help="Fraction of train to copy into valid when auto-splitting")
    parser.add_argument("--seed", type=int, default=1337, help="Random seed used for auto-splitting")

    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    default_roots = [
        repo_root / "datasets" / "temp_loot",
        repo_root / "datasets" / "temp_creatures",
        repo_root / "datasets" / "temp_tibia",
    ]

    targets = [Path(p) for p in args.datasets] if args.datasets else default_roots

    worst = 0
    for t in targets:
        try:
            spec = load_dataset(t)
            rc = _print_dataset_report(
                spec,
                emit_local_yaml=args.emit_local_yaml,
                autosplit_missing_val=args.auto_split_missing_val,
                val_fraction=args.val_fraction,
                seed=args.seed,
            )

            if args.emit_local_yaml:
                out = _write_local_yaml(spec)
                print(f"\n[emit] wrote {out}")
            worst = max(worst, rc)
        except Exception as e:
            print("\n" + "=" * 80)
            print(f"Dataset: {t}")
            print(f"ERROR: {e}")
            worst = max(worst, 2)

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
