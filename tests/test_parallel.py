"""Parallel tour checks equal the sequential path (same proof inputs)."""

from pathlib import Path

from PIL import Image

from pot.checklist import build_checklist
from pot.proof import PROBLEM, PROVEN
from pot.recheck import check_photo_parallel, check_photo_sequential
from pot.runs import RunState
from tests.fakes import FakeSpark, tiny_property


def _upload(directory, name, color="blue"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    Image.new("RGB", (64, 48), color).save(path, "JPEG")
    return path


def _state(directory):
    property = tiny_property()
    checklist = build_checklist(property)
    bed = [i.id for i in checklist if i.room_id == "bed" and i.key != "coverage"]
    tour = {"shots": [{
        "shot_id": "bed-1", "room_id": "bed", "item_ids": bed,
        "instruction_es": "Foto", "instruction_en": "Photo",
    }]}
    run_dir = Path(directory) / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunState(run_dir, property, checklist, tour=tour)


def _done_inspector(photo_id):
    def answer(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        return {
            "claims": [
                {"item_id": item_id, "status": "done", "photo_id": photo_id,
                 "seen": "looks good", "count_seen": 1}
                for item_id in enum
            ],
            "other_issues": [],
        }
    return answer


def _photos(directory):
    first = _state(directory / "a")
    second = _state(directory / "b")
    photo_a, _ = first.claim_photo(str(_upload(directory / "u1", "a.jpg")), "bed-1")
    photo_b, _ = second.claim_photo(str(_upload(directory / "u2", "b.jpg", "green")), "bed-1")
    return first, second, photo_a, photo_b


def test_parallel_matches_sequential_for_done_items(tmp_path):
    first, second, photo_a, photo_b = _photos(tmp_path)
    items = [i for i in first.checklist if i.room_id == "bed" and i.key != "coverage"]
    seq = check_photo_sequential(FakeSpark(inspector=_done_inspector("P01")),
                                 photo_a, "bed", first.rooms, items)
    par = check_photo_parallel(FakeSpark(inspector=_done_inspector("P01")),
                               photo_b, "bed", second.rooms, items)
    assert set(par["fresh_ids"]) == set(seq["fresh_ids"]) == {i.id for i in items}
    assert set(par["seconds"]) == set(seq["seconds"])
    assert set(par["sams"]) == set(seq["sams"])
    merged_seq = first.merge_result(photo_a.id, seq)
    merged_par = second.merge_result(photo_b.id, par)
    assert merged_par["changes"] == merged_seq["changes"]
    for item in items:
        assert second.proofs[item.id].status == PROVEN
        assert second.proofs[item.id].status == first.proofs[item.id].status


def test_parallel_chains_problem_sam_as_second_look_returns(tmp_path):
    first, _second_state, photo_a, _photo_b = _photos(tmp_path)
    items = [i for i in first.checklist if i.room_id == "bed" and i.key != "coverage"]

    def inspector(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        return {
            "claims": [
                {"item_id": item_id, "status": "problem", "photo_id": "P01",
                 "seen": "a mess", "count_seen": None}
                for item_id in enum
            ],
            "other_issues": [],
        }

    def verifier(call):
        return {"this_room": "yes", "verdict": "not_done", "count_seen": None,
                "reason": "a mess", "problem_object": "dirty pile"}

    client = FakeSpark(inspector=inspector, verifier=verifier)
    fresh = check_photo_parallel(client, photo_a, "bed", first.rooms, items)
    assert set(fresh["problem_sams"]) == {i.id for i in items}
    assert all(s.prompt == "dirty pile" for s in fresh["problem_sams"].values())
    assert any("sam problem" in c["purpose"] for c in client.sam_calls)
    merged = first.merge_result(photo_a.id, fresh)
    for item in items:
        assert first.proofs[item.id].status == PROBLEM
    assert set(merged["changes"]) == {i.id for i in items}


def test_parallel_skips_problem_sam_without_problem_object(tmp_path):
    first, _second_state, photo_a, _photo_b = _photos(tmp_path)
    items = [i for i in first.checklist if i.room_id == "bed" and i.key != "coverage"]

    def inspector(call):
        enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
        return {
            "claims": [
                {"item_id": item_id, "status": "problem", "photo_id": "P01",
                 "seen": "a mess", "count_seen": None}
                for item_id in enum
            ],
            "other_issues": [],
        }

    def verifier(call):
        return {"this_room": "yes", "verdict": "not_done", "count_seen": None,
                "reason": "a mess", "problem_object": ""}

    client = FakeSpark(inspector=inspector, verifier=verifier)
    fresh = check_photo_parallel(client, photo_a, "bed", first.rooms, items)
    assert fresh["problem_sams"] == {}
    assert not any("sam problem" in c["purpose"] for c in client.sam_calls)
