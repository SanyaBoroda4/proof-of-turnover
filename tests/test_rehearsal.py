"""Offline tests for scripts/rehearsal.py --map/--leave-cards.

HTTP is monkeypatched: no live calls, no server, no model key.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _load_rehearsal():
    path = ROOT / "scripts" / "rehearsal.py"
    spec = importlib.util.spec_from_file_location("rehearsal_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tour():
    return json.loads(
        (ROOT / "properties" / "rental-1" / "tour.json")
        .read_text(encoding="utf-8"))["shots"]


def test_plan_covers_room_completely_or_skips_it():
    rehearsal = _load_rehearsal()
    tour = _tour()
    full = {tour[0]["shot_id"]: {"file": "x.jpg", "reason": "r"}}
    mapped, skip = rehearsal.plan_from_map(tour, full)
    assert mapped == [tour[0]["shot_id"]]
    room_ids = []
    for shot in tour:
        if shot["room_id"] not in room_ids:
            room_ids.append(shot["room_id"])
    assert skip == [r for r in room_ids if r != tour[0]["room_id"]]


def test_plan_rejects_partially_mapped_room():
    rehearsal = _load_rehearsal()
    tour = _tour()
    kitchen = [s["shot_id"] for s in tour if s["room_id"] == "kitchen"]
    partial = {kitchen[0]: {"file": "x.jpg", "reason": "r"}}
    with pytest.raises(SystemExit) as exc:
        rehearsal.plan_from_map(tour, partial)
    assert "completely or not at all" in str(exc.value)
    assert "kitchen" in str(exc.value)


def test_plan_rejects_unknown_shot():
    rehearsal = _load_rehearsal()
    with pytest.raises(SystemExit) as exc:
        rehearsal.plan_from_map(_tour(), {"nope-99": {"file": "x"}})
    assert "unknown shots" in str(exc.value)


def test_leave_cards_skips_unmapped_rooms_and_stops(tmp_path, monkeypatch,
                                                     capsys):
    rehearsal = _load_rehearsal()
    photo = tmp_path / "porch.jpg"
    Image.new("RGB", (24, 18), "blue").save(photo, "JPEG")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"shots": {
        "porch-01": {"file": str(photo), "reason": "test"},
    }}), encoding="utf-8")

    tour = _tour()
    calls = []

    def fake_request(base, code, method, path, body=None, content_type=None,
                     filename=None, fields=None):
        calls.append((method, path))
        if method == "POST" and path == "/api/runs":
            return {"run_id": "run-test-1", "shots": len(tour),
                    "language": "en"}
        if method == "GET" and path.endswith("/tour"):
            return {"shots": tour}
        if method == "POST" and path.endswith("/photos"):
            return {"photo_id": "P01"}
        if method == "GET" and path.endswith("/status"):
            return {
                "checking": [], "unchecked": [],
                "agent_state": "needs_input",
                "cards": ["porch.area_tidy"],
                "card_groups": [{
                    "key": "P01", "photo_id": "P01", "shot_id": "porch-01",
                    "item_ids": ["porch.area_tidy"],
                }],
                "rooms": [{"id": "porch", "name": "Front porch", "items": [{
                    "id": "porch.area_tidy", "status": "problem",
                    "reason": "shoes on the porch",
                    "photo_id": "P01", "has_evidence": True,
                    "has_before": False, "request": {
                        "cleaner": "Clear the porch", "host": "x",
                        "status": "problem"},
                }]}],
            }
        return {}

    monkeypatch.setattr(rehearsal, "_request", fake_request)
    result = rehearsal.main([
        "--base", "http://127.0.0.1:8000", "--code", "TESTCODE",
        "--map", str(map_file), "--leave-cards", "--pace", "0",
        "--language", "en",
    ])
    assert result == 0
    out = capsys.readouterr().out
    assert "TESTCODE" not in out
    assert "Run left open: run-test-1" in out
    assert "photo=P01 shot=porch-01" in out
    assert "Clear the porch" in out
    # Skipped every unmapped room, uploaded only the mapped shot,
    # and never resolved or finished.
    posts = [path for method, path in calls if method == "POST"]
    assert posts.count("/api/runs") == 1
    assert sum(1 for p in posts if p.endswith("/skip-room")) >= 5
    assert sum(1 for p in posts if p.endswith("/photos")) == 1
    assert not any("resolve" in p or p.endswith("/finish") for p in posts)


def test_demo_run_actions_apply_like_the_phone(tmp_path, monkeypatch, capsys):
    rehearsal = _load_rehearsal()
    photo = tmp_path / "porch.jpg"
    Image.new("RGB", (24, 18), "blue").save(photo, "JPEG")
    fix = tmp_path / "fix.jpg"
    Image.new("RGB", (24, 18), "green").save(fix, "JPEG")
    map_file = tmp_path / "map.json"
    map_file.write_text(json.dumps({"shots": {
        "porch-01": {"file": str(photo), "reason": "test"},
    }}), encoding="utf-8")
    actions_file = tmp_path / "actions.json"
    actions_file.write_text(json.dumps({"actions": [
        {"action": "its_fine", "item": "porch.area_tidy"},
        {"action": "new_photo", "item": "laundry.floor_clear",
         "file": str(fix)},
    ]}), encoding="utf-8")

    tour = _tour()
    posts = []

    def status():
        def item(item_id, room):
            return {"id": item_id, "status": "problem", "reason": "mess",
                    "photo_id": "P01", "has_evidence": True,
                    "has_before": False,
                    "request": {"cleaner": "Fix it", "host": "x",
                                "status": "problem"}}
        return {
            "checking": [], "unchecked": [], "agent_state": "needs_input",
            "cards": ["porch.area_tidy", "laundry.floor_clear"],
            "card_groups": [
                {"key": "P01", "photo_id": "P01", "shot_id": "porch-01",
                 "item_ids": ["porch.area_tidy"]},
                {"key": "P07", "photo_id": "P07", "shot_id": "laundry-04",
                 "item_ids": ["laundry.floor_clear"]},
            ],
            "rooms": [
                {"id": "porch", "name": "Porch",
                 "items": [item("porch.area_tidy", "porch")]},
                {"id": "laundry", "name": "Laundry",
                 "items": [item("laundry.floor_clear", "laundry")]},
            ],
        }

    def fake_request(base, code, method, path, body=None, content_type=None,
                     filename=None, fields=None):
        if method == "POST":
            posts.append((path, body, fields))
        if method == "POST" and path == "/api/runs":
            return {"run_id": "run-test-2", "shots": len(tour),
                    "language": "en"}
        if method == "GET" and path.endswith("/tour"):
            return {"shots": tour}
        if method == "POST" and path.endswith("/photos"):
            return {"photo_id": "P02"}
        if method == "GET" and path.endswith("/status"):
            return status()
        return {}

    monkeypatch.setattr(rehearsal, "_request", fake_request)
    result = rehearsal.main([
        "--base", "http://127.0.0.1:8000", "--code", "TESTCODE",
        "--map", str(map_file), "--demo-run",
        "--actions", str(actions_file), "--pace", "0", "--language", "en",
    ])
    assert result == 0
    out = capsys.readouterr().out
    assert "TESTCODE" not in out
    create = next(b for p, b, f in posts if p == "/api/runs")
    assert b'"demo": true' in create
    resolves = [p for p, b, f in posts if "resolve" in p]
    assert resolves == ["/api/runs/run-test-2/items/porch.area_tidy/resolve"]
    uploads = [(p, f) for p, b, f in posts if p.endswith("/photos")]
    # First the taken shot, then the group retake with retake_items.
    assert len(uploads) == 2
    retake_fields = dict(
        (name, value) for name, value in uploads[1][1]
        if not isinstance(value, tuple))
    assert retake_fields["shot_id"] == "laundry-04"
    assert retake_fields["retake_items"] == "laundry.floor_clear"
    assert not any(p.endswith("/finish") for p, b, f in posts)
