"""End-to-end CLI test for pot.turnover with a fake client (no network)."""

import json
from pathlib import Path

from PIL import Image

import pot.turnover as turnover
from pot.matcher import match_rooms
from tests.fakes import FakeSpark, photo_ids_in


def _incoming(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), "blue").save(directory / "a.jpg")
    Image.new("RGB", (48, 64), "green").save(directory / "b.png")
    return directory


def _client():
    def matcher(call):
        rooms = call["schema"]["properties"]["matches"]["items"]["properties"]["room_id"]["enum"]
        return {
            "matches": [
                {
                    "photo_id": pid,
                    "room_id": rooms[0],
                    "confidence": "high",
                    "seen": "a tidy room",
                }
                for pid in photo_ids_in(call["content"])
            ]
        }

    def inspector(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        return {
            "claims": [
                {
                    "item_id": item_id,
                    "status": "done",
                    "photo_id": photo_ids_in(call["content"])[0],
                    "seen": "looks good",
                    "count_seen": 1,
                }
                for item_id in enum
            ],
            "other_issues": [],
        }

    return FakeSpark(matcher=matcher, inspector=inspector)


def test_run_turnover_writes_first_look_json(tmp_path):
    run_dir = tmp_path / "run"
    outcome = turnover.run_turnover(_incoming(tmp_path / "in"), "demo-house", _client(), run_dir)
    saved = json.loads((run_dir / "first_look.json").read_text(encoding="utf-8"))
    assert saved["property"] == "demo-house"
    assert [p["id"] for p in saved["photos"]] == ["P01", "P02"]
    assert [p["file"] for p in saved["photos"]] == ["a.jpg", "b.png"]
    assert set(saved["coverage"]) == {
        "master-bedroom", "guest-bedroom", "master-bathroom", "hall-bathroom",
        "kitchen", "living-room", "patio",
    }
    assert len(saved["claims"]) == 30 - 7  # no coverage items sent to the model
    assert set(saved) == {"property", "photos", "coverage", "claims", "other_issues", "notes"}
    assert outcome["match"].coverage["master-bedroom"] == "covered"
    assert outcome["look"].claims["master-bedroom.beds_made"].status == "done"
    assert outcome["look"].claims["kitchen.trash"].status == "not_shown"
    proof = json.loads((run_dir / "proof.json").read_text(encoding="utf-8"))
    assert set(proof) == {"verdict", "items", "other_issues", "notes"}
    assert proof["verdict"]["ready"] is False
    assert proof["items"]["master-bedroom.beds_made"]["status"] == "proven"
    assert proof["items"]["master-bedroom.beds_made"]["file"] == "a.jpg"
    assert proof["items"]["master-bedroom.beds_made"]["sam"]["count"] == 3
    assert (run_dir / "evidence" / "master-bedroom.beds_made.jpg").is_file()


def test_main_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(turnover, "MetaClient", lambda *a, **k: _client())
    incoming = _incoming(tmp_path / "in")
    assert turnover.main(["pot.turnover", str(incoming), "--property", "demo-house"]) == 0
    runs = list((tmp_path / "data" / "runs").iterdir())
    assert len(runs) == 1
    assert (runs[0] / "first_look.json").is_file()
    assert (runs[0] / "photos" / "P01_check.jpg").is_file()
    assert (runs[0] / "proof.json").is_file()
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "Need a photo" in out
    assert "Proven:" in out
    assert "Meta calls:" in out
    assert "Tokens:" in out
    assert "Model time:" in out


def test_run_reports_elapsed_time(tmp_path):
    outcome = turnover.run_turnover(_incoming(tmp_path / "in"), "demo-house", _client(), tmp_path / "run")
    assert outcome["elapsed_seconds"] >= 0


def test_matcher_runs_through_real_photos(tmp_path):
    # Matcher reads the overview files intake saved (exercises photo_parts).
    run_dir = tmp_path / "run"
    outcome = turnover.run_turnover(_incoming(tmp_path / "in"), "demo-house", _client(), run_dir)
    assert len(outcome["photos"]) == 2
    assert match_rooms(_client(), outcome["photos"], outcome["property"]).assignments.keys() == {
        "P01", "P02"
    }
