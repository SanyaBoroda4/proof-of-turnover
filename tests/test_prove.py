"""End-to-end test for pot.prove with a fake client (no network)."""

import json
from pathlib import Path

from PIL import Image

import pot.prove as prove
import pot.turnover as turnover
from tests.fakes import FakeSpark, photo_ids_in


def test_prove_reruns_proof_on_existing_run(tmp_path, monkeypatch, capsys):
    incoming = tmp_path / "in"
    incoming.mkdir()
    Image.new("RGB", (64, 48), "blue").save(incoming / "a.jpg")

    def matcher(call):
        rooms = call["schema"]["properties"]["matches"]["items"]["properties"]["room_id"]["enum"]
        return {
            "matches": [
                {"photo_id": pid, "room_id": rooms[0], "confidence": "high", "seen": "a room"}
                for pid in photo_ids_in(call["content"])
            ]
        }

    def inspector(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        return {
            "claims": [
                {"item_id": item_id, "status": "done",
                 "photo_id": photo_ids_in(call["content"])[0],
                 "seen": "looks good", "count_seen": 1}
                for item_id in enum
            ],
            "other_issues": [],
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(turnover, "MetaClient", lambda *a, **k: FakeSpark(matcher=matcher, inspector=inspector))
    monkeypatch.setattr(prove, "MetaClient", lambda *a, **k: FakeSpark(matcher=matcher, inspector=inspector))
    assert turnover.main(["pot.turnover", str(incoming), "--property", "demo-house"]) == 0
    run_dir = next((tmp_path / "data" / "runs").iterdir())
    (run_dir / "proof.json").unlink()
    capsys.readouterr()
    assert prove.main(["pot.prove", str(run_dir)]) == 0
    proof = json.loads((run_dir / "proof.json").read_text(encoding="utf-8"))
    assert proof["items"]["master-bedroom.beds_made"]["status"] == "proven"
    assert (run_dir / "evidence" / "master-bedroom.beds_made.jpg").is_file()
    out = capsys.readouterr().out
    assert "NOT READY" in out
    assert "Proven:" in out


def test_prove_missing_run_fails(tmp_path, capsys):
    assert prove.main(["pot.prove", str(tmp_path / "nope")]) == 1
