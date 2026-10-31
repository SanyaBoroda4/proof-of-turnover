"""Re-run only the proof stage on an existing run folder.

Usage: python -m pot.prove <run folder>

Reuses the run's photos and first_look.json, runs verify + proof rule +
evidence again, and rewrites proof.json.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from pot.checklist import PropertyError, build_checklist, load_property
from pot.inspector import Claim, FirstLook, OtherIssue
from pot.matcher import Assignment, MatchResult
from pot.meta_client import MetaClient, MetaClientError
from pot.photos import Photo
from pot.turnover import print_report, run_proof


def load_run(run_dir):
    """Rebuild turnover objects from a run folder's first_look.json."""
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "first_look.json").read_text(encoding="utf-8"))
    property = load_property(data["property"])
    checklist = build_checklist(property)
    photos = []
    for entry in data["photos"]:
        check_path = run_dir / "photos" / f"{entry['id']}_check.jpg"
        with Image.open(check_path) as image:
            width, height = image.size
        photos.append(
            Photo(
                id=entry["id"],
                original_name=entry["file"],
                taken_at=None,
                received_at=datetime.now(),
                check_path=check_path,
                overview_path=run_dir / "photos" / f"{entry['id']}_overview.jpg",
                width=width,
                height=height,
            )
        )
    match = MatchResult(
        assignments={
            entry["id"]: Assignment(
                photo_id=entry["id"],
                room_id=entry["room_id"],
                confidence=entry["confidence"],
                seen=entry["seen"],
            )
            for entry in data["photos"]
        },
        coverage=data["coverage"],
        notes=[],
    )
    look = FirstLook(
        claims={item_id: Claim(**claim) for item_id, claim in data["claims"].items()},
        other_issues=[OtherIssue(**issue) for issue in data.get("other_issues", [])],
        notes=[],
    )
    return {
        "run_dir": run_dir,
        "property": property,
        "checklist": checklist,
        "photos": photos,
        "match": match,
        "look": look,
    }


def main(argv):
    parser = argparse.ArgumentParser(prog="pot.prove")
    parser.add_argument("run")
    args = parser.parse_args(argv[1:])
    run_dir = Path(args.run)
    if not (run_dir / "first_look.json").is_file():
        print(f"No first_look.json in {run_dir}", file=sys.stderr)
        return 1
    try:
        started = time.monotonic()
        client = MetaClient()
        outcome = load_run(run_dir)
        outcome["proof"] = run_proof(
            client, run_dir, outcome["property"], outcome["checklist"],
            outcome["photos"], outcome["match"], outcome["look"],
        )
        outcome["elapsed_seconds"] = time.monotonic() - started
    except (PropertyError, MetaClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Run: {run_dir}")
    print_report(outcome, client)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
