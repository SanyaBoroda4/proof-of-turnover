"""Run the full turnover: match photos, first look, then proof.

Usage: python -m pot.turnover <photo folder> --property demo-house

Loads the property, checklist and photos into a new data/runs/<timestamp>/
folder, matches rooms, runs the first look and the proof stage, and saves
first_look.json, proof.json and evidence/ images.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from pot.checklist import PropertyError, build_checklist, load_property
from pot.inspector import first_look
from pot.matcher import match_rooms
from pot.meta_client import MetaClient, MetaClientError
from pot.photos import load_photo_set, new_run_dir
from pot.proof import PROVEN, prune_other_issues, prove_all, save_evidence, verdict
from pot.requests import write_requests
from pot.verifier import segment_problem_objects, verify


def _sam_json(sam):
    if sam is None:
        return None
    return {"prompt": sam.prompt, "count": sam.count, "boxes": sam.boxes}


def build_proof_result(checklist, photos, proofs, claims, seconds, sams, problem_sams,
                       host_verdict, other_issues, notes):
    """Assemble the proof.json dict (shared by turnover, prove, recheck)."""
    photos_by_id = {photo.id: photo for photo in photos}
    items = {}
    for item in checklist:
        proof = proofs[item.id]
        photo = photos_by_id.get(proof.photo_id)
        claim = claims.get(item.id)
        second = seconds.get(item.id)
        items[item.id] = {
            "status": proof.status,
            "reason": proof.reason,
            "photo_id": proof.photo_id,
            "file": photo.original_name if photo is not None else None,
            "claim": asdict(claim) if claim is not None else None,
            "second_look": asdict(second) if second is not None else None,
            "sam": _sam_json(sams.get(item.id)),
            "problem_sam": _sam_json(problem_sams.get(item.id)),
        }
    return {
        "verdict": {
            "ready": host_verdict.ready,
            "problems": host_verdict.problems,
            "need_photo": host_verdict.need_photo,
            "worth_a_look": host_verdict.worth_a_look,
        },
        "items": items,
        "other_issues": [asdict(issue) for issue in other_issues],
        "notes": list(notes),
    }


def print_requests(requests):
    print(f"Requests ({len(requests)}):")
    current_room = None
    for request in requests:
        if request["room_name"] != current_room:
            current_room = request["room_name"]
            print(f"  {current_room}:")
        print(f"    - {request['cleaner']} (host: {request['host']})")


def run_proof(client, run_dir, property, checklist, photos, match, look):
    """Run verify + proof rule + evidence + requests; save JSONs; return data."""
    run_dir = Path(run_dir)
    rooms = {room["id"]: room for room in property["rooms"]}
    verification = verify(client, checklist, look.claims, photos, rooms, match.assignments)
    proofs = prove_all(
        checklist,
        look.claims,
        verification.second_looks,
        verification.sams,
        match.assignments,
        match.coverage,
        [photo.id for photo in photos],
        verification.room_decisions,
        rooms,
    )
    photos_by_id = {photo.id: photo for photo in photos}
    problem_sams, problem_notes = segment_problem_objects(
        client, proofs, verification.second_looks, photos_by_id
    )
    verification.notes.extend(problem_notes)
    pruned = prune_other_issues(look.other_issues, proofs, look.claims)
    host_verdict = verdict(checklist, proofs)
    requests, request_notes = write_requests(client, property, checklist, proofs)
    (run_dir / "requests.json").write_text(
        json.dumps({"requests": requests, "notes": request_notes}, indent=2), encoding="utf-8"
    )
    result = build_proof_result(
        checklist, photos, proofs, look.claims, verification.second_looks,
        verification.sams, problem_sams, host_verdict, pruned,
        match.notes + look.notes + verification.notes + request_notes,
    )
    (run_dir / "proof.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    save_evidence(
        run_dir, proofs, {item.id: item for item in checklist},
        photos_by_id, verification.sams, problem_sams,
    )
    return {
        "proofs": proofs,
        "verdict": host_verdict,
        "other_issues": pruned,
        "verification": verification,
        "problem_sams": problem_sams,
        "requests": requests,
    }


def run_turnover(photo_folder, property_id, client, run_dir=None):
    """Run matching + first look + proof; save JSONs; return the result."""
    started = time.monotonic()
    property = load_property(property_id)
    checklist = build_checklist(property)
    run_dir = Path(run_dir) if run_dir is not None else new_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)
    photos = load_photo_set(photo_folder, run_dir)
    match = match_rooms(client, photos, property)
    look = first_look(
        client,
        photos,
        match.assignments,
        checklist,
        rooms={room["id"]: room for room in property["rooms"]},
    )
    result = {
        "property": property_id,
        "photos": [
            {
                "id": photo.id,
                "file": photo.original_name,
                "room_id": match.assignments[photo.id].room_id,
                "confidence": match.assignments[photo.id].confidence,
                "seen": match.assignments[photo.id].seen,
            }
            for photo in photos
        ],
        "coverage": match.coverage,
        "claims": {item_id: asdict(claim) for item_id, claim in look.claims.items()},
        "other_issues": [asdict(issue) for issue in look.other_issues],
        "notes": match.notes + look.notes,
    }
    (run_dir / "first_look.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    proof = run_proof(client, run_dir, property, checklist, photos, match, look)
    return {
        "run_dir": run_dir,
        "property": property,
        "checklist": checklist,
        "photos": photos,
        "match": match,
        "look": look,
        "proof": proof,
        "elapsed_seconds": time.monotonic() - started,
    }


def _trace_totals(trace):
    seconds = sum(entry.get("seconds", 0) for entry in trace)
    cached = sum(1 for entry in trace if entry.get("cache") == "hit")
    tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for entry in trace:
        usage = entry.get("usage") or {}
        for key in tokens:
            value = usage.get(key)
            if isinstance(value, int):
                tokens[key] += value
    return len(trace), cached, seconds, tokens


def print_report(outcome, client):
    proofs = outcome["proof"]["proofs"]
    host_verdict = outcome["proof"]["verdict"]
    photos_by_id = {photo.id: photo for photo in outcome["photos"]}
    print("READY" if host_verdict.ready else "NOT READY")

    def show(title, ids):
        print(f"{title} ({len(ids)}):")
        for item_id in ids:
            proof = proofs[item_id]
            photo = photos_by_id.get(proof.photo_id)
            where = f" [{photo.original_name}]" if photo is not None else ""
            print(f"  {item_id}: {proof.reason}{where}")

    show("Problems", host_verdict.problems)
    show("Need a photo", host_verdict.need_photo)
    show("Worth a look", host_verdict.worth_a_look)
    print_requests(outcome["proof"]["requests"])
    proven = sum(1 for proof in proofs.values() if proof.status == PROVEN)
    print(f"Proven: {proven}/{len(proofs)} items")
    calls, cached, model_seconds, tokens = _trace_totals(client.trace)
    print(f"Meta calls: {calls} ({cached} from cache)")
    print(f"Time: {outcome['elapsed_seconds']:.1f} s")
    print(f"Model time: {model_seconds:.1f} s")
    print(
        f"Tokens: {tokens['input_tokens']} in / {tokens['output_tokens']} out / "
        f"{tokens['total_tokens']} total"
    )


def main(argv):
    parser = argparse.ArgumentParser(prog="pot.turnover")
    parser.add_argument("folder")
    parser.add_argument("--property", default="demo-house")
    args = parser.parse_args(argv[1:])
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 1
    try:
        client = MetaClient()
        outcome = run_turnover(folder, args.property, client)
    except (PropertyError, MetaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Run: {outcome['run_dir']}")
    print_report(outcome, client)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
