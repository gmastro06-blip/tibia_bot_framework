import json
import time
from typing import Dict


class Logger:
    def log(self, stage: str, data: Dict):
        entry = {'timestamp': time.time(), 'stage': stage, **data}
        with open('logs/telemetry.json', 'a') as f:
            json.dump(entry, f)
