"""Score a first-look run against an answer key.

Usage: scripts/score_first_look.py <run folder> <answer key>

Compares run/first_look.json with the key, prints a short report and saves
it as score.md in the run folder.
"""

import json
import sys
from pathlib import Path


def _load(run_dir, key_path):
    run_dir = Path(run_dir)
    first_look_path = run_dir / "first_look.json"
    if not first_look_path.is_file():
        raise SystemExit(f"No first_look.json in {run_dir}")
    key_path = Path(key_path)
    if not key_path.is_file():
        raise SystemExit(f"Answer key not found: {key_path}")
    run = json.loads(first_look_path.read_text(encoding="utf-8"))
    key = json.loads(key_path.read_text(encoding="utf-8"))
    return run, key


def _claim_view(claim, files):
    """(status, file-or-"-", seen) for a claim dict."""
    photo_id = claim.get("photo_id") or ""
    return claim.get("status", "?"), files.get(photo_id, "-") if photo_id else "-", claim.get("seen") or ""


def _score_rooms(run, key):
    by_file = {photo["file"]: photo for photo in run.get("photos", [])}
    lines = []
    correct = total = 0
    for name in sorted(key.get("rooms", {})):
        expected = key["rooms"][name]
        if expected is None or name not in by_file:
            continue
        total += 1
        given = by_file[name]
        if given["room_id"] == expected:
            correct += 1
        else:
            lines.append(
                f"- MISMATCH `{name}`: expected {expected}, "
                f"got {given['room_id']} ({given['confidence']})"
            )
    return correct, total, lines


def _candidates(run, item):
    """(claim-id, status, file, seen) candidates for a key item id.

    Coverage entries are synthesized as pseudo-claims (covered -> done).
    """
    files = {photo["id"]: photo["file"] for photo in run.get("photos", [])}
    claims = run.get("claims", {})
    coverage = run.get("coverage", {})
    found = []
    if item.startswith("*."):
        want_key = item[2:]
        for claim_id in sorted(claims):
            key = claim_id.partition(".")[2]
            if key == want_key:
                status, cited, seen = _claim_view(claims[claim_id], files)
                found.append((claim_id, status, cited, seen))
        if want_key == "coverage":
            for room_id in sorted(coverage):
                status = "done" if coverage[room_id] == "covered" else "not_shown"
                found.append((f"{room_id}.coverage", status, "-", ""))
        return found
    room, _, key = item.partition(".")
    if key == "coverage":
        if room in coverage:
            status = "done" if coverage[room] == "covered" else "not_shown"
            return [(item, status, "-", "")]
        return []
    if item in claims:
        status, cited, seen = _claim_view(claims[item], files)
        return [(item, status, cited, seen)]
    return []


def _satisfies(candidate, result, photo):
    _, status, cited, _ = candidate
    if result == "done":
        return status == "done"
    if result == "problem":
        return status == "problem" and (photo is None or cited == photo)
    if result == "not_done":
        return status != "done"
    return False


def _score_items(run, key):
    lines = []
    passed = 0
    consumed = set()
    for entry in key.get("expected", []):
        item, result = entry["item"], entry["result"]
        photo = entry.get("photo")
        why = entry.get("why", "")
        candidates = _candidates(run, item)
        decisive = next((c for c in candidates if _satisfies(c, result, photo)), None)
        if decisive is not None:
            passed += 1
            consumed.add(decisive[0])
            mark = "PASS"
        else:
            mark = "FAIL"
            decisive = next((c for c in candidates if c[1] != "not_shown"), None) or (
                candidates[0] if candidates else (item, "missing", "-", "")
            )
            consumed.update(c[0] for c in candidates)
        _, status, cited, seen = decisive
        want = result if photo is None else f"{result} @ `{photo}`"
        extra = " in some room" if item.startswith("*.") else ""
        lines.append(
            f"- {mark} `{decisive[0]}`{extra} (expected {want}): "
            f"app {status} @ `{cited}` — \"{seen}\" (key: {why})"
        )
    return passed, len(key.get("expected", [])), lines, consumed


def _proof_candidates(proof, item):
    items = proof.get("items", {})
    if item.startswith("*."):
        want_key = item[2:]
        return [
            (claim_id, items[claim_id]["status"], items[claim_id].get("file") or "-")
            for claim_id in sorted(items)
            if claim_id.partition(".")[2] == want_key
        ]
    if item in items:
        entry = items[item]
        return [(item, entry["status"], entry.get("file") or "-")]
    return []


def _proof_satisfies(status, result):
    if result == "done":
        return status == "proven"
    if result == "problem":
        return status == "problem"
    if result == "not_done":
        return status != "proven"
    return False


def _first_look_view(run, item_id):
    claims = run.get("claims", {})
    files = {photo["id"]: photo["file"] for photo in run.get("photos", [])}
    if item_id in claims:
        claim = claims[item_id]
        cited = files.get(claim.get("photo_id")) or "-"
        return f"{claim.get('status')} @ `{cited}`"
    room = item_id.partition(".")[0]
    return run.get("coverage", {}).get(room, "?")


def _score_final(run, proof, key):
    lines = []
    passed = 0
    for entry in key.get("expected", []):
        item, result = entry["item"], entry["result"]
        candidates = _proof_candidates(proof, item)
        decisive = next((c for c in candidates if _proof_satisfies(c[1], result)), None)
        if decisive is not None:
            passed += 1
            mark = "PASS"
        else:
            mark = "FAIL"
            decisive = candidates[0] if candidates else (item, "missing", "-")
        claim_id, status, cited = decisive
        extra = " in some room" if item.startswith("*.") else ""
        lines.append(
            f"- {mark} `{claim_id}`{extra} (expected {result}): "
            f"final {status} @ `{cited}` (first look: {_first_look_view(run, claim_id)})"
        )
    return passed, len(key.get("expected", [])), lines


def _false_alarms(run, key, consumed):
    expected_ids = {e["item"] for e in key.get("expected", []) if not e["item"].startswith("*.")}
    files = {photo["id"]: photo["file"] for photo in run.get("photos", [])}
    lines = []
    for claim_id in sorted(run.get("claims", {})):
        claim = run["claims"][claim_id]
        if claim.get("status") == "problem" and claim_id not in expected_ids | consumed:
            status, cited, seen = _claim_view(claim, files)
            lines.append(f"- problem `{claim_id}` @ `{cited}` — \"{seen}\"")
    for issue in run.get("other_issues", []):
        cited = files.get(issue.get("photo_id"), "-")
        lines.append(
            f"- other issue @ `{cited}` (room {issue.get('room_id')}) — "
            f"\"{issue.get('description')}\""
        )
    return lines


def score_run(run_dir, key_path):
    """Score a run folder against an answer key; return the markdown report."""
    run, key = _load(run_dir, key_path)
    out = [f"# First-look score: {Path(run_dir).name} vs {Path(key_path).name}", ""]
    correct, total, mismatches = _score_rooms(run, key)
    out.append(f"## Room matching: {correct}/{total} correct")
    out.extend(mismatches or ["- none"])
    out.append("")
    passed, total_items, item_lines, consumed = _score_items(run, key)
    out.append(f"## Items: {passed}/{total_items} pass")
    out.extend(item_lines or ["- none"])
    out.append("")
    proof_path = Path(run_dir) / "proof.json"
    if proof_path.is_file():
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
        final_passed, final_total, final_lines = _score_final(run, proof, key)
        out.append(f"## Final statuses: {final_passed}/{final_total} pass")
        out.extend(final_lines or ["- none"])
        out.append("")
    out.append("## Other app findings (possible false alarms)")
    out.extend(_false_alarms(run, key, consumed) or ["- none"])
    out.append("")
    return "\n".join(out)


def main(argv):
    if len(argv) != 3:
        print("Usage: scripts/score_first_look.py <run folder> <answer key>", file=sys.stderr)
        return 1
    try:
        report = score_run(argv[1], argv[2])
    except SystemExit as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(report)
    (Path(argv[1]) / "score.md").write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
