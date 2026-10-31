"""Record a small tour, then replay it with POT_REPLAY=1 and no key.

Uses the real MetaClient with a stubbed OpenAI transport and generated
test images (no real photos). The replay must serve every call from the
cache: any cache miss fails the test.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from pot import prompts
from pot.checklist import build_checklist
from pot.meta_client import MetaClient
from pot.proof import PROBLEM, PROVEN
from pot.recheck import run_photo_checks
from pot.requests import write_requests
from pot.runs import RunState
from tests.fakes import SAM_FIXTURE, photo_ids_in, tiny_property


def _upload(directory, name, color):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    Image.new("RGB", (64, 48), color).save(path, "JPEG")
    return path


def _answer(kwargs):
    """Answer like the API: bed proves, bath fails, SAM finds boxes."""
    if kwargs.get("model", "").startswith("sam"):
        return SAM_FIXTURE
    schema = (kwargs.get("text") or {}).get("format", {}).get("schema", {})
    props = schema.get("properties", {})
    if "verdict" in props:
        if "Room: Bath" in (kwargs.get("instructions") or ""):
            return {"this_room": "yes", "verdict": "not_done", "count_seen": None,
                    "reason": "a mess", "problem_object": "dirty pile"}
        return {"this_room": "yes", "verdict": "done", "count_seen": 5,
                "reason": "fine", "problem_object": ""}
    if "requests" in props:
        enum = props["requests"]["items"]["properties"]["item_id"]["enum"]
        return {"requests": [
            {"item_id": i, "cleaner": f"Foto {i}", "host": f"Photo {i}"}
            for i in enum
        ]}
    if "boxes" in props:
        import re

        match = re.search(r"(\d+) numbered boxes", kwargs.get("instructions") or "")
        count = int(match.group(1)) if match else 0
        return {"boxes": list(range(1, count + 1))}
    enum = props["claims"]["items"]["properties"]["item_id"]["enum"]
    pid = photo_ids_in(kwargs["input"][0]["content"])[0]
    claims = []
    for item_id in enum:
        if item_id.startswith("bath"):
            claims.append({"item_id": item_id, "status": "problem", "photo_id": pid,
                           "seen": "a mess", "count_seen": None})
        else:
            claims.append({"item_id": item_id, "status": "done", "photo_id": pid,
                           "seen": "looks good", "count_seen": 1})
    return {"claims": claims, "other_issues": []}


class _StubResponses:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = _answer(kwargs)
        text = outcome if isinstance(outcome, str) else json.dumps(outcome)
        return SimpleNamespace(output_text=text, usage=None)


class _StubOpenAI:
    def __init__(self):
        self.responses = _StubResponses()


class _ExplodingOpenAI:
    class responses:
        @staticmethod
        def create(**kwargs):
            raise AssertionError("replay must not touch the network")


def _tour(checklist):
    shots = []
    for room_id in ("bed", "bath"):
        items = [i.id for i in checklist if i.room_id == room_id and i.key != "coverage"]
        shots.append({"shot_id": f"{room_id}-1", "room_id": room_id,
                      "item_ids": items, "instruction_es": "Foto",
                      "instruction_en": "Photo"})
    return {"shots": shots}


def _run_tour(client, run_dir, uploads_dir, property, checklist, tour):
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(run_dir, property, checklist, tour=tour)
    colors = ["blue", "green"]
    for index, shot in enumerate(tour["shots"]):
        upload = _upload(uploads_dir / f"u{index}", f"p{index}.jpg", colors[index])
        photo, _seq = state.claim_photo(str(upload), shot["shot_id"])
        items = [state.items_by_id[i] for i in shot["item_ids"]]
        fresh = run_photo_checks(client, state, photo.id, items, parallel=True,
                                 effort=prompts.RECHECK_EFFORT)
        merged = state.merge_result(photo.id, fresh)
        if merged["failed"]:
            requests, notes = write_requests(
                client, property, checklist, state.proofs,
                effort=prompts.RECHECK_EFFORT, item_ids=set(merged["failed"]))
            state.store_requests(requests, notes)
    return state


def test_record_then_replay_with_zero_cache_misses(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    property = tiny_property()
    checklist = build_checklist(property)
    tour = _tour(checklist)
    cache_dir = tmp_path / "cache"

    record_client = MetaClient(openai_client=_StubOpenAI(), cache_dir=cache_dir)
    record_state = _run_tour(record_client, tmp_path / "run1", tmp_path / "up1",
                             property, checklist, tour)
    assert record_client.trace
    # All calls succeed; identical concurrent requests may already hit.
    assert all(e["ok"] for e in record_client.trace)
    bed_item = next(i.id for i in checklist if i.room_id == "bed" and i.key != "coverage")
    bath_item = next(i.id for i in checklist if i.room_id == "bath" and i.key != "coverage")
    assert record_state.proofs[bed_item].status == PROVEN
    assert record_state.proofs[bath_item].status == PROBLEM
    assert record_state.requests, "a failing shot must record requests"

    replay_client = MetaClient(openai_client=_ExplodingOpenAI(),
                               cache_dir=cache_dir, replay=True)
    replay_state = _run_tour(replay_client, tmp_path / "run2", tmp_path / "up2",
                             property, checklist, tour)
    assert replay_client.trace
    assert all(e["cache"] == "hit" for e in replay_client.trace), [
        e["purpose"] for e in replay_client.trace if e["cache"] != "hit"
    ]
    for item in checklist:
        assert (replay_state.proofs[item.id].status ==
                record_state.proofs[item.id].status)
    assert replay_state.requests == record_state.requests
