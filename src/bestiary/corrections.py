import json
import os


def label_unknowns(directory: str, output: str = "configs/ocr_corrections.json"):
    corrections = {}
    if os.path.exists(output):
        with open(output, 'r') as f:
            corrections = json.load(f)
    for file in os.listdir(directory):
        if file.endswith(".jpg"):  # Crops guardados
            # Mostrar img, input user correct name
            print(f"Para {file}: ingresa nombre correcto")
            correct = input()
            raw_ocr = file.split("_")[1]  # Asumir naming
            corrections[raw_ocr] = correct
    with open(output, 'w') as f:
        json.dump(corrections, f)
