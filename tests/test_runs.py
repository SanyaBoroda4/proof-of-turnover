"""Tests for pot.runs: concurrent claims, arrival-order merge, tour state."""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from PIL.Image import Exif

from pot.checklist import build_checklist
from pot.inspector import Claim
from pot.proof import NEEDS_PROOF, PROBLEM, PROVEN
from pot.runs import NOT_YET, OldPhotoError, RunState
from pot.verifier import SamCheck, SecondLook
from tests.fakes import tiny_property


def _photo_file(directory, name="pic.jpg", color="blue", taken=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    image = Image.new("RGB", (64, 48), color)
    if taken is not None:
        exif = Exif()
        exif.get_ifd(0x8769)[36867] = taken
        image.save(path, "JPEG", exif=exif)
    else:
        image.save(path, "JPEG")
    return path


def _state(directory):
    property = tiny_property()
    checklist = build_checklist(property)
    tour = {"shots": []}
    for room_id in ("bed", "bath"):
        items = [i.id for i in checklist if i.room_id == room_id and i.key != "coverage"]
        tour["shots"].append({
            "shot_id": f"{room_id}-1", "room_id": room_id, "item_ids": items,
            "instruction_es": "Foto", "instruction_en": "Photo",
        })
    run_dir = Path(directory) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunState(run_dir, property, checklist, tour=tour)


def _fresh_done(photo_id, item_ids):
    return {
        "claims": {i: Claim(i, "done", photo_id, "looks good", 1) for i in item_ids},
        "seconds": {
            i: SecondLook(i, photo_id, "yes", "done", 5, "fine", "") for i in item_ids
        },
        "sams": {i: SamCheck(i, photo_id, "thing", 3, []) for i in item_ids},
        "problem_sams": {},
        "issues": [],
        "notes": [],
        "fresh_ids": list(item_ids),
        "decisions": {},
    }


def _fresh_problem(photo_id, item_id, reason):
    return {
        "claims": {item_id: Claim(item_id, "problem", photo_id, reason, None)},
        "seconds": {
            item_id: SecondLook(item_id, photo_id, "yes", "not_done", None, reason, "mess")
        },
        "sams": {},
        "problem_sams": {item_id: SamCheck(item_id, photo_id, "mess", 1, [])},
        "issues": [],
        "notes": [],
        "fresh_ids": [item_id],
        "decisions": {},
    }


def _fresh_unclear(photo_id, item_id):
    return {
        "claims": {item_id: Claim(item_id, "done", photo_id, "maybe", None)},
        "seconds": {
            item_id: SecondLook(item_id, photo_id, "unclear", "unclear", None, "", "")
        },
        "sams": {},
        "problem_sams": {},
        "issues": [],
        "notes": [],
        "fresh_ids": [item_id],
        "decisions": {},
    }


def _bed_item(state):
    return next(i.id for i in state.checklist if i.room_id == "bed" and i.key != "coverage")


def _bath_item(state):
    return next(i.id for i in state.checklist if i.room_id == "bath" and i.key != "coverage")


def test_concurrent_claims_get_distinct_ids_and_both_survive(tmp_path):
    state = _state(tmp_path)
    first = _photo_file(tmp_path / "up1", "a.jpg", "blue")
    second = _photo_file(tmp_path / "up2", "b.jpg", "green")
    bed_item = _bed_item(state)
    bath_item = _bath_item(state)
    results = {}

    def claim(name, upload, shot):
        results[name] = state.claim_photo(str(upload), shot)

    threads = [
        threading.Thread(target=claim, args=("a", first, "bed-1")),
        threading.Thread(target=claim, args=("b", second, "bath-1")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results["a"][0].id != results["b"][0].id
    state.merge_result(results["a"][0].id, _fresh_done(results["a"][0].id, [bed_item]))
    state.merge_result(results["b"][0].id, _fresh_done(results["b"][0].id, [bath_item]))
    assert state.proofs[bed_item].status == PROVEN
    assert state.proofs[bath_item].status == PROVEN
    saved = json.loads((state.run_dir / "first_look.json").read_text(encoding="utf-8"))
    assert len(saved["photos"]) == 2


def test_older_photo_finishing_last_does_not_overwrite(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_problem(second.id, item, "mess everywhere"))
    state.merge_result(first.id, _fresh_problem(first.id, item, "a stain"))
    proof = state.proofs[item]
    assert (proof.status, proof.photo_id, proof.reason) == (
        PROBLEM, second.id, "mess everywhere")


def test_unclear_result_never_erases_proven_or_problem(tmp_path):
    state = _state(tmp_path)
    proven_item = _bed_item(state)
    problem_item = _bath_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [proven_item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg")), "bath-1")
    state.merge_result(second.id, _fresh_problem(second.id, problem_item, "dirty"))
    third, _ = state.claim_photo(str(_photo_file(tmp_path / "u3", "c.jpg", "red")), "bed-1")
    state.merge_result(third.id, _fresh_unclear(third.id, proven_item))
    fourth, _ = state.claim_photo(str(_photo_file(tmp_path / "u4", "d.jpg", "red")), "bath-1")
    state.merge_result(fourth.id, _fresh_unclear(fourth.id, problem_item))
    assert state.proofs[proven_item].status == PROVEN
    assert state.proofs[proven_item].photo_id == first.id
    assert state.proofs[problem_item].status == PROBLEM
    assert state.proofs[problem_item].photo_id == second.id


def test_newer_problem_replaces_proven(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    merged = state.merge_result(second.id, _fresh_problem(second.id, item, "now dirty"))
    assert merged["changes"][item] == (PROVEN, PROBLEM)
    assert state.proofs[item].photo_id == second.id


def test_status_marks_untaken_shots_not_yet(tmp_path):
    state = _state(tmp_path)
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    assert all(i["status"] == NOT_YET for i in bed["items"] if i["id"] != "bed.coverage")
    assert status["agent_state"] == "needs_input"
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [_bed_item(state)]))
    status = state.status()
    bath = next(r for r in status["rooms"] if r["id"] == "bath")
    assert all(i["status"] == NOT_YET for i in bath["items"] if i["id"] != "bath.coverage")
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    assert bed["items"][1]["status"] == PROVEN
    rest = [i for i in bed["items"][2:] if i["status"] == "needs photo"]
    assert rest and all(i["reason"] == "No photo shows it" for i in rest)
    assert status["progress"]["shots_taken"] == 1
    assert status["current_shot"]["shot_id"] == "bath-1"


def test_photo_stays_checking_until_finished(tmp_path):
    state = _state(tmp_path)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    assert photo.id in state.status()["checking"]
    state.merge_result(photo.id, _fresh_done(photo.id, [_bed_item(state)]))
    assert photo.id in state.status()["checking"]
    state.finish_photo(photo.id)
    assert photo.id not in state.status()["checking"]


def test_coverage_proven_at_claim_before_merge(tmp_path):
    state = _state(tmp_path)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    assert state.proofs["bed.coverage"].status == PROVEN
    assert state.proofs["bed.coverage"].photo_id == photo.id
    assert state.proofs["bath.coverage"].status == NEEDS_PROOF


def test_old_photo_refused_but_missing_or_new_time_allowed(tmp_path):
    state = _state(tmp_path)
    old = (datetime.now() - timedelta(days=1)).strftime("%Y:%m:%d %H:%M:%S")
    with pytest.raises(OldPhotoError, match="foto antigua"):
        state.claim_photo(str(_photo_file(tmp_path / "u1", "old.jpg", taken=old)), "bed-1")
    fresh = (state.started_at + timedelta(seconds=5)).strftime("%Y:%m:%d %H:%M:%S")
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u2", "new.jpg", taken=fresh)), "bed-1"
    )
    assert photo.id == "P01"
    photo2, _ = state.claim_photo(str(_photo_file(tmp_path / "u3", "plain.jpg")), "bed-1")
    assert photo2.id == "P02"


def test_claim_unknown_shot_raises(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError, match="Unknown shot"):
        state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "nope")


def test_send_back_returns_items_to_needs_photo(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    signoff = state.send_back([item], "Hazlo de nuevo")
    assert state.proofs[item].status == NEEDS_PROOF
    assert state.proofs[item].reason == "Hazlo de nuevo"
    assert state.agent_state() == "needs_input"
    assert signoff["decision"] == "sendback"
    assert signoff["items"] == [item]
    assert item in signoff["open_items"]
    saved = json.loads((state.run_dir / "signoff.json").read_text(encoding="utf-8"))
    assert saved["decision"] == "sendback"


def test_approve_marks_done(tmp_path):
    state = _state(tmp_path)
    signoff = state.approve()
    assert state.agent_state() == "done"
    assert signoff["decision"] == "approve"
    assert signoff["time"]


def test_older_photo_stores_nothing_when_verdict_kept(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_problem(second.id, item, "mess everywhere"))
    state.merge_result(first.id, _fresh_problem(first.id, item, "a stain"))
    assert state.claims[item].photo_id == second.id
    assert state.seconds[item].reason == "mess everywhere"
    saved = json.loads((state.run_dir / "proof.json").read_text(encoding="utf-8"))
    entry = saved["items"][item]
    assert entry["claim"]["photo_id"] == second.id
    assert entry["second_look"]["photo_id"] == second.id
    assert entry["second_look"]["reason"] == "mess everywhere"


def test_unclear_result_keeps_old_second_look(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    first, _ = state.claim_photo(str(_photo_file(tmp_path / "u1", "a.jpg")), "bed-1")
    state.merge_result(first.id, _fresh_done(first.id, [item]))
    second, _ = state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", "green")), "bed-1")
    state.merge_result(second.id, _fresh_unclear(second.id, item))
    assert state.seconds[item].photo_id == first.id
    assert state.seconds[item].verdict == "done"
    saved = json.loads((state.run_dir / "proof.json").read_text(encoding="utf-8"))
    assert saved["items"][item]["second_look"]["photo_id"] == first.id


def test_old_photo_allows_15_minutes_clock_grace(tmp_path):
    state = _state(tmp_path)
    close = (state.started_at - timedelta(minutes=5)).strftime("%Y:%m:%d %H:%M:%S")
    photo, _ = state.claim_photo(
        str(_photo_file(tmp_path / "u1", "a.jpg", taken=close)), "bed-1"
    )
    assert photo.id == "P01"
    far = (state.started_at - timedelta(minutes=16)).strftime("%Y:%m:%d %H:%M:%S")
    with pytest.raises(OldPhotoError, match="foto antigua"):
        state.claim_photo(str(_photo_file(tmp_path / "u2", "b.jpg", taken=far)), "bed-1")


def test_news_reports_changes_requests_and_signoff(tmp_path):
    state = _state(tmp_path)
    item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    state.merge_result(photo.id, _fresh_problem(photo.id, item, "mess everywhere"))
    state.store_requests(
        [{"item_id": item, "room_id": "bed", "room_name": "Bed",
          "status": PROBLEM, "cleaner": "Limpia", "host": "Clean"}], [],
    )
    state.approve()
    news = state.status()["news"]
    assert [e["kind"] for e in news] == ["signoff", "request", "status"]
    assert news[0]["text"] == "Host approved the turnover"
    assert news[1]["item_id"] == item and news[1]["photo_id"] == photo.id
    assert news[1]["text"] == "request sent: Clean"
    assert news[2]["status"] == PROBLEM and news[2]["room_name"] == "Bed"
    assert news[2]["photo_id"] == photo.id
    import re

    assert all(re.fullmatch(r"\d{2}:\d{2}:\d{2}", e["time"]) for e in news)


def test_has_evidence_only_when_file_exists(tmp_path):
    state = _state(tmp_path)
    bed_item = _bed_item(state)
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "bed-1")
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    coverage = next(i for i in bed["items"] if i["id"] == "bed.coverage")
    assert coverage["status"] == "proven"
    assert coverage["has_evidence"] is False
    state.merge_result(photo.id, _fresh_done(photo.id, [bed_item]))
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    item = next(i for i in bed["items"] if i["id"] == bed_item)
    assert item["has_evidence"] is True
    state.send_back([bed_item], "again")
    status = state.status()
    bed = next(r for r in status["rooms"] if r["id"] == "bed")
    item = next(i for i in bed["items"] if i["id"] == bed_item)
    assert item["has_evidence"] is False


def test_load_roundtrip_restores_state(tmp_path):
    from pot.checklist import load_property
    from pot.tour import load_tour

    property = load_property("demo-house")
    checklist = build_checklist(property)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, checklist,
                     tour={"shots": load_tour("demo-house")})
    item = "master-bedroom.beds_made"
    photo, _ = state.claim_photo(str(_photo_file(tmp_path / "u", "a.jpg")), "master-bedroom-1")
    state.merge_result(photo.id, _fresh_done(photo.id, [item]))
    state.record_call({"purpose": "second look", "model": "m", "photo_id": photo.id,
                       "seconds": 1.0, "attempts": 1, "source": "live"})
    (state.run_dir / "calls.jsonl").write_text(
        json.dumps(state.calls[0]) + "\n", encoding="utf-8"
    )
    loaded = RunState.load(state.run_dir)
    assert loaded.proofs[item].status == PROVEN
    assert loaded.coverage["master-bedroom"] == "covered"
    assert loaded.taken_shots() == ["master-bedroom-1"]
    assert loaded.call_log()[0]["purpose"] == "second look"
    assert loaded.status()["progress"]["photos"] == 1
    # Arrival order survives the restart: an older result stays ignored.
    assert loaded.decided_seq[item] == 0
