"""Offline tests for scripts/timing.py with a fabricated run folder."""

import importlib.util
import json
from pathlib import Path


def _load_timing():
    path = Path(__file__).resolve().parents[1] / "scripts" / "timing.py"
    spec = importlib.util.spec_from_file_location("timing_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(run_dir, name, rows):
    path = run_dir / name
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_timing_reports_waits_slowest_and_open_items(tmp_path, monkeypatch, capsys):
    import pot.config as config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run-20261101-120000"
    run_dir.mkdir(parents=True)
    _write(run_dir, "timeline.jsonl", [
        {"time": "2026-11-01T12:00:00.000", "event": "photo_received",
         "photo_id": "P01", "shot_id": "bed-1"},
        {"time": "2026-11-01T12:00:10.000", "event": "photo_received",
         "photo_id": "P02", "shot_id": "bath-1"},
        {"time": "2026-11-01T12:00:11.000", "event": "photo_finished",
         "photo_id": "P01", "outcome": "ok"},
        {"time": "2026-11-01T12:00:12.000", "event": "card_ready",
         "item_id": "bath.towels"},
        {"time": "2026-11-01T12:00:20.000", "event": "photo_finished",
         "photo_id": "P02", "outcome": "ok"},
        {"time": "2026-11-01T12:00:20.100", "event": "all_checked", "photos": 2},
    ])
    _write(run_dir, "calls.jsonl", [
        {"time": "2026-11-01T12:00:01.000", "model": "muse-spark",
         "purpose": "first look", "photo_id": "P01", "seconds": 9.5,
         "attempts": 1, "source": "live"},
        {"time": "2026-11-01T12:00:02.000", "model": "sam-3.1",
         "purpose": "sam x", "photo_id": "P01", "seconds": 2.25,
         "attempts": 2, "source": "failed", "status": 429},
    ])
    (run_dir / "proof.json").write_text(json.dumps({
        "verdict": {"ready": False, "problems": ["bath.towels"],
                    "need_photo": [], "worth_a_look": []},
        "items": {"bath.towels": {"status": "problem"}},
    }), encoding="utf-8")
    (run_dir / "requests.json").write_text(json.dumps({
        "requests": [{"item_id": "bath.towels", "cleaner": "Foto toallas"}],
    }), encoding="utf-8")

    timing = _load_timing()
    assert timing.main(["timing.py", "run-20261101-120000"]) == 0
    out = capsys.readouterr().out
    assert "P01: 11.0 s" in out
    assert "P02: 10.0 s" in out
    assert "9.50 s muse-spark first look" in out
    assert "429s: 1" in out
    assert "-> all checked: 10.1 s" in out
    assert "-> first card ready: 2.0 s (bath.towels)" in out
    assert "Open critical items: 1" in out
    assert "bath.towels [problem]: Foto toallas" in out


def test_timing_same_millisecond_card_ready(tmp_path, monkeypatch, capsys):
    """Two card_ready events in the same millisecond must not crash min()."""
    import pot.config as config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    run_dir = tmp_path / "run-20261101-120000"
    run_dir.mkdir(parents=True)
    _write(run_dir, "timeline.jsonl", [
        {"time": "2026-11-01T12:00:00.000", "event": "photo_received",
         "photo_id": "P01", "shot_id": "bed-1"},
        {"time": "2026-11-01T12:00:10.000", "event": "photo_finished",
         "photo_id": "P01", "outcome": "ok"},
        {"time": "2026-11-01T12:00:10.000", "event": "card_ready",
         "item_id": "bed.made"},
        {"time": "2026-11-01T12:00:10.000", "event": "card_ready",
         "item_id": "bed.floor"},
        {"time": "2026-11-01T12:00:10.100", "event": "all_checked", "photos": 1},
    ])
    _write(run_dir, "calls.jsonl", [])

    timing = _load_timing()
    assert timing.main(["timing.py", "run-20261101-120000"]) == 0
    out = capsys.readouterr().out
    assert "First card overall:" in out


def test_timing_missing_run_exits(tmp_path, monkeypatch):
    import pot.config as config

    monkeypatch.setattr(config, "RUNS_DIR", tmp_path)
    timing = _load_timing()
    try:
        timing.main(["timing.py", "run-19990101-000000"])
    except SystemExit as exc:
        assert "No such run" in str(exc)
    else:
        raise AssertionError("expected SystemExit")
