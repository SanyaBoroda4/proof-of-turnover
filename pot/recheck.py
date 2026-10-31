"""Re-check one room with one new photo.

Usage: python -m pot.recheck <run folder> <photo file> --room <room id>

Adds the photo to the run, runs the first look on it for the room's
not-yet-proven items, then second look, SAM and the proof rule for the
new claims. Updates proof.json, evidence, requests and the verdict.
A proven item only changes if the new photo shows it is now not done.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from pot import prompts
from pot.inspector import OtherIssue, first_look
from pot.matcher import Assignment
from pot.meta_client import MetaClient, MetaClientError
from pot.photos import ingest_file
from pot.proof import PROBLEM, PROVEN, ProofItem, prune_other_issues, prove_all, save_evidence, verdict
from pot.prove import load_run
from pot.requests import write_requests
from pot.turnover import build_proof_result, print_requests
from pot.verifier import SamCheck, SecondLook, segment_problem_objects, verify


def _next_photo_id(photos):
    taken = set()
    for photo in photos:
        if photo.id.startswith("P") and photo.id[1:].isdigit():
            taken.add(int(photo.id[1:]))
    return f"P{(max(taken) + 1) if taken else 1:02d}"


def _rebuild_saved(proof_data):
    proofs, seconds, sams, problem_sams = {}, {}, {}, {}
    for item_id, entry in proof_data["items"].items():
        proofs[item_id] = ProofItem(
            item_id=item_id, status=entry["status"],
            reason=entry["reason"], photo_id=entry["photo_id"],
        )
        if entry.get("second_look") is not None:
            seconds[item_id] = SecondLook(**entry["second_look"])
        if entry.get("sam") is not None:
            sam = entry["sam"]
            sams[item_id] = SamCheck(item_id, entry["photo_id"], sam["prompt"],
                                     sam["count"], sam["boxes"])
        if entry.get("problem_sam") is not None:
            sam = entry["problem_sam"]
            problem_sams[item_id] = SamCheck(item_id, entry["photo_id"], sam["prompt"],
                                             sam["count"], sam["boxes"])
    return proofs, seconds, sams, problem_sams


def recheck_photo(client, run_dir, photo_file, room_id, effort=None):
    """Add one photo for one room and re-check; returns a summary dict."""
    started = time.monotonic()
    effort = prompts.RECHECK_EFFORT if effort is None else effort
    run_dir = Path(run_dir)
    first_path = run_dir / "first_look.json"
    proof_path = run_dir / "proof.json"
    if not first_path.is_file():
        raise ValueError(f"No first_look.json in {run_dir}")
    if not proof_path.is_file():
        raise ValueError(f"No proof.json in {run_dir}; run the full turnover first")
    outcome = load_run(run_dir)
    property = outcome["property"]
    rooms = {room["id"]: room for room in property["rooms"]}
    if room_id not in rooms:
        raise ValueError(f"Unknown room '{room_id}' for property '{property['id']}'")
    checklist = outcome["checklist"]
    photos = outcome["photos"]
    match = outcome["match"]
    look = outcome["look"]

    new_id = _next_photo_id(photos)
    photo = ingest_file(photo_file, run_dir / "photos", new_id)
    photos.append(photo)
    match.assignments[new_id] = Assignment(new_id, room_id, "high", "")
    match.coverage[room_id] = "covered"

    proof_data = json.loads(proof_path.read_text(encoding="utf-8"))
    old_proofs, seconds, sams, problem_sams = _rebuild_saved(proof_data)
    subset = [
        item for item in checklist
        if item.room_id == room_id and item.key != "coverage"
        and old_proofs.get(item.id, ProofItem(item.id, "needs proof", "", "")).status != PROVEN
    ]
    new_issues = []
    fresh_ids = set()
    recheck_notes = [f"Re-check {new_id} ({photo.original_name}) added for {room_id}"]
    if subset:
        fresh = first_look(client, [photo], {new_id: match.assignments[new_id]},
                           subset, rooms, effort=effort)
        recheck_notes.extend(fresh.notes)
        new_issues.extend(fresh.other_issues)
        for item_id, claim in fresh.claims.items():
            if claim.status in ("done", "problem"):
                look.claims[item_id] = claim
                fresh_ids.add(item_id)
    if fresh_ids:
        verification = verify(
            client, checklist,
            {item_id: look.claims[item_id] for item_id in fresh_ids},
            [photo], rooms, match.assignments, effort=effort,
        )
        recheck_notes.extend(verification.notes)
        seconds.update(verification.second_looks)
        sams.update(verification.sams)
    else:
        verification = None

    fresh_proofs = prove_all(
        checklist, look.claims, seconds, sams, match.assignments,
        match.coverage, [photo.id for photo in photos], {}, rooms,
    )
    proofs = {}
    for item in checklist:
        old = old_proofs[item.id]
        new = fresh_proofs[item.id]
        if item.id in fresh_ids and (old.status != PROVEN or new.status == PROBLEM):
            proofs[item.id] = new
        else:
            proofs[item.id] = old
    photos_by_id = {photo.id: photo for photo in photos}
    need_problem_sam = {}
    for item_id, proof in proofs.items():
        if proof.status != PROBLEM:
            continue
        old_sam = problem_sams.get(item_id)
        if old_sam is not None and old_proofs[item_id].photo_id == proof.photo_id:
            continue
        need_problem_sam[item_id] = proof
    if need_problem_sam:
        found, problem_notes = segment_problem_objects(
            client, need_problem_sam, seconds, photos_by_id
        )
        recheck_notes.extend(problem_notes)
        problem_sams.update(found)
    problem_sams = {item_id: problem_sams[item_id] for item_id in problem_sams
                    if proofs[item_id].status == PROBLEM}
    old_issues = proof_data.get("other_issues", [])
    merged_issues = [OtherIssue(**issue) for issue in old_issues] + new_issues
    pruned = prune_other_issues(merged_issues, proofs, look.claims)
    host_verdict = verdict(checklist, proofs)
    requests, request_notes = write_requests(client, property, checklist, proofs, effort=effort)
    recheck_notes.extend(request_notes)
    (run_dir / "requests.json").write_text(
        json.dumps({"requests": requests, "notes": request_notes}, indent=2), encoding="utf-8"
    )
    result = build_proof_result(
        checklist, photos, proofs, look.claims, seconds, sams, problem_sams,
        host_verdict, pruned, proof_data.get("notes", []) + recheck_notes,
    )
    proof_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    save_evidence(
        run_dir, proofs, {item.id: item for item in checklist},
        photos_by_id, sams, problem_sams,
    )
    first_data = json.loads(first_path.read_text(encoding="utf-8"))
    first_data["photos"].append({
        "id": new_id, "file": photo.original_name, "room_id": room_id,
        "confidence": "high", "seen": "",
    })
    first_data["coverage"][room_id] = "covered"
    for item_id in fresh_ids:
        first_data["claims"][item_id] = asdict(look.claims[item_id])
    first_data.setdefault("other_issues", []).extend(asdict(i) for i in new_issues)
    first_data.setdefault("notes", []).extend(recheck_notes)
    first_path.write_text(json.dumps(first_data, indent=2), encoding="utf-8")

    changes = {
        item.id: (old_proofs[item.id].status, proofs[item.id].status)
        for item in checklist
        if old_proofs[item.id].status != proofs[item.id].status
    }
    return {
        "photo_id": new_id,
        "changes": changes,
        "verdict": host_verdict,
        "requests": requests,
        "elapsed_seconds": time.monotonic() - started,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pot.recheck")
    parser.add_argument("run")
    parser.add_argument("photo")
    parser.add_argument("--room", required=True)
    args = parser.parse_args(argv[1:])
    try:
        client = MetaClient()
        summary = recheck_photo(client, args.run, args.photo, args.room)
    except (ValueError, MetaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if summary["changes"]:
        for item_id, (old, new) in summary["changes"].items():
            print(f"{item_id}: {old} -> {new}")
    else:
        print("No changes.")
    print("READY" if summary["verdict"].ready else "NOT READY")
    print_requests(summary["requests"])
    print(f"Re-check took {summary['elapsed_seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
