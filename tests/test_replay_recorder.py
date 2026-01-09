from __future__ import annotations

import json

from src.telemetry.replay import ReplayRecorder


def test_replay_recorder_throttle(monkeypatch):
    rr = ReplayRecorder()

    t = 1000.0

    def fake_time():
        return t

    monkeypatch.setattr("time.time", fake_time)

    assert rr.should_record(enabled=True, interval_ms=1000) is True
    assert rr.should_record(enabled=True, interval_ms=1000) is False

    t = 1001.01
    assert rr.should_record(enabled=True, interval_ms=1000) is True


def test_replay_recorder_writes_json_and_pngs(tmp_path):
    rr = ReplayRecorder()

    crops = {
        "a": __make_img(10, 10),
        "b": __make_img(8, 12),
    }
    payload = {"ts": 123.456, "foo": "bar"}

    rr.record_crops(out_dir=str(tmp_path), crops=crops, payload=payload)

    json_files = list(tmp_path.glob("*.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["foo"] == "bar"

    roi_dir = tmp_path / "rois"
    assert roi_dir.exists()
    pngs = list(roi_dir.glob("*.png"))
    assert len(pngs) == 2


def __make_img(h: int, w: int):
    import numpy as np

    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 1] = 255
    return img
