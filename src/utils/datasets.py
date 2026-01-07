from typing import List, Dict
import os
from imagehash import phash
from PIL import Image


def dedupe_dataset(dir_path: str, threshold: float = 0.9) -> List[str]:
    hashes: Dict[str, phash.ImageHash] = {}  # Hint
    for file in os.listdir(dir_path):
        img = Image.open(os.path.join(dir_path, file))
        h = phash(img)
        # Dist hamming
        dists = [(h - existing) for existing in hashes.values()]
        if all(d > (1 - threshold) * 64 for d in dists):
            hashes[file] = h
        else:
            os.remove(os.path.join(dir_path, file))  # Dedupe
    # Conservar hard negatives: manual o por flag en active learning
    return list(hashes.keys())
