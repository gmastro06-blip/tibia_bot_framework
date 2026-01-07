import os
import json
import cv2
import time

class ActiveLearningTool:
    def __init__(self, unknowns_dir: str = 'data/unknowns'):
        os.makedirs(unknowns_dir, exist_ok=True)
        self.unknowns_dir = unknowns_dir

    def save_unknown(self, crop: cv2.Mat, raw_ocr: str, score: float, context: dict):
        ts = time.time()
        img_path = os.path.join(self.unknowns_dir, f"{ts}.png")
        cv2.imwrite(img_path, crop)
        json_path = os.path.join(self.unknowns_dir, f"{ts}.json")
        data = {"raw_ocr": raw_ocr, "score": score, "context": context}
        with open(json_path, 'w') as f:
            json.dump(data, f)

    def label(self):
        files = [f for f in os.listdir(self.unknowns_dir) if f.endswith('.png')]
        files.sort()
        corrections = {}
        corr_path = 'data/ocr_corrections.json'
        if os.path.exists(corr_path):
            with open(corr_path, 'r') as f:
                corrections = json.load(f)
        for file in files:
            path = os.path.join(self.unknowns_dir, file)
            img = cv2.imread(path)
            cv2.imshow('Label Unknown', img)
            base = os.path.splitext(file)[0]
            json_path = os.path.join(self.unknowns_dir, f"{base}.json")
            with open(json_path, 'r') as f:
                data = json.load(f)
            raw = data['raw_ocr']
            correct = input(f"Raw: {raw} -> Correcto (skip): ")
            if correct.lower() != 'skip':
                corrections[raw] = correct
            cv2.waitKey(1)
        cv2.destroyAllWindows()
        with open(corr_path, 'w') as f:
            json.dump(corrections, f, indent=4)