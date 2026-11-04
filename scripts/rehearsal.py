"""Full live dress rehearsal: a guided round against a real server.

Uses the same HTTP API as the phone: starts a run on rental-1, uploads
each shot's photo from data/inputs/rehearsal_map.json in tour order every
--pace seconds like a walking cleaner, waits until every photo is checked,
prints every card, does the left-fridge-door retake, resolves the rest and
finishes. Photos are copied to a temp folder with their metadata removed
first (their camera dates are older than the run, so the server would
refuse them as old photos); the originals are never changed.

Usage:
    .venv\\Scripts\\python scripts\\rehearsal.py --base http://127.0.0.1:8000 \\
        --code <access-code> --language en --pace 8

The access code is never printed. The model key stays in the server's
environment and is never read here.
"""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageOps  # noqa: E402

from pot.photos import read_taken_at  # noqa: E402

FRIDGE_ITEM = "kitchen.fridge_left_door"
FRIDGE_RETAKE = "data/photos/Clean House/Opened Fridge.jpeg"
POLL_SECONDS = 5.0


class RehearsalError(Exception):
    """A rehearsal step failed; the message is safe to print (no secrets)."""


def _request(base, code, method, path, body=None, content_type=None, filename=None,
             fields=None):
    """One API call with ?code= auth; returns the decoded JSON body."""
    url = f"{base}{path}?code={urllib.parse.quote(code)}"
    if fields is not None:
        boundary = "----rehearsal-form-boundary"
        buffer = io.BytesIO()
        for name, value in fields:
            buffer.write(f"--{boundary}\r\n".encode("ascii"))
            if isinstance(value, tuple):
                part_name, part_bytes, part_type = value
                buffer.write(
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{part_name}"\r\n'.encode("ascii")
                )
                buffer.write(f"Content-Type: {part_type}\r\n\r\n".encode("ascii"))
                buffer.write(part_bytes)
                buffer.write(b"\r\n")
            else:
                buffer.write(
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
                )
                buffer.write(value.encode("utf-8"))
                buffer.write(b"\r\n")
        buffer.write(f"--{boundary}--\r\n".encode("ascii"))
        data = buffer.getvalue()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        data = body
        headers = {"Content-Type": content_type or "application/json"}
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            detail = exc.reason
        raise RehearsalError(f"{method} {path} -> HTTP {exc.code}: {detail[:300]}")
    except urllib.error.URLError as exc:
        raise RehearsalError(f"{method} {path} -> connection failed: {exc.reason}")


def _get(base, code, path):
    return _request(base, code, "GET", path)


def _post_json(base, code, path, payload):
    return _request(
        base, code, "POST", path,
        body=json.dumps(payload).encode("utf-8"),
        content_type="application/json",
    )


def copy_verbatim(source, dest):
    """Copy a photo byte for byte (demo runs: recorded calls match)."""
    Path(dest).write_bytes(Path(source).read_bytes())
    return dest


def strip_metadata(source, dest):
    """Copy an image without metadata (no EXIF camera date); returns dest."""
    with Image.open(source) as handle:
        handle.load()
        image = ImageOps.exif_transpose(handle).convert("RGB")
        image.save(dest, "JPEG", quality=92)
    if read_taken_at(dest) is not None:
        raise RehearsalError(f"metadata strip failed for {source}")
    return dest


def wait_until_checked(base, code, run_id, timeout, label):
    """Poll /status until no photo is checking; returns the final status."""
    deadline = time.time() + timeout
    last_count = None
    while True:
        status = _get(base, code, f"/api/runs/{run_id}/status")
        checking = status.get("checking", [])
        if not checking:
            return status
        if len(checking) != last_count:
            last_count = len(checking)
            print(f"[{label}] {datetime.now():%H:%M:%S} "
                  f"still checking: {len(checking)} ({', '.join(checking[:6])}"
                  f"{'...' if len(checking) > 6 else ''})", flush=True)
        if time.time() > deadline:
            raise RehearsalError(
                f"timed out waiting for checks ({label}); "
                f"still checking: {', '.join(checking)}"
            )
        time.sleep(POLL_SECONDS)


def items_by_id(status):
    found = {}
    for room in status.get("rooms", []):
        for item in room.get("items", []):
            found[item["id"]] = item
    return found


def plan_from_map(tour, photo_map):
    """Decide mapped shots and rooms to skip from a rehearsal map.

    Returns (mapped shot ids in tour order, room ids to skip in tour
    order). A room must be mapped completely or not at all.
    """
    tour_ids = [shot["shot_id"] for shot in tour]
    unknown = [sid for sid in photo_map if sid not in set(tour_ids)]
    if unknown:
        raise SystemExit(
            f"rehearsal map lists unknown shots: {', '.join(unknown)}")
    rooms = []
    for shot in tour:
        if shot["room_id"] not in rooms:
            rooms.append(shot["room_id"])
    mapped, skip_rooms, partial = [], [], []
    for room_id in rooms:
        room_shots = [s["shot_id"] for s in tour if s["room_id"] == room_id]
        have = [sid for sid in room_shots if sid in photo_map]
        if len(have) == len(room_shots):
            mapped.extend(have)
        elif have:
            partial.append(
                f"{room_id} ({len(have)}/{len(room_shots)} shots mapped)")
        else:
            skip_rooms.append(room_id)
    if partial:
        raise SystemExit(
            "rehearsal map must cover a room completely or not at all; "
            f"partially mapped rooms: {', '.join(partial)}")
    return mapped, skip_rooms


def print_card_groups(status):
    groups = status.get("card_groups") or []
    if not groups:
        groups = [
            {"photo_id": "", "shot_id": "", "item_ids": [item_id]}
            for item_id in status.get("cards", [])
        ]
    by_id = items_by_id(status)
    print(f"--- {len(groups)} card groups ---")
    for group in groups:
        item_ids = group.get("item_ids", [])
        print(f"  photo={group.get('photo_id') or '-'} "
              f"shot={group.get('shot_id') or '-'} "
              f"items={', '.join(item_ids)}")
        line_ids = group.get("line_item_ids") or item_ids
        for item_id in line_ids:
            item = by_id.get(item_id, {})
            request = (item.get("request") or {}).get("cleaner",
                                                      "(no request yet)")
            print(f"    {item_id} [{item.get('status', '?')}] "
                  f"reason: {item.get('reason') or '-'}")
            print(f"      request: {request}")
    return groups


def find_group(status, selector):
    """The card group for an action selector (item id or photo id)."""
    for group in status.get("card_groups") or []:
        if selector in (group.get("item_ids") or []):
            return group
        if selector and selector == group.get("photo_id"):
            return group
    return None


def apply_actions(base, code, run_id, actions, wait_timeout):
    """Apply card actions in order, exactly like the phone does."""
    for action in actions:
        kind = action.get("action")
        selector = action.get("item") or action.get("photo_id")
        status = _get(base, code, f"/api/runs/{run_id}/status")
        group = find_group(status, selector)
        if group is None:
            raise SystemExit(
                f"action {kind} for {selector}: no such card group")
        item_ids = group.get("item_ids") or []
        if kind == "new_photo":
            source = Path(action["file"])
            if not source.is_absolute():
                source = ROOT / source
            if not source.is_file():
                raise SystemExit(f"action file not found: {source}")
            result = upload_photo(base, code, run_id, group["shot_id"],
                                  source, source.name,
                                  retake_items=item_ids)
            print(f"Action new_photo for {', '.join(item_ids)} "
                  f"(shot {group['shot_id']}, items "
                  f"{','.join(item_ids)}) -> {result.get('photo_id')}",
                  flush=True)
            status = wait_until_checked(base, code, run_id,
                                         wait_timeout, "retake")
            for attempt in range(1, 4):
                if not status.get("unchecked"):
                    break
                _post_json(base, code,
                           f"/api/runs/{run_id}/recheck-unchecked", {})
                status = wait_until_checked(base, code, run_id,
                                            wait_timeout, "retake")
        elif kind in ("its_fine", "cant_fix"):
            body = {"action": kind,
                    "reason": action.get("reason") if kind == "cant_fix" else None,
                    "note_id": None}
            for item_id in item_ids:
                _post_json(base, code,
                           f"/api/runs/{run_id}/items/{item_id}/resolve", body)
                print(f"Action {kind} for {item_id}", flush=True)
            status = _get(base, code, f"/api/runs/{run_id}/status")
        else:
            raise SystemExit(f"unknown action: {kind}")
        by_id = items_by_id(status)
        for item_id in item_ids:
            item = by_id.get(item_id, {})
            print(f"  result {item_id} [{item.get('status', '?')}] "
                  f"photo={item.get('photo_id') or '-'}", flush=True)
    return status


def print_cards(status):
    cards = status.get("cards", [])
    by_id = items_by_id(status)
    print(f"--- {len(cards)} cards ---")
    for item_id in cards:
        item = by_id.get(item_id, {})
        request = (item.get("request") or {}).get("cleaner", "(no request yet)")
        print(f"  {item_id} [{item.get('status', '?')}] "
              f"photo={item.get('photo_id') or '-'}: {request}")
    return cards


def upload_photo(base, code, run_id, shot_id, path, original_name, tries=3,
                 retake_items=None):
    payload = Path(path).read_bytes()
    fields = [("shot_id", shot_id),
              ("image", (original_name, payload, "image/jpeg"))]
    if retake_items:
        fields.insert(1, ("retake_items", ",".join(retake_items)))
    last_error = None
    for attempt in range(tries):
        try:
            return _request(
                base, code, "POST", f"/api/runs/{run_id}/photos",
                fields=fields,
            )
        except RehearsalError as exc:
            last_error = exc
            if "HTTP 400" in str(exc):
                raise
            print(f"  upload {shot_id} attempt {attempt + 1} failed; retrying...",
                  flush=True)
            time.sleep(5)
    raise last_error


def main(argv):
    parser = argparse.ArgumentParser(description="Live dress rehearsal.")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--code", required=True, help="server access code")
    parser.add_argument("--language", default="en")
    parser.add_argument("--pace", type=float, default=8.0,
                        help="seconds between upload starts")
    parser.add_argument("--wait-timeout", type=float, default=3600.0,
                        help="max seconds to wait for all photos checked")
    parser.add_argument("--map", default="data/inputs/rehearsal_map.json",
                        help="photo map JSON (may cover only whole rooms)")
    parser.add_argument("--leave-cards", action="store_true",
                        help="stop after printing the card groups; no "
                             "retake, resolve or finish")
    parser.add_argument("--demo-run", action="store_true",
                        help="start the run with demo=true and upload "
                             "files byte for byte (replay recording)")
    parser.add_argument("--actions",
                        help="JSON list of card actions to apply after "
                             "the round; then stop (no finish)")
    args = parser.parse_args(argv)

    tour = json.loads((ROOT / "properties" / "rental-1" / "tour.json")
                       .read_text(encoding="utf-8"))["shots"]
    map_path = Path(args.map)
    if not map_path.is_absolute():
        map_path = ROOT / map_path
    photo_map = json.loads(map_path.read_text(encoding="utf-8"))["shots"]
    mapped, skip_rooms = plan_from_map(tour, photo_map)

    with tempfile.TemporaryDirectory(prefix="rehearsal-") as tmp:
        staged = {}
        for shot in tour:
            shot_id = shot["shot_id"]
            if shot_id not in mapped:
                continue
            source = ROOT / photo_map[shot_id]["file"]
            if not source.is_file():
                raise SystemExit(f"photo not found: {source}")
            dest = Path(tmp) / f"{shot_id}{source.suffix.lower()}"
            if args.demo_run:
                copy_verbatim(source, dest)
            else:
                strip_metadata(source, dest)
            staged[shot_id] = (dest, source.name)
        retake_src = ROOT / FRIDGE_RETAKE
        retake_path = Path(tmp) / f"retake{retake_src.suffix.lower()}"
        if not args.leave_cards and not args.actions:
            strip_metadata(retake_src, retake_path)
        print(f"Staged {len(staged)} photos"
              f"{'' if args.leave_cards or args.actions else ' + retake'} "
              f"in {tmp} (metadata removed)")

        created = _post_json(args.base, args.code, "/api/runs",
                             {"property": "rental-1",
                              "demo": bool(args.demo_run),
                              "language": args.language})
        run_id = created["run_id"]
        print(f"Run {run_id} started ({created.get('shots')} shots, "
              f"language={created.get('language')})", flush=True)

        server_tour = _get(args.base, args.code, f"/api/runs/{run_id}/tour")["shots"]
        if [s["shot_id"] for s in server_tour] != [s["shot_id"] for s in tour]:
            raise SystemExit("server tour order differs from tour.json; aborting")
        for room_id in skip_rooms:
            _post_json(args.base, args.code,
                       f"/api/runs/{run_id}/skip-room", {"room_id": room_id})
            print(f"Skipped room {room_id} (no mapped photos)", flush=True)
        upload_tour = [s for s in server_tour if s["shot_id"] in mapped]

        try:
            start = time.time()
            for index, shot in enumerate(upload_tour):
                shot_id = shot["shot_id"]
                wait = start + index * args.pace - time.time()
                if wait > 0:
                    time.sleep(wait)
                path, original = staged[shot_id]
                try:
                    result = upload_photo(args.base, args.code, run_id,
                                          shot_id, path, original)
                except RehearsalError as exc:
                    raise SystemExit(f"upload {shot_id} ({index + 1}/"
                                     f"{len(upload_tour)}) failed: {exc}")
                print(f"[{index + 1}/{len(upload_tour)}] {datetime.now():%H:%M:%S} "
                      f"{shot_id} -> {result.get('photo_id')}", flush=True)
            print(f"Last upload at {datetime.now():%H:%M:%S.%f}"[:-3])

            status = wait_until_checked(args.base, args.code, run_id,
                                        args.wait_timeout, "round")
            print(f"All checked at {datetime.now():%H:%M:%S.%f}"[:-3])
            for attempt in range(1, 4):
                if not status.get("unchecked"):
                    break
                print(f"Rechecking {status['unchecked']} (try {attempt}/3)...")
                _post_json(args.base, args.code,
                           f"/api/runs/{run_id}/recheck-unchecked", {})
                status = wait_until_checked(args.base, args.code, run_id,
                                            args.wait_timeout, "recheck")
            if status.get("unchecked"):
                raise SystemExit(
                    f"photos still unchecked after 3 retries: "
                    f"{status['unchecked']}")
            print(f"agent_state={status.get('agent_state')}")
            if args.actions:
                actions_path = Path(args.actions)
                if not actions_path.is_absolute():
                    actions_path = ROOT / actions_path
                actions = json.loads(
                    actions_path.read_text(encoding="utf-8")).get("actions", [])
                status = apply_actions(args.base, args.code, run_id,
                                       actions, args.wait_timeout)
                print(f"Actions done; run left open: {run_id}")
                print(f"Pages: {args.base}/clean and "
                      f"{args.base}/host?run={run_id}")
                return 0
            if args.leave_cards:
                print_card_groups(status)
                print(f"Run left open: {run_id}")
                print(f"Pages: {args.base}/clean and "
                      f"{args.base}/host?run={run_id}")
                return 0
            cards = print_cards(status)

            fridge_shot = next(
                s["shot_id"] for s in server_tour
                if FRIDGE_ITEM in s.get("item_ids", []))
            by_id = items_by_id(status)
            if FRIDGE_ITEM in cards:
                print(f"Retake for {FRIDGE_ITEM} (shot {fridge_shot})...")
                result = upload_photo(args.base, args.code, run_id,
                                      fridge_shot, retake_path, retake_src.name)
                print(f"  retake -> {result.get('photo_id')}", flush=True)
                status = wait_until_checked(args.base, args.code, run_id,
                                            args.wait_timeout, "retake")
                if status.get("unchecked"):
                    print(f"Rechecking {status['unchecked']} after retake...")
                    _post_json(args.base, args.code,
                               f"/api/runs/{run_id}/recheck-unchecked", {})
                    status = wait_until_checked(
                        args.base, args.code, run_id, args.wait_timeout,
                        "retake-recheck")
                by_id = items_by_id(status)
                fridge = by_id.get(FRIDGE_ITEM, {})
                if fridge.get("status") == "proven":
                    print(f"Fridge retake PROVEN (photo {fridge.get('photo_id')})")
                else:
                    print(f"Fridge retake not proven: status="
                          f"{fridge.get('status', '?')}; card stays.")
                cards = print_cards(status)
            else:
                fridge = by_id.get(FRIDGE_ITEM, {})
                print(f"No {FRIDGE_ITEM} card (status "
                      f"{fridge.get('status', '?')}); skipping retake.")

            cards = [c for c in status.get("cards", [])]
            if cards:
                first = cards.pop(0)
                _post_json(args.base, args.code,
                           f"/api/runs/{run_id}/items/{first}/resolve",
                           {"action": "cant_fix", "reason": "supplies",
                            "note_id": None})
                print(f"Resolved {first} as cant_fix/supplies")
            if cards:
                second = cards.pop(0)
                _post_json(args.base, args.code,
                           f"/api/runs/{run_id}/items/{second}/resolve",
                           {"action": "its_fine", "reason": None,
                            "note_id": None})
                print(f"Resolved {second} as its_fine")
            for item_id in cards:
                _post_json(args.base, args.code,
                           f"/api/runs/{run_id}/items/{item_id}/resolve",
                           {"action": "its_fine", "reason": None,
                            "note_id": None})
                print(f"Resolved {item_id} as its_fine")

            status = _get(base=args.base, code=args.code,
                          path=f"/api/runs/{run_id}/status")
            if status.get("checking"):
                raise SystemExit(f"cannot finish, still checking: "
                                 f"{status['checking']}")
            if status.get("unchecked"):
                raise SystemExit(f"cannot finish, unchecked: "
                                 f"{status['unchecked']}")
            # NB: status["open"] is the host verdict and ignores resolutions;
            # cards (like the phone) lists exactly what is still actionable.
            if status.get("cards"):
                raise SystemExit(f"cannot finish, cards left: "
                                 f"{status['cards']}")
            print(f"Pre-finish agent_state={status.get('agent_state')}")
            done = _post_json(args.base, args.code,
                              f"/api/runs/{run_id}/finish", {})
            print(f"Finished at {done.get('finished_at')}; "
                  f"agent_state={done.get('agent_state')}")
            if done.get("agent_state") != "awaiting_signoff":
                raise SystemExit(f"expected awaiting_signoff, got "
                                 f"{done.get('agent_state')}")
        except BaseException:
            print(f"Rehearsal run id (for inspection): {run_id}")
            raise

    timing = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "timing.py"), run_id],
        capture_output=True, text=True,
    )
    print("--- timing.py ---")
    print(timing.stdout, end="")
    if timing.returncode != 0:
        print(timing.stderr, end="")
        raise SystemExit("timing.py failed")
    print(f"DONE run={run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
