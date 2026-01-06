from typing import Dict, Any
import lupa
import jsonschema
import json
import time

class ScriptEngine:
    def __init__(self):
        self.lua = lupa.LuaRuntime()
        self.schemas: Dict = self.load_schemas()

    def load_schemas(self) -> Dict:
        return {'route': {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}}}}}

    def validate_json(self, file: str, schema_key: str) -> Dict:
        with open(file, 'r') as f:
            data = json.load(f)
        jsonschema.validate(data, self.schemas[schema_key])
        return data

    def exec_lua(self, script: str, api: Dict) -> Any:
        safe_env = self.lua.table(**api)
        self.lua.globals()['os'] = None
        start = time.time()
        try:
            func = self.lua.eval(script)
            result = func()
            if time.time() - start > 0.1:
                raise TimeoutError("Lua timeout")
            return result
        except lupa.LuaError:
            return None