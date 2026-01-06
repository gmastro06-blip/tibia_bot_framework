import json
from typing import List, Dict
from bs4 import BeautifulSoup  # Para HTML input

def build_creature_registry(input_path: str, output_path: str = "data/creatures_registry.json"):
    if input_path.endswith(".html"):
        with open(input_path, 'r') as f:
            soup = BeautifulSoup(f, 'html.parser')
        names = [tag.text.strip().lower() for tag in soup.find_all("name_tag")]  # Asumir tags
    else:
        with open(input_path, 'r') as f:
            names = json.load(f)['names']
    registry = {name: {"key": name.replace(" ", "_"), "hp": 0, "exp": 0} for name in names}  # Placeholder
    with open(output_path, 'w') as f:
        json.dump(registry, f)