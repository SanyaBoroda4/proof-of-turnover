"""Offline tests for scripts/score_first_look.py with a made-up run."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from score_first_look import score_run


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _fixture(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write(run_dir / "first_look.json", {
        "property": "tiny",
        "photos": [
            {"id": "P01", "file": "bed.jpg", "room_id": "bed", "confidence": "high", "seen": "a bed"},
            {"id": "P02", "file": "bath.jpg", "room_id": "bed", "confidence": "low", "seen": "tiles?"},
            {"id": "P03", "file": "mystery.jpg", "room_id": "unknown", "confidence": "low", "seen": ""},
        ],
        "coverage": {"bed": "covered", "bath": "needs proof"},
        "claims": {
            "bed.beds_made": {"status": "done", "photo_id": "P01", "seen": "made bed", "count_seen": 1},
            "bed.floor_clear": {"status": "problem", "photo_id": "P01", "seen": "shoes", "count_seen": None},
            "bath.towels": {"status": "not_shown", "photo_id": "", "seen": "", "count_seen": None},
            "bath.floor_clear": {"status": "problem", "photo_id": "P02", "seen": "towel pile", "count_seen": None},
        },
        "other_issues": [{"photo_id": "P01", "description": "crack in wall", "room_id": "bed"}],
        "notes": [],
    })
    key_path = tmp_path / "key.json"
    _write(key_path, {
        "rooms": {"bed.jpg": "bed", "bath.jpg": "bath", "mystery.jpg": None, "gone.jpg": "bed"},
        "expected": [
            {"item": "bed.beds_made", "result": "done", "why": "made"},
            {"item": "bath.towels", "result": "not_done", "why": "no towels shown"},
            {"item": "bath.coverage", "result": "not_done", "why": "no bath photo"},
            {"item": "*.floor_clear", "result": "problem", "photo": "bath.jpg", "why": "pile"},
            {"item": "bed.no_belongings", "result": "done", "why": "absent from run"},
        ],
    })
    return run_dir, key_path


def test_room_scoring_ignores_null_and_missing(tmp_path):
    report = score_run(*_fixture(tmp_path))
    assert "## Room matching: 1/2 correct" in report
    assert "MISMATCH `bath.jpg`: expected bath, got bed (low)" in report
    assert "mystery.jpg" not in report
    assert "gone.jpg" not in report


def test_item_scoring(tmp_path):
    report = score_run(*_fixture(tmp_path))
    assert "## Items: 4/5 pass" in report
    assert "PASS `bed.beds_made`" in report
    assert "PASS `bath.towels`" in report
    assert "PASS `bath.coverage`" in report
    assert "PASS `bath.floor_clear` in some room" in report
    assert "FAIL `bed.no_belongings`" in report


def test_false_alarms_list_unexpected_problems_and_issues(tmp_path):
    report = score_run(*_fixture(tmp_path))
    assert "`bed.floor_clear` @ `bed.jpg`" in report  # unexpected problem claim
    assert "other issue @ `bed.jpg` (room bed)" in report
    assert "`bath.floor_clear` @ `bath.jpg`" not in report.split("## Other app findings")[1]


def test_final_statuses_scored_when_proof_exists(tmp_path):
    run_dir, key_path = _fixture(tmp_path)
    _write(run_dir / "proof.json", {
        "verdict": {"ready": False},
        "items": {
            "bed.beds_made": {"status": "proven", "file": "bed.jpg"},
            "bed.floor_clear": {"status": "problem", "file": "bed.jpg"},
            "bath.towels": {"status": "needs proof", "file": None},
            "bath.coverage": {"status": "needs proof", "file": None},
            "bath.floor_clear": {"status": "needs proof", "file": "bath.jpg"},
            "bed.no_belongings": {"status": "needs proof", "file": None},
        },
    })
    report = score_run(run_dir, key_path)
    assert "## Final statuses: 4/5 pass" in report
    assert "PASS `bed.beds_made`" in report
    assert "final proven @ `bed.jpg` (first look: done @ `bed.jpg`)" in report
    assert "PASS `bath.coverage`" in report
    assert "PASS `bed.floor_clear` in some room" in report
    assert "FAIL `bed.no_belongings`" in report
