import os
from typing import List

class RepoSetup:
    def __init__(self, root_dir: str = "tibia_bot_framework"):
        self.root_dir = root_dir
        self.directories: List[str] = [
            "src",
            "src/capture", "src/calibration", "src/vision", "src/gamestate",
            "src/bestiary", "src/battlelist", "src/navigation", "src/decision",
            "src/scripting", "src/actions", "src/safety", "src/telemetry", "src/utils",
            "configs", "configs/schemas",
            "data", "data/datasets", "data/replays",
            "examples", "examples/scripts", "examples/models",
            "tests", "tests/unit", "tests/integration",
            "tools"
        ]
        self.files: List[str] = [
            "README.md", "pyproject.toml",
            "src/__init__.py", "src/main.py",
            "src/capture/__init__.py", "src/capture/provider.py", "src/capture/benchmark.py",
            "src/calibration/__init__.py", "src/calibration/calibrator.py", "src/calibration/profiles.py",
            "src/vision/__init__.py", "src/vision/inference.py", "src/vision/ocr.py",
            "src/vision/classifiers.py", "src/vision/utils.py",
            "src/gamestate/__init__.py", "src/gamestate/builder.py", "src/gamestate/models.py",
            "src/bestiary/__init__.py", "src/bestiary/registry.py", "src/bestiary/matcher.py",
            "src/bestiary/corrections.py",
            "src/battlelist/__init__.py", "src/battlelist/extractor.py",
            "src/navigation/__init__.py", "src/navigation/navigator.py",
            "src/decision/__init__.py", "src/decision/engine.py", "src/decision/nodes.py",
            "src/scripting/__init__.py", "src/scripting/engine.py", "src/scripting/schemas.py",
            "src/actions/__init__.py", "src/actions/executor.py",
            "src/safety/__init__.py", "src/safety/manager.py",
            "src/telemetry/__init__.py", "src/telemetry/logger.py", "src/telemetry/replay.py",
            "src/utils/__init__.py", "src/utils/concurrency.py", "src/utils/datasets.py", "src/utils/overlay.py",
            "configs/rois_guess.json", "configs/bestiary_match.yaml", "configs/ocr_corrections.json",
            "configs/rules_overrides.json",
            "examples/scripts/hunt_cave.lua", "examples/scripts/route.json",
            "examples/scripts/targeting.json", "examples/scripts/panic.json",
            "examples/scripts/equipment.json", "examples/scripts/loot.json",
            "tools/build_registry.py", "tools/label_unknowns.py"
        ]

    def create_directories(self) -> None:
        try:
            os.makedirs(self.root_dir, exist_ok=True)
            for dir_path in self.directories:
                full_path = os.path.join(self.root_dir, dir_path)
                os.makedirs(full_path, exist_ok=True)
            print("Carpetas creadas exitosamente.")
        except OSError as e:
            print(f"Error al crear carpetas: {e}")

    def create_files(self) -> None:
        try:
            for file_path in self.files:
                full_path = os.path.join(self.root_dir, file_path)
                with open(full_path, 'w') as f:
                    f.write("")  # Placeholder vacío
            print("Archivos placeholders creados.")
        except OSError as e:
            print(f"Error al crear archivos: {e}")

    def generate_pyproject_toml(self) -> None:
        content = """
[tool.poetry]
name = "tibia-bot-framework"
version = "0.1.0"
description = "Framework para bot de Tibia-like"
authors = ["TuNombre <tuemail@example.com>"]

[tool.poetry.dependencies]
python = "^3.11"
opencv-python = "*"
mss = "*"
pyautogui = "*"
easyocr = "*"
onnxruntime-gpu = "*"
rapidfuzz = "*"
py-trees = "*"
luasandbox = "*"
jsonschema = "*"
imagehash = "*"
keyboard = "*"
numpy = "*"
scipy = "*"

[tool.poetry.group.dev.dependencies]
pytest = "*"
mypy = "*"
flake8 = "*"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
"""
        toml_path = os.path.join(self.root_dir, "pyproject.toml")
        with open(toml_path, 'w') as f:
            f.write(content.strip())
        print("pyproject.toml generado. Ejecuta 'poetry install' para instalar dependencias.")

    def setup(self) -> None:
        self.create_directories()
        self.create_files()
        self.generate_pyproject_toml()
        print(f"Estructura completa creada en {self.root_dir}. Ahora cd {self.root_dir} && poetry install")

if __name__ == "__main__":
    setup = RepoSetup()
    setup.setup()