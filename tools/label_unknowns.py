import sys
from ..src.bestiary.corrections import label_unknowns  # Ajusta import

if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "data/datasets/unknowns"
    label_unknowns(directory)