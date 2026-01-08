from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class RoboflowConfig:
    api_key: str
    workspace: str
    project: str
    version: int
    confidence: float = 0.25
    overlap: float = 0.3
    local_model_path: Optional[str] = None


class RoboflowInference:
    """Wrapper mínimo para inferencia con Roboflow.

    Config por env vars:
      - ROBOFLOW_API_KEY
      - ROBOFLOW_WORKSPACE
      - ROBOFLOW_PROJECT
      - ROBOFLOW_VERSION
      - ROBOFLOW_CONFIDENCE (opcional)
      - ROBOFLOW_OVERLAP (opcional)

    Nota: este wrapper no se activa si falta config.
    """

    def __init__(self, config: RoboflowConfig):
        self.config = config
        self._model: Any
        self._is_local: bool

        if config.local_model_path:
            # Use local YOLO model
            from ultralytics import YOLO
            self._model = YOLO(config.local_model_path)
            self._is_local = True
        else:
            # Use Roboflow hosted
            from roboflow import Roboflow  # type: ignore

            rf = Roboflow(api_key=config.api_key)
            self._model = (
                rf.workspace(config.workspace)
                .project(config.project)
                .version(config.version)
                .model
            )
            self._is_local = False

    @staticmethod
    def from_env() -> Optional["RoboflowInference"]:
        api_key = os.getenv("ROBOFLOW_API_KEY", "").strip()
        workspace = os.getenv("ROBOFLOW_WORKSPACE", "").strip()
        project = os.getenv("ROBOFLOW_PROJECT", "").strip()
        version_raw = os.getenv("ROBOFLOW_VERSION", "").strip()

        if not (api_key and workspace and project and version_raw):
            return None

        try:
            version = int(version_raw)
        except ValueError:
            return None

        confidence = float(os.getenv("ROBOFLOW_CONFIDENCE", "0.25"))
        overlap = float(os.getenv("ROBOFLOW_OVERLAP", "0.3"))

        return RoboflowInference(
            RoboflowConfig(
                api_key=api_key,
                workspace=workspace,
                project=project,
                version=version,
                confidence=confidence,
                overlap=overlap,
            )
        )

    @staticmethod
    def from_env_hpmp() -> Optional["RoboflowInference"]:
        local_model = os.getenv("ROBOFLOW_HPMP_LOCAL_MODEL", "").strip()
        if local_model and os.path.exists(local_model):
            # Use local model
            confidence = float(os.getenv("ROBOFLOW_CONFIDENCE", "0.25"))
            overlap = float(os.getenv("ROBOFLOW_OVERLAP", "0.3"))
            return RoboflowInference(
                RoboflowConfig(
                    api_key="",  # Not needed for local
                    workspace="",
                    project="",
                    version=1,
                    confidence=confidence,
                    overlap=overlap,
                    local_model_path=local_model,
                )
            )
        else:
            # Use hosted or none
            api_key = os.getenv("ROBOFLOW_API_KEY", "").strip()
            workspace = os.getenv("ROBOFLOW_HPMP_WORKSPACE", "levelup-12nnc").strip()
            project = os.getenv("ROBOFLOW_HPMP_PROJECT", "hp-f3dd6").strip()
            version_raw = os.getenv("ROBOFLOW_HPMP_VERSION", "1").strip()

            if not (api_key and workspace and project and version_raw):
                return None

            try:
                version = int(version_raw)
            except ValueError:
                return None

            confidence = float(os.getenv("ROBOFLOW_CONFIDENCE", "0.25"))
            overlap = float(os.getenv("ROBOFLOW_OVERLAP", "0.3"))
            return RoboflowInference(
                RoboflowConfig(
                    api_key=api_key,
                    workspace=workspace,
                    project=project,
                    version=version,
                    confidence=confidence,
                    overlap=overlap,
                )
            )
    def predict(self, frame_bgr: np.ndarray) -> Dict[str, Any]:
        """Devuelve el JSON de predicciones."""
        # Roboflow acepta numpy arrays; la mayoría de modelos esperan RGB.
        frame_rgb = frame_bgr[:, :, ::-1]
        if self._is_local:
            # Local YOLO model
            results = self._model.predict(
                frame_rgb,
                conf=self.config.confidence,
                iou=self.config.overlap,
            )
            # Convert to similar format as Roboflow
            predictions = []
            for result in results:
                boxes = result.boxes
                for box in boxes:
                    x, y, w, h = box.xywh[0].cpu().numpy()
                    cls = int(box.cls[0].cpu().numpy())
                    conf = float(box.conf[0].cpu().numpy())
                    class_name = result.names[cls]
                    predictions.append({
                        "x": float(x),
                        "y": float(y),
                        "width": float(w),
                        "height": float(h),
                        "class": class_name,
                        "confidence": conf,
                    })
            return {"predictions": predictions}
        else:
            # Roboflow hosted
            prediction_obj = self._model.predict(
                frame_rgb,
                confidence=self.config.confidence,
                overlap=self.config.overlap,
            )
            prediction = prediction_obj.json()
            return prediction

    @staticmethod
    def extract_boxes(prediction: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normaliza a una lista simple de boxes (x,y,w,h,class,confidence)."""
        preds = prediction.get("predictions")
        if not isinstance(preds, list):
            return []
        out: List[Dict[str, Any]] = []
        for p in preds:
            if not isinstance(p, dict):
                continue
            out.append(
                {
                    "x": p.get("x"),
                    "y": p.get("y"),
                    "width": p.get("width"),
                    "height": p.get("height"),
                    "class": p.get("class"),
                    "confidence": p.get("confidence"),
                }
            )
        return out

    @staticmethod
    def best_box_by_class(
        boxes: List[Dict[str, Any]],
        class_names: Sequence[str],
    ) -> Optional[Dict[str, Any]]:
        """Devuelve la detección con mayor confidence cuya class esté en class_names."""
        if not boxes or not class_names:
            return None

        def _norm(name: str) -> str:
            # Normalize common label variations: case, spaces, hyphens, punctuation.
            s = (name or "").strip().lower()
            s = re.sub(r"[\s\-]+", "_", s)
            s = re.sub(r"[^a-z0-9_]+", "", s)
            s = re.sub(r"_+", "_", s)
            return s

        wanted = {_norm(c) for c in class_names if c and _norm(c)}
        best: Optional[Dict[str, Any]] = None
        best_conf = -1.0
        for b in boxes:
            cls = _norm(str(b.get("class", "")))
            if cls not in wanted:
                continue
            try:
                conf = float(b.get("confidence", 0.0) or 0.0)
            except Exception:
                conf = 0.0
            if conf > best_conf:
                best_conf = conf
                best = b
        return best

    @staticmethod
    def crop_from_box(frame: np.ndarray, box: Dict[str, Any]) -> Optional[np.ndarray]:
        """Cropea usando un box de Roboflow (x,y,width,height) en píxeles (centro + tamaño)."""
        try:
            x = float(box["x"])
            y = float(box["y"])
            w = float(box["width"])
            h = float(box["height"])
        except Exception:
            return None

        x0 = int(round(x - w / 2))
        y0 = int(round(y - h / 2))
        x1 = int(round(x + w / 2))
        y1 = int(round(y + h / 2))

        x0 = max(0, min(x0, frame.shape[1] - 1))
        y0 = max(0, min(y0, frame.shape[0] - 1))
        x1 = max(1, min(x1, frame.shape[1]))
        y1 = max(1, min(y1, frame.shape[0]))

        if x1 <= x0 or y1 <= y0:
            return None
        return frame[y0:y1, x0:x1]
