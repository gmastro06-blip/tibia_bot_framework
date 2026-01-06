from typing import Dict
import time
import sys
import os

# Hack para relative imports al correr directamente (opcional, quítalo si usas -m)
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from capture.provider import CaptureProvider

def benchmark_capture(provider: CaptureProvider, duration: int = 10) -> Dict[str, float]:
    start = time.time()
    frames = 0
    latencies = []
    drops = 0
    while time.time() - start < duration:
        t0 = time.time()
        frame = provider.capture()
        lat = (time.time() - t0) * 1000
        if frame is None:
            drops += 1
        else:
            frames += 1
        latencies.append(lat)
    return {
        "fps": frames / duration,
        "avg_ms": sum(latencies) / len(latencies),
        "dropped": drops,
        "cpu_usage": 0,  # TODO: psutil.cpu_percent()
        "gpu_usage": 0   # TODO: GPUtil
    }


if __name__ == "__main__":
    provider = CaptureProvider(mode="mss", window_title="Tibia - Loterinne")  # MSS no necesita ventana exacta
    results = benchmark_capture(provider, duration=20)
    print(results)