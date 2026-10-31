"""Offline tests for pot.requests with a fake client."""

from pot.checklist import build_checklist
from pot.meta_client import MetaClientError
from pot.proof import NEEDS_PROOF, PROBLEM, PROVEN, ProofItem
from pot.requests import write_requests
from tests.fakes import FakeSpark, tiny_property


def _proofs(statuses):
    return {
        item_id: ProofItem(item_id, status, "reason", "P01")
        for item_id, status in statuses.items()
    }


def test_requests_grouped_by_room_in_house_order():
    prop = tiny_property()
    checklist = build_checklist(prop)
    proofs = _proofs({
        "bath.towels": PROBLEM,
        "bed.beds_made": NEEDS_PROOF,
        "bed.no_belongings": PROVEN,
        "bed.floor_clear": NEEDS_PROOF,
        "bath.toilet_paper": PROVEN,
        "bath.toilet": PROVEN,
        "bath.shower": PROVEN,
        "bath.sink": PROVEN,
        "bed.coverage": PROVEN,
        "bath.coverage": PROVEN,
    })
    client = FakeSpark()
    requests, notes = write_requests(client, prop, checklist, proofs)
    assert [r["item_id"] for r in requests] == [
        "bed.beds_made", "bed.floor_clear", "bath.towels",
    ]
    assert len(client.calls) == 1
    assert "Spanish" in client.calls[0]["instructions"]
    assert all(r["cleaner"] and r["host"] for r in requests)
    assert requests[0]["room_name"] == "Bed"
    assert requests[0]["status"] == NEEDS_PROOF
    assert notes == []


def test_no_call_when_everything_proven():
    prop = tiny_property()
    checklist = build_checklist(prop)
    proofs = _proofs({item.id: PROVEN for item in checklist})
    client = FakeSpark()
    requests, notes = write_requests(client, prop, checklist, proofs)
    assert requests == [] and notes == []
    assert client.calls == []


def test_missing_item_falls_back_to_requirement():
    prop = tiny_property()
    checklist = build_checklist(prop)
    proofs = _proofs({"bed.beds_made": NEEDS_PROOF})

    def sparse(call):
        return {"requests": []}

    requests, notes = write_requests(FakeSpark(requester=sparse), prop, checklist, proofs)
    assert len(requests) == 1
    assert "bed" in requests[0]["cleaner"].lower()
    assert any("bed.beds_made" in note for note in notes)


def test_failed_call_falls_back_for_all(tmp_path):
    prop = tiny_property()
    checklist = build_checklist(prop)
    proofs = _proofs({"bed.beds_made": NEEDS_PROOF})

    def boom(call):
        raise MetaClientError("requests: boom")

    requests, notes = write_requests(FakeSpark(requester=boom), prop, checklist, proofs)
    assert len(requests) == 1
    assert any("check failed" in note for note in notes)
