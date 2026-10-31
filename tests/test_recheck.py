"""End-to-end tests for pot.recheck with a fake client (no network)."""

import json
from pathlib import Path

from PIL import Image

import pot.recheck as recheck
import pot.turnover as turnover
from tests.fakes import FakeSpark, photo_ids_in


def _photo(directory, name, color="blue"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color).save(directory / name)
    return directory / name


def _matcher(call):
    rooms = call["schema"]["properties"]["matches"]["items"]["properties"]["room_id"]["enum"]
    return {
        "matches": [
            {"photo_id": pid, "room_id": rooms[0], "confidence": "high", "seen": "a room"}
            for pid in photo_ids_in(call["content"])
        ]
    }


def _inspector(new_photo_id):
    def answer(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        if new_photo_id in call["purpose"]:
            status, photo = "done", new_photo_id
        else:
            status, photo = "not_shown", ""
        return {
            "claims": [
                {"item_id": item_id, "status": status, "photo_id": photo,
                 "seen": "looks good", "count_seen": 1}
                for item_id in enum
            ],
            "other_issues": [],
        }
    return answer


def _run_started(tmp_path, monkeypatch, inspector):
    incoming = tmp_path / "in"
    _photo(incoming, "a.jpg")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(turnover, "MetaClient", lambda *a, **k: FakeSpark(matcher=_matcher, inspector=inspector))
    assert turnover.main(["pot.turnover", str(incoming), "--property", "demo-house"]) == 0
    return next((tmp_path / "data" / "runs").iterdir())


def test_recheck_turns_needs_proof_into_proven(tmp_path, monkeypatch, capsys):
    client = FakeSpark(matcher=_matcher, inspector=_inspector("P02"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(turnover, "MetaClient", lambda *a, **k: client)
    incoming = tmp_path / "in"
    _photo(incoming, "a.jpg")
    assert turnover.main(["pot.turnover", str(incoming), "--property", "demo-house"]) == 0
    run_dir = next((tmp_path / "data" / "runs").iterdir())
    calls_before = len(client.calls)
    new_photo = _photo(tmp_path / "new", "b.jpg", "green")
    summary = recheck.recheck_photo(client, run_dir, new_photo, "master-bedroom")
    assert summary["photo_id"] == "P02"
    assert summary["changes"] == {
        "master-bedroom.beds_made": ("needs proof", "proven"),
        "master-bedroom.no_belongings": ("needs proof", "proven"),
        "master-bedroom.floor_clear": ("needs proof", "proven"),
    }
    assert summary["elapsed_seconds"] >= 0
    assert all(call["effort"] == "low" for call in client.calls[calls_before:])
    first = json.loads((run_dir / "first_look.json").read_text(encoding="utf-8"))
    assert first["photos"][-1]["id"] == "P02"
    assert first["photos"][-1]["room_id"] == "master-bedroom"
    assert first["claims"]["master-bedroom.beds_made"]["status"] == "done"
    proof = json.loads((run_dir / "proof.json").read_text(encoding="utf-8"))
    assert proof["items"]["master-bedroom.beds_made"]["status"] == "proven"
    assert (run_dir / "evidence" / "master-bedroom.beds_made.jpg").is_file()
    assert (run_dir / "requests.json").is_file()


def test_recheck_proven_item_keeps_proof_without_new_problem(tmp_path, monkeypatch, capsys):
    client = FakeSpark(matcher=_matcher, inspector=_inspector("P01"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(turnover, "MetaClient", lambda *a, **k: client)
    incoming = tmp_path / "in"
    _photo(incoming, "a.jpg")
    assert turnover.main(["pot.turnover", str(incoming), "--property", "demo-house"]) == 0
    run_dir = next((tmp_path / "data" / "runs").iterdir())
    # The re-check photo (P02) yields not_shown: nothing changes.
    new_photo = _photo(tmp_path / "new", "b.jpg", "green")
    summary = recheck.recheck_photo(client, run_dir, new_photo, "master-bedroom")
    assert summary["changes"] == {}
    proof = json.loads((run_dir / "proof.json").read_text(encoding="utf-8"))
    assert proof["items"]["master-bedroom.beds_made"]["status"] == "proven"


def test_recheck_main_end_to_end(tmp_path, monkeypatch, capsys):
    run_dir = _run_started(tmp_path, monkeypatch, _inspector("P02"))
    monkeypatch.setattr(recheck, "MetaClient", lambda *a, **k: FakeSpark(matcher=_matcher, inspector=_inspector("P02")))
    new_photo = _photo(tmp_path / "new", "b.jpg", "green")
    capsys.readouterr()
    assert recheck.main(["pot.recheck", str(run_dir), str(new_photo), "--room", "master-bedroom"]) == 0
    out = capsys.readouterr().out
    assert "master-bedroom.beds_made: needs proof -> proven" in out
    assert "NOT READY" in out
    assert "Requests" in out
    assert "Re-check took" in out


def test_recheck_main_errors(tmp_path, monkeypatch, capsys):
    run_dir = _run_started(tmp_path, monkeypatch, _inspector("P02"))
    monkeypatch.setattr(recheck, "MetaClient", lambda *a, **k: FakeSpark())
    new_photo = _photo(tmp_path / "new", "b.jpg", "green")
    assert recheck.main(["pot.recheck", str(run_dir), str(new_photo), "--room", "attic"]) == 1
    bad = tmp_path / "new" / "note.txt"
    bad.write_text("x")
    assert recheck.main(["pot.recheck", str(run_dir), str(bad), "--room", "master-bedroom"]) == 1
    assert recheck.main(["pot.recheck", str(tmp_path / "nope"), str(new_photo), "--room", "master-bedroom"]) == 1
