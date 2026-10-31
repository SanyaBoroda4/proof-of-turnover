"""Run the first look: match photos to rooms and claim checklist items.

Usage: python -m pot.turnover <photo folder> --property demo-house

Loads the property, checklist and photos into a new data/runs/<timestamp>/
folder, matches rooms, runs the first look and saves first_look.json.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from pot.checklist import PropertyError, build_checklist, load_property
from pot.inspector import first_look
from pot.matcher import match_rooms
from pot.meta_client import MetaClient, MetaClientError
from pot.photos import load_photo_set, new_run_dir


def run_turnover(photo_folder, property_id, client, run_dir=None):
    """Run matching + first look; save first_look.json; return the result."""
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
    return {
        "run_dir": run_dir,
        "property": property,
        "checklist": checklist,
        "photos": photos,
        "match": match,
        "look": look,
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
    property = outcome["property"]
    match = outcome["match"]
    look = outcome["look"]
    by_room = {}
    for item in outcome["checklist"]:
        by_room.setdefault(item.room_id, []).append(item)
    photos_by_room = {}
    for assignment in match.assignments.values():
        photos_by_room.setdefault(assignment.room_id, []).append(assignment.photo_id)
    rooms = sorted(property["rooms"], key=lambda room: room["floor"])
    for room in rooms:
        mine = photos_by_room.get(room["id"], [])
        print(f"Floor {room['floor']} - {room['name']}: photos {', '.join(mine) or 'none'}")
        for item in by_room.get(room["id"], []):
            if item.key == "coverage":
                print(f"  [{match.coverage[room['id']]}] {item.id}: {item.label}")
                continue
            claim = look.claims[item.id]
            print(f"  [{claim.status}|{claim.photo_id or '-'}] {item.id}: {claim.seen or '-'}")
    if photos_by_room.get("unknown"):
        print(f"Unknown room: photos {', '.join(photos_by_room['unknown'])}")
    house_items = by_room.get("house", [])
    if house_items:
        print("Whole house")
        for item in house_items:
            claim = look.claims[item.id]
            print(f"  [{claim.status}|{claim.photo_id or '-'}] {item.id}: {claim.seen or '-'}")
    missing = [room["name"] for room in rooms if match.coverage[room["id"]] != "covered"]
    if missing:
        print(f"Rooms not photographed: {', '.join(missing)}")
    else:
        print("Rooms not photographed: none")
    calls, cached, seconds, tokens = _trace_totals(client.trace)
    print(f"Meta calls: {calls} ({cached} from cache)")
    print(f"Time: {seconds:.1f} s")
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
