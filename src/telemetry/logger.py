import json
import time
from typing import Any, Dict  # Import Dict

class TelemetryLogger:
    def __init__(self, log_path: str = "data/replays/log.json"):
        self.log_file = open(log_path, 'a')

    def log_metric(self, name: str, value: float) -> None:
        self._write({"type": "metric", "name": name, "value": value, "ts": time.time()})

    def log_state(self, state: Any) -> None:
        self._write({"type": "state", "data": state.__dict__, "ts": time.time()})

    def _write(self, entry: Dict[str, Any]) -> None:  # Type hint
        self.log_file.write(json.dumps(entry) + "\n")
        self.log_file.flush()