import json

from tools.report_kpis import compute_kpis, iter_jsonl_events


def test_kpi_parser_and_fps_computation() -> None:
    lines = [
        json.dumps({"kind": "health", "ts": 100.0, "capture_ms_last": 10, "vision_ms_last": 20, "decision_ms_last": 5, "drop_frame_queue": 1, "drop_gs_queue": 0, "drop_jsonl_queue": 0}),
        json.dumps({"kind": "telemetry", "ts": 100.0}),
        "not json",
        json.dumps({"kind": "telemetry", "ts": 101.0}),
        json.dumps({"kind": "telemetry", "ts": 102.0}),
        json.dumps({"kind": "health", "ts": 102.0, "capture_ms_last": 30, "vision_ms_last": 10, "decision_ms_last": 15, "drop_frame_queue": 4, "drop_gs_queue": 2, "drop_jsonl_queue": 1}),
    ]

    events = list(iter_jsonl_events(lines))
    kpis = compute_kpis(events)

    assert kpis.n_telemetry == 3
    assert kpis.n_health == 2
    # window is from first telemetry (100) to last telemetry (102)
    assert kpis.window_s == 2.0
    assert kpis.telemetry_fps == 1.5
    assert kpis.avg_capture_ms == 20.0
    assert kpis.avg_vision_ms == 15.0
    assert kpis.avg_decision_ms == 10.0
    assert kpis.drops_frame_oldest == 3
    assert kpis.drops_gs_oldest == 2
    assert kpis.drops_jsonl_oldest == 1
