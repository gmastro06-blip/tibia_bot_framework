import sys
from ..src.bestiary.registry import build_creature_registry  # Import la función de src si está allí; ajusta path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: poetry run python tools/build_registry.py tu_input.html")
        sys.exit(1)
    input_path = sys.argv[1]
    build_creature_registry(input_path)