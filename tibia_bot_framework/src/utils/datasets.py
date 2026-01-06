from typing import List, Dict
import os
import cv2
from imagehash import phash
from PIL import Image

def dedupe_dataset(dir_path: str, threshold: float = 0.9) -> List[str]:
    hashes: Dict[str, phash.ImageHash] = {}  # Hint
    for file in os.listdir(dir_path):
        img = Image.open(os.path.join(dir_path, file))
        h = phash(img)
        if all((h - existing) > (1 - threshold) * 64 for existing in hashes.values()):  # Dist hamming
            hashes[file] = h
        else:
            os.remove(os.path.join(dir_path, file))  # Dedupe
    # Conservar hard negatives: manual o por flag en active learning
    return list(hashes.keys())