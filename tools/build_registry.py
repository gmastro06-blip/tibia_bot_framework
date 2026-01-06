import sys
import json
from bs4 import BeautifulSoup
import requests

def build_creature_registry(source: str) -> None:
    if source.startswith('http'):
        html = requests.get(source).text
    else:
        with open(source, 'r', encoding='utf-8') as f:
            html = f.read()
    soup = BeautifulSoup(html, 'html.parser')
    creatures = []
    for entry in soup.select('.creature-entry'):
        name = entry.find('span', class_='name').text.strip()
        key = name.lower().replace(' ', '_')
        creatures.append({'name': name, 'name_key': key})
    with open('data/creatures_registry.json', 'w', encoding='utf-8') as f:
        json.dump(creatures, f, indent=4)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: poetry run python tools/build_registry.py tu_input.html")
        sys.exit(1)
    input_path = sys.argv[1]
    build_creature_registry(input_path)