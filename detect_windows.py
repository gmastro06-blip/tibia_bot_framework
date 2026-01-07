#!/usr/bin/env python3
"""Script para detectar todas las ventanas disponibles en el sistema"""

import sys
import os

# Agregar src al path
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import win32gui

def enum_windows_callback(hwnd, windows):
    """Callback para enumerar ventanas"""
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title:  # Solo ventanas con título
            windows.append((hwnd, title))

def main():
    print("Detectando todas las ventanas visibles en el sistema...")
    windows = []
    win32gui.EnumWindows(enum_windows_callback, windows)

    print(f"Encontradas {len(windows)} ventanas con título:")
    print()

    # Filtrar ventanas que podrían ser de Tibia
    tibia_windows = []
    for hwnd, title in windows:
        print(f"HWND: {hwnd:08X} - Título: '{title}'")
        if 'tibia' in title.lower():
            tibia_windows.append((hwnd, title))

    print()
    print(f"Ventanas que contienen 'tibia': {len(tibia_windows)}")
    for hwnd, title in tibia_windows:
        rect = win32gui.GetWindowRect(hwnd)
        x, y, width, height = rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]
        print(f"  HWND: {hwnd:08X} - '{title}' - Posición: ({x}, {y}) - Tamaño: {width}x{height}")

if __name__ == "__main__":
    main()