import json
import time, Dict

class Logger:
    def log(self, stage: str, data: Dict):
        entry = {'timestamp': time.time(), 'stage': stage, **data}
        with open('logs/telemetry.json', 'a') as f:
            json.dump(entry, f)