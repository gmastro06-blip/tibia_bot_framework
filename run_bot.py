#!/usr/bin/env python3
"""Launcher script para ejecutar el bot desde el directorio raíz"""

import sys
import os

# Agregar el directorio src al path para que los imports funcionen
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from main import run_bot

if __name__ == "__main__":
    # Safe-by-default OCR: isolate EasyOCR into a subprocess with hard timeout.
    # Users can override any of these via env.
    os.environ.setdefault("OCR_ENABLED", "1")
    os.environ.setdefault("OCR_ISOLATE_PROCESS", "1")
    os.environ.setdefault("OCR_READTEXT_TIMEOUT_MS", "250")
    os.environ.setdefault("HPMP_OCR_ENABLED", "1")
    os.environ.setdefault("CAP_OCR_ENABLED", "0")

    # Safe-by-default capture: prefer real client window (avoid OBS projector).
    os.environ.setdefault("CAPTURE_BACKEND", "dxgi")
    os.environ.setdefault("CAPTURE_TARGET", "client")
    os.environ.setdefault("CAPTURE_TITLE_INCLUDE", "Tibia")
    os.environ.setdefault("CAPTURE_TITLE_EXCLUDE", "Proyector|Projector|OBS")
    run_bot()