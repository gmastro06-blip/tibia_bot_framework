from typing import Dict, Any
import lupa  # type: ignore[import-untyped]
import jsonschema  # type: ignore[import-untyped]
import json
import os

class ScriptEngine:
    def __init__(self):
        self.lua = lupa.LuaRuntime()
        self.schemas: Dict[str, Any] = self.load_schemas()

    def load_schemas(self) -> Dict[str, Any]:
        return {
            'route': {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "name": {"type": "string"},
                        "action": {"type": "string"}
                    }
                }
            },
            'targeting': {
                "type": "object",
                "properties": {
                    "priorities": {"type": "array", "items": {"type": "string"}},
                    "min_hp_pct": {"type": "number"}
                }
            }
        }

    def validate_json(self, file_path: str, schema_key: str) -> Dict:
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "configs", file_path)
        with open(full_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        jsonschema.validate(data, self.schemas[schema_key])
        return data

    def exec_lua(self, script_path: str, api: Dict) -> Any:
        full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "scripts", script_path)
        safe_env = self.lua.table(**api)
        self.lua.globals()['os'] = None
        self.lua.globals()['io'] = None
        try:
            code = open(full_path, 'r', encoding='utf-8').read()
            func = self.lua.eval(code)
            return func()
        except Exception as e:
            print(f"Lua error: {e}")
            return None