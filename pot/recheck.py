"""Re-check one room with one new photo, live or from the CLI.

Usage: python -m pot.recheck <run folder> <photo file> --room <room id>

Three phases so photos checked at the same time never clash:
(a) RunState.claim_photo* numbers and saves the photo under the run lock;
(b) run_photo_checks() runs the model calls with no lock held;
(c) RunState.merge_result() merges under the lock, in arrival order.

Tour mode checks in parallel: the first look, the second looks and the
item SAMs all start together (the second look never sees the first
look's answer) and each problem-object SAM starts as soon as its second
look returns. The proof rule is unchanged. A proven item only changes
when a newer photo shows it is now not done.
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from pot import prompts
from pot.inspector import first_look
from pot.matcher import Assignment
from pot.meta_client import MetaClient, MetaClientError, submit_with_context
from pot.proof import PROBLEM, PROVEN, prove_all, verdict
from pot.requests import write_requests
from pot.runs import RunState
from pot.turnover import print_requests
from pot.verifier import (
    MAX_WORKERS,
    check_sam,
    check_second_look,
    segment_problem_objects,
    verify,
)


def _fresh_ids_for(claims, photo_id):
    return [
        item_id for item_id, claim in claims.items()
        if claim.status in ("done", "problem") and claim.photo_id == photo_id
    ]


def check_photo_sequential(client, photo, room_id, rooms, items, effort=None):
    """First look, then verify; returns a fresh dict for merge_result."""
    effort = prompts.RECHECK_EFFORT if effort is None else effort
    checked = [item for item in items if item.key != "coverage"]
    assignment = Assignment(photo.id, room_id, "high", "")
    fresh = first_look(
        client, [photo], {photo.id: assignment}, checked, rooms, effort=effort
    )
    fresh_ids = _fresh_ids_for(fresh.claims, photo.id)
    seconds, sams, decisions = {}, {}, {}
    notes = list(fresh.notes)
    if fresh_ids:
        verification = verify(
            client, checked,
            {item_id: fresh.claims[item_id] for item_id in fresh_ids},
            [photo], rooms, {photo.id: assignment}, effort=effort,
        )
        seconds.update(verification.second_looks)
        sams.update(verification.sams)
        decisions.update(verification.room_decisions)
        notes.extend(verification.notes)
    return {
        "claims": dict(fresh.claims),
        "seconds": seconds,
        "sams": sams,
        "problem_sams": {},
        "issues": list(fresh.other_issues),
        "notes": notes,
        "fresh_ids": fresh_ids,
        "decisions": decisions,
    }


def check_photo_parallel(client, photo, room_id, rooms, items, effort=None):
    """First look + second looks + item SAMs together; problem SAM chained.

    Everything starts at once; each problem-object SAM starts as soon as
    its second look returns. Same proof inputs as the sequential path.
    """
    effort = prompts.RECHECK_EFFORT if effort is None else effort
    room = rooms.get(room_id, {})
    room_name = room.get("name") or room_id
    room_desc = room.get("description")
    checked = [item for item in items if item.key != "coverage"]
    assignment = Assignment(photo.id, room_id, "high", "")
    claims, seconds, sams, problem_sams = {}, {}, {}, {}
    issues, notes = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        pending = {}
        pending[submit_with_context(
            pool, first_look, client, [photo], {photo.id: assignment},
            checked, rooms, effort,
        )] = ("first", None)
        for item in checked:
            name = room_name if item.room_id != "house" else item.room_name
            desc = room_desc if item.room_id != "house" else None
            pending[submit_with_context(
                pool, check_second_look, client, item, photo, name, desc, effort
            )] = ("second", item)
            if item.sam_prompt:
                pending[submit_with_context(
                    pool, check_sam, client, item.id, photo, item.sam_prompt,
                    f"sam {item.id} {photo.id} ({item.sam_prompt})",
                )] = ("sam", item)
        claims_known = False
        chained = set()

        def maybe_chain(item_id):
            if item_id in chained or not claims_known:
                return
            claim = claims.get(item_id)
            second = seconds.get(item_id)
            if (
                claim is not None and claim.status == "problem"
                and second is not None and second.verdict == "not_done"
                and second.problem_object
            ):
                chained.add(item_id)
                pending[submit_with_context(
                    pool, check_sam, client, item_id, photo, second.problem_object,
                    f"sam problem {item_id} {photo.id} ({second.problem_object})",
                )] = ("problem", item_id)

        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for future in done:
                kind, ref = pending.pop(future)
                if kind == "first":
                    result = future.result()
                    claims.update(result.claims)
                    issues.extend(result.other_issues)
                    notes.extend(result.notes)
                    claims_known = True
                    for item_id in seconds:
                        maybe_chain(item_id)
                elif kind == "second":
                    second, job_notes = future.result()
                    seconds[ref.id] = second
                    notes.extend(job_notes)
                    maybe_chain(ref.id)
                elif kind == "sam":
                    sam, job_notes = future.result()
                    sams[ref.id] = sam
                    notes.extend(job_notes)
                else:
                    sam, job_notes = future.result()
                    problem_sams[ref] = sam
                    notes.extend(job_notes)
    return {
        "claims": claims,
        "seconds": seconds,
        "sams": sams,
        "problem_sams": problem_sams,
        "issues": issues,
        "notes": notes,
        "fresh_ids": _fresh_ids_for(claims, photo.id),
        "decisions": {},
    }


def run_photo_checks(client, state, photo_id, items, parallel, effort=None):
    """Phase (b): lock-free model calls for one claimed photo.

    Returns the fresh dict for RunState.merge_result. The sequential path
    segments problem objects after a preliminary prove; the parallel path
    already chained them.
    """
    photo = state.photos_by_id[photo_id]
    room_id = state.assignments[photo_id].room_id
    if parallel:
        return check_photo_parallel(client, photo, room_id, state.rooms, items, effort)
    fresh = check_photo_sequential(client, photo, room_id, state.rooms, items, effort)
    if fresh["fresh_ids"]:
        snap = state.snapshot()
        acc_claims = dict(snap["claims"])
        acc_seconds = dict(snap["seconds"])
        acc_sams = dict(snap["sams"])
        for item_id in fresh["fresh_ids"]:
            acc_claims[item_id] = fresh["claims"][item_id]
            if item_id in fresh["seconds"]:
                acc_seconds[item_id] = fresh["seconds"][item_id]
            if item_id in fresh["sams"]:
                acc_sams[item_id] = fresh["sams"][item_id]
        prelim = prove_all(
            state.checklist, acc_claims, acc_seconds, acc_sams,
            snap["assignments"], snap["coverage"],
            [photo.id for photo in state.photos], snap["decisions"], state.rooms,
        )
        candidates = {
            item_id: prelim[item_id] for item_id in fresh["fresh_ids"]
            if prelim[item_id].status == PROBLEM
        }
        if candidates:
            found, problem_notes = segment_problem_objects(
                client, candidates, acc_seconds, {photo.id: photo}
            )
            fresh["problem_sams"].update(found)
            fresh["notes"].extend(problem_notes)
    return fresh


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
    state = RunState.load(run_dir)
    if room_id not in state.rooms:
        raise ValueError(
            f"Unknown room '{room_id}' for property '{state.property['id']}'"
        )
    photo, _seq = state.claim_photo_for_room(photo_file, room_id)
    subset = [
        item for item in state.checklist
        if item.room_id == room_id and item.key != "coverage"
        and state.proofs[item.id].status != PROVEN
    ]
    fresh = run_photo_checks(client, state, photo.id, subset, parallel=False, effort=effort)
    merged = state.merge_result(photo.id, fresh)
    requests, request_notes = write_requests(
        client, state.property, state.checklist, state.proofs, effort=effort
    )
    state.store_requests(requests, request_notes)
    host_verdict = verdict(state.checklist, state.proofs)
    return {
        "photo_id": photo.id,
        "changes": merged["changes"],
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
        print(f"No changes.")
    print("READY" if summary["verdict"].ready else "NOT READY")
    print_requests(summary["requests"])
    print(f"Re-check took {summary['elapsed_seconds']:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
