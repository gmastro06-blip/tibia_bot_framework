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
    run_bot()