"""Timing report for a live run: per-photo waits, slowest calls, 429s.

Usage: scripts/timing.py <run_id>

Reads data/runs/<run_id>/{timeline.jsonl, calls.jsonl, proof.json,
requests.json} and prints per photo received -> finished, the slowest
calls, and the wait after the last upload (-> all checked, -> first
card ready), plus the open critical items with their request texts.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pot import config  # noqa: E402


def _read_jsonl(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _at(row):
    try:
        return datetime.fromisoformat(row.get("time", ""))
    except ValueError:
        return None


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Usage: scripts/timing.py <run_id>")
    run_dir = config.RUNS_DIR / argv[1]
    if not run_dir.is_dir():
        raise SystemExit(f"No such run: {run_dir}")
    timeline = [row for row in _read_jsonl(run_dir / "timeline.jsonl") if _at(row)]
    calls = _read_jsonl(run_dir / "calls.jsonl")

    received, finished, failed = {}, {}, set()
    for row in timeline:
        event = row.get("event")
        if event == "photo_received" and row.get("photo_id"):
            received.setdefault(row["photo_id"], _at(row))
        elif event == "photo_finished" and row.get("photo_id"):
            finished.setdefault(row["photo_id"], _at(row))
            if row.get("outcome") == "failed":
                failed.add(row["photo_id"])

    print(f"Run {argv[1]}: {len(received)} photos, {len(calls)} calls")
    print("Per photo (received -> finished):")
    for photo_id in sorted(received):
        start = received[photo_id]
        end = finished.get(photo_id)
        if end is None:
            print(f"  {photo_id}: still checking")
        elif photo_id in failed:
            print(f"  {photo_id}: FAILED after {(end - start).total_seconds():.1f} s")
        else:
            print(f"  {photo_id}: {(end - start).total_seconds():.1f} s")

    slowest = sorted(
        [call for call in calls if isinstance(call.get("seconds"), (int, float))],
        key=lambda call: call["seconds"],
        reverse=True,
    )[:8]
    print("Slowest calls:")
    if not slowest:
        print("  (none)")
    for call in slowest:
        print(
            f"  {call['seconds']:.2f} s {call.get('model', '?')} "
            f"{call.get('purpose', '?')} photo={call.get('photo_id') or '-'} "
            f"attempts={call.get('attempts', '?')} {call.get('source', '?')}"
        )
    throttled = sum(1 for call in calls if call.get("status") == 429)
    print(f"429s: {throttled}")

    received_at = [(_at(row), row) for row in timeline if row.get("event") == "photo_received"]
    if received_at:
        last_upload = max(when for when, _row in received_at)
        print(f"Last upload at {last_upload.strftime('%H:%M:%S.%f')[:-3]}")
        after = [(_at(row), row) for row in timeline if _at(row) and _at(row) >= last_upload]
        checked = [when for when, row in after if row.get("event") == "all_checked"]
        if checked:
            print(f"  -> all checked: {(min(checked) - last_upload).total_seconds():.1f} s")
        else:
            print("  -> all checked: not yet")
        cards = [(when, row) for when, row in after if row.get("event") == "card_ready"]
        if cards:
            when, row = min(cards)
            print(f"  -> first card ready: {(when - last_upload).total_seconds():.1f} s "
                  f"({row.get('item_id', '?')})")
        else:
            print("  -> first card ready: none yet")
        first_cards = [(when, row) for when, row in
                       [(_at(row), row) for row in timeline]
                       if row.get("event") == "card_ready"]
        if first_cards:
            when, row = min(first_cards)
            print(f"First card overall: {row.get('item_id', '?')} "
                  f"at {when.strftime('%H:%M:%S.%f')[:-3]}")

    proof_path = run_dir / "proof.json"
    requests_path = run_dir / "requests.json"
    if proof_path.is_file():
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        texts = {}
        if requests_path.is_file():
            stored = json.loads(requests_path.read_text(encoding="utf-8"))
            texts = {request["item_id"]: request.get("cleaner", "")
                     for request in stored.get("requests", [])}
        items = proof.get("items", {})
        open_items = [
            item_id for item_id in
            proof.get("verdict", {}).get("problems", [])
            + proof.get("verdict", {}).get("need_photo", [])
        ]
        print(f"Open critical items: {len(open_items)}")
        for item_id in open_items:
            status = items.get(item_id, {}).get("status", "?")
            print(f"  {item_id} [{status}]: "
                  f"{texts.get(item_id) or '(no request yet)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
