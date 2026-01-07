import json
import requests
from bs4 import BeautifulSoup


class BestiaryRegistry:
    def build(self, source: str = 'https://ejemplo-catalogo-tibia.com') -> None:
        html = (requests.get(source).text if source.startswith('http')
                else open(source).read())
        soup = BeautifulSoup(html, 'html.parser')
        creatures = []
        for entry in soup.find_all('div', class_='creature'):
            name = entry.text.strip()
            creatures.append({
                'name_key': name.lower().replace(' ', '_'),
                'name': name
            })
        with open('data/creatures_registry.json', 'w') as f:
            json.dump(creatures, f)
