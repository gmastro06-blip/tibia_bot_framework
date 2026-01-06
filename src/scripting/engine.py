from typing import Dict
import lupa
import jsonschema
import json

class ScriptEngine:
    def __init__(self):
        self.lua = lupa.LuaRuntime()
        self.schemas: Dict = self._load_schemas()

    def _load_schemas(self) -> Dict:
        return {
            'route': {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}}}
            # Agrega más
        }

    def validate_json(self, file: str, schema_key: str) -> Dict:
        with open(file, 'r') as f:
            data = json.load(f)
        jsonschema.validate(data, self.schemas[schema_key])
        return data

    def load_script(self, script: str = "scripts/hunt_cave.lua") -> None:
        safe_env = self.lua.table(move_to=print, wait=print)  # API segura
        try:
            self.lua.execute(script, environment=safe_env)
        except lupa.LuaError as e:
            print(f"Lua error: {e}")