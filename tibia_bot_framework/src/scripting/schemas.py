# Ejemplo schema para route.json
ROUTE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "action": {"type": "string"}
        },
        "required": ["x", "y"]
    }
}
# Similar para otros