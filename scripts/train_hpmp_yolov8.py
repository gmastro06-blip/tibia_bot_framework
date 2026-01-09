from __future__ import annotations

from pathlib import Path
import os


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    data = repo_root / "datasets" / "temp_hpmp" / "hp-f3dd6_v1_yolov8" / "data.local.yaml"
    if not data.exists():
        raise SystemExit(f"data yaml not found: {data}")

    from ultralytics import YOLO  # type: ignore[attr-defined]

    # Pick device automatically (cuda if available AND torchvision NMS works on CUDA).
    device: str | int = "cpu"
    try:
        import torch
        if torch.cuda.is_available():
            try:
                import torchvision

                boxes = torch.tensor([[0.0, 0.0, 10.0, 10.0]], device="cuda")
                scores = torch.tensor([0.9], device="cuda")
                _ = torchvision.ops.nms(boxes, scores, 0.5)
                device = 0
            except Exception as e:
                # Common on Windows: torchvision NMS failed ({e}); falling back to CPU.
                print(f"CUDA available but torchvision NMS failed ({e}); falling back to CPU.")
                device = "cpu"
    except Exception:
        device = "cpu"

    epochs = int(os.getenv("EPOCHS", "50"))
    imgsz = int(os.getenv("IMGSZ", "640"))
    batch = int(os.getenv("BATCH", "-1"))

    model = YOLO("yolov8n.pt")
    model.train(
        data=str(data),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        project=str(repo_root / "runs" / "tibia"),
        name="hpmp_v1_yolov8",
        device=device,
    )


if __name__ == "__main__":
    main()