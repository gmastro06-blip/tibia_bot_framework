from imagehash import phash
from PIL import Image
import os

class DatasetDedupe:
    def __init__(self, dataset_dir: str = 'data/datasets'):
        os.makedirs(dataset_dir, exist_ok=True)
        self.dataset_dir = dataset_dir

    def dedupe(self, threshold: int = 5, keep_hard_negatives: bool = True):
        hashes = {}
        removed = 0
        for file in os.listdir(self.dataset_dir):
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                path = os.path.join(self.dataset_dir, file)
                try:
                    img = Image.open(path)
                    h = phash(img)
                    duplicate = False
                    for existing_file, existing_hash in list(hashes.items()):
                        if h - existing_hash < threshold:
                            duplicate = True
                            if keep_hard_negatives and 'hard' in file.lower():
                                os.remove(os.path.join(self.dataset_dir, existing_file))
                                del hashes[existing_file]
                                hashes[file] = h
                            else:
                                os.remove(path)
                                removed += 1
                            break
                    if not duplicate:
                        hashes[file] = h
                except Exception:
                    pass
        print(f"Deduplicados: {removed} archivos")