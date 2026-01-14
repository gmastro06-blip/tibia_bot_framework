import json

from tools.report_kpis import compute_kpis, iter_jsonl_events


def test_kpi_parser_and_fps_computation(monkeypatch) -> None:
    monkeypatch.setenv("WATCHDOG_STALE_GS_S", "5")

    # 12 lines total (10-20 target): mix of telemetry/health + some junk.
    lines = [
        json.dumps({
            "kind": "health",
            "ts": 100.0,
            "capture_ms_last": 10,
            "vision_ms_last": 20,
            "decision_ms_last": 5,
            "capture_ok": 10,
            "vision_ok": 10,
            "decision_ok": 10,
            "drop_frame_queue": 1,
            "drop_frame_queue_new": 0,
            "drop_gs_queue": 0,
            "drop_gs_queue_new": 0,
            "drop_jsonl_queue": 0,
            "drop_jsonl_queue_new": 0,
            "gs_age_s": 0.2,
        }),
        json.dumps({"kind": "telemetry", "ts": 100.0}),
        "not json",
        json.dumps({"kind": "telemetry", "ts": 101.0}),
        json.dumps({"kind": "telemetry", "ts": 102.0}),
        json.dumps({
            "kind": "health",
            "ts": 101.0,
            "capture_ms_last": 12,
            "vision_ms_last": 18,
            "decision_ms_last": 6,
            "capture_ok": 25,
            "vision_ok": 20,
            "decision_ok": 22,
            "drop_frame_queue": 2,
            "drop_frame_queue_new": 1,
            "drop_gs_queue": 1,
            "drop_gs_queue_new": 0,
            "drop_jsonl_queue": 0,
            "drop_jsonl_queue_new": 0,
            "gs_age_s": 6.0,
        }),
        "",
        json.dumps({"kind": "telemetry", "ts": 103.0}),
        json.dumps({
            "kind": "health",
            "ts": 102.0,
            "capture_ms_last": 30,
            "vision_ms_last": 10,
            "decision_ms_last": 15,
            "capture_ok": 40,
            "vision_ok": 30,
            "decision_ok": 35,
            "drop_frame_queue": 4,
            "drop_frame_queue_new": 3,
            "drop_gs_queue": 2,
            "drop_gs_queue_new": 1,
            "drop_jsonl_queue": 1,
            "drop_jsonl_queue_new": 0,
            "warn": "stale_gs=6.0s",
        }),
        json.dumps({"kind": "telemetry", "ts": 104.0}),
        json.dumps({"kind": "telemetry", "ts": 105.0}),
        "{",
    ]

    events = list(iter_jsonl_events(lines))
    kpis = compute_kpis(events)

    assert kpis.n_telemetry == 6
    assert kpis.n_health == 3
    # window is from first telemetry (100) to last telemetry (105)
    assert kpis.window_s == 5.0
    assert kpis.telemetry_fps == 1.2

    # Means across health samples
    assert kpis.avg_capture_ms == (10.0 + 12.0 + 30.0) / 3.0
    assert kpis.avg_vision_ms == (20.0 + 18.0 + 10.0) / 3.0
    assert kpis.avg_decision_ms == (5.0 + 6.0 + 15.0) / 3.0

    # End-to-end = capture + vision + decision
    assert kpis.avg_end_to_end_ms == ((10 + 20 + 5) + (12 + 18 + 6) + (30 + 10 + 15)) / 3.0

    # Percentiles (linear interpolation). With capture_ms [10, 12, 30]: p50=12.
    assert kpis.p50_capture_ms == 12.0
    assert kpis.p95_capture_ms is not None and 12.0 < kpis.p95_capture_ms < 30.0

    # Drop deltas from first health(100) -> last health(102)
    assert kpis.drops_frame_oldest == 3
    assert kpis.drops_frame_new == 3
    assert kpis.drops_gs_oldest == 2
    assert kpis.drops_gs_new == 1
    assert kpis.drops_jsonl_oldest == 1
    assert kpis.drops_jsonl_new == 0

    # Health-window fps from counters: (40-10)/(102-100)=15 fps
    assert kpis.capture_fps == 15.0
    assert kpis.vision_fps == 10.0
    assert kpis.decision_fps == 12.5
    assert kpis.effective_fps == 10.0

    # Stale detection: two of three health events are stale (gs_age_s>=5 or warn contains stale_gs)
    assert kpis.stale_gamestate_events == 2
    assert kpis.stale_gamestate_ratio == 2.0 / 3.0
