import os
from pathlib import Path

from roboflow import Roboflow


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(
            f"Missing env var {name}. Set it and re-run. "
            f"Example (PowerShell): $env:{name}='YOUR_KEY'"
        )
    return value


def _download(*, rf: Roboflow, workspace: str, project: str, version: int, fmt: str, location: Path) -> None:
    # IMPORTANT: Roboflow SDK skips downloading if the *location path exists*.
    # Therefore we download into a unique, per-dataset subfolder that doesn't exist yet.
    location.mkdir(parents=True, exist_ok=True)

    force = os.getenv("ROBOFLOW_FORCE_DOWNLOAD", "").strip().lower() in {"1", "true", "yes"}

    ws = rf.workspace(workspace)
    proj = ws.project(project)

    requested_version = version
    actual_version = requested_version

    # Resolve missing versions up-front (so we name the target folder correctly).
    try:
        proj.version(requested_version)
    except RuntimeError as e:
        msg = str(e)
        if "Version number" in msg and "is not found" in msg:
            versions = proj.versions()
            available = sorted({int(getattr(v, "version")) for v in versions if hasattr(v, "version")})
            if not available:
                raise
            actual_version = available[-1]
        else:
            raise

    target_dir = location / f"{project}_v{actual_version}_{fmt}"

    # If already downloaded, skip unless forced.
    if not force and (target_dir.exists() and any(target_dir.iterdir())):
        print(f"\n=== Skipping {workspace}/{project} (already downloaded) ===")
        print(f"Location: {target_dir}")
        print("Set ROBOFLOW_FORCE_DOWNLOAD=1 to re-download.")
        return

    print(f"\n=== Downloading {workspace}/{project} v{version} -> {fmt} ===")
    print(f"Location: {location}")

    if actual_version != requested_version:
        print(f"Requested v{requested_version} not found; falling back to latest v{actual_version}.")

    # Roboflow will create target_dir; it must not exist unless overwrite=True.
    ds = proj.version(actual_version).download(fmt, location=str(target_dir), overwrite=force)
    # roboflow SDK returns a Dataset object with a few helpful attributes depending on version
    print("Done.")
    try:
        print(f"Dataset name: {getattr(ds, 'name', None)}")
        print(f"Dataset location: {getattr(ds, 'location', None)}")
    except Exception:
        pass


def main() -> None:
    api_key = _require_env("ROBOFLOW_API_KEY")
    rf = Roboflow(api_key=api_key)

    repo_root = Path(__file__).resolve().parents[1]
    base = repo_root / "datasets"

    # 1) tibia-loot (112 imgs items/loot)
    _download(
        rf=rf,
        workspace="gameplay-anon-15nly",
        project="tibia-loot",
        version=1,
        fmt="yolov8",
        location=base / "temp_loot",
    )

    # 2) tibia-creatures (67 imgs orcs/monstruos)
    _download(
        rf=rf,
        workspace="luciano-virmes-uvplt",
        project="tibia-creatures",
        version=1,
        fmt="yolov8",
        location=base / "temp_creatures",
    )

    # 3) tibia (41 imgs orcs, shaman)
    _download(
        rf=rf,
        workspace="tibia-nfxv8",
        project="tibia-uhokq",
        version=1,
        fmt="yolov8",
        location=base / "temp_tibia",
    )

    # 4) hp-mp bars
    _download(
        rf=rf,
        workspace="levelup-12nnc",
        project="hp-f3dd6",
        version=1,
        fmt="yolov8",
        location=base / "temp_hpmp",
    )
