import os
from imagehash import phash
from PIL import Image

def dedupe(dir: str, threshold: float = 5):
    hashes = {}
    for file in os.listdir(dir):
        img = Image.open(os.path.join(dir, file))
        h = phash(img)
        if all(abs(h - existing) > threshold for existing in hashes.values()):
            hashes[file] = h
        else:
            os.remove(os.path.join(dir, file))
    # Hard negatives: manual flag