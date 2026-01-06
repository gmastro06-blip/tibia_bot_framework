import os
import json
import cv2

def label_unknowns(unknowns_dir: str = 'data/unknowns') -> None:
    corrections = {}
    corr_path = 'data/ocr_corrections.json'
    if os.path.exists(corr_path):
        with open(corr_path, 'r') as f:
            corrections = json.load(f)
    for filename in os.listdir(unknowns_dir):
        if filename.endswith('.png'):
            path = os.path.join(unknowns_dir, filename)
            img = cv2.imread(path)
            cv2.imshow('Unknown', img)
            raw_ocr = input("Raw OCR: ")
            correct_name = input("Nombre correcto (o skip): ")
            if correct_name != 'skip':
                corrections[raw_ocr] = correct_name
            cv2.destroyAllWindows()
    with open(corr_path, 'w') as f:
        json.dump(corrections, f, indent=4)

if __name__ == "__main__":
    import sys
    directory = sys.argv[1] if len(sys.argv) > 1 else "data/datasets/unknowns"
    label_unknowns(directory)