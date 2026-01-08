import sys
import os

# Configurar path para imports absolutos desde el directorio del proyecto
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

import cv2
from vision.ocr import OCRProcessor

if __name__ == "__main__":
    print("Analizando ROIs capturadas...")

    ocr = OCRProcessor()

    # Cargar imágenes
    hp_img = cv2.imread("debug_images_real/hp_roi_real.png")
    mp_img = cv2.imread("debug_images_real/mp_roi_real.png")

    if hp_img is not None:
        print("Analizando HP ROI...")
        hp_text = ocr.extract_text(hp_img)
        print(f"HP texto: '{hp_text}'")
    else:
        print("No se encontró hp_roi_real.png")

    if mp_img is not None:
        print("Analizando MP ROI...")
        mp_text = ocr.extract_text(mp_img)
        print(f"MP texto: '{mp_text}'")
    else:
        print("No se encontró mp_roi_real.png")