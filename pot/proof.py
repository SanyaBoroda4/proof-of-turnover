"""The proof rule (pure, no API calls), the host verdict, and evidence images.

prove_all() turns first-look claims + second looks + SAM checks into a
final status per item: proven, needs proof, or problem. verdict() turns
those into the host's ready / not-ready answer. save_evidence() draws
one annotated photo per proven/problem item.
"""

import os
import tempfile
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

PROVEN = "proven"
NEEDS_PROOF = "needs proof"
PROBLEM = "problem"

COVERED_CONFIDENCES = ("high", "medium")
BOX_WIDTH = 4
CAPTION_FONT_PATHS = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@dataclass
class ProofItem:
    item_id: str
    status: str  # proven / needs proof / problem
    reason: str  # short, host-readable
    photo_id: str  # "" when no photo


@dataclass
class Verdict:
    ready: bool
    problems: list = field(default_factory=list)  # critical problem ids
    need_photo: list = field(default_factory=list)  # critical needs-proof ids
    worth_a_look: list = field(default_factory=list)  # non-critical, not proven


def _join_names(names):
    if len(names) < 2:
        return names[0] if names else "?"
    return ", ".join(names[:-1]) + " or " + names[-1]


def prove_item(item, claim, second, sam, photo_unknown, coverage, first_room_photo,
               decision="none", rooms=None, candidate_ids=()):
    """Apply the proof rule to one item; returns a ProofItem.

    candidate_ids are the rooms (sorted) whose claims cite this photo; a
    problem the second look confirms on a room-undecided photo is still
    reported once, under the first candidate room.
    """
    rooms = rooms or {}
    if item.key == "coverage":
        if coverage == "covered" and first_room_photo:
            return ProofItem(item.id, PROVEN, "Room photographed", first_room_photo)
        return ProofItem(item.id, NEEDS_PROOF, "No photo of this room", "")
    if claim is None or claim.status == "not_shown":
        return ProofItem(item.id, NEEDS_PROOF, "No photo shows it", "")
    if second is None:
        return ProofItem(item.id, NEEDS_PROOF, "Verification did not run", claim.photo_id)
    if photo_unknown and decision != item.room_id:
        if decision == "none":
            if (
                claim.status == "problem"
                and second.verdict == "not_done"
                and second.this_room != "no"
            ):
                first = candidate_ids[0] if candidate_ids else item.room_id
                if item.room_id == first:
                    names = _join_names(
                        [rooms.get(room_id, {}).get("name", room_id) for room_id in candidate_ids]
                        or [rooms.get(item.room_id, {}).get("name", item.room_id)]
                    )
                    return ProofItem(
                        item.id, PROBLEM, f"room not confirmed: {names}", claim.photo_id
                    )
                first_name = rooms.get(first, {}).get("name", first)
                return ProofItem(
                    item.id, NEEDS_PROOF,
                    f"Same problem reported for the {first_name}", claim.photo_id,
                )
            if claim.status == "done":
                return ProofItem(
                    item.id, NEEDS_PROOF, "Can't tell which room this photo shows", claim.photo_id
                )
            # A problem claim the second look did not confirm: fall through
            # to the normal disagreement reasons below.
        else:
            name = rooms.get(decision, {}).get("name", decision)
            return ProofItem(
                item.id, NEEDS_PROOF, f"This photo looks like the {name}", claim.photo_id
            )
    if claim.status == "done":
        if second.this_room == "no":
            return ProofItem(item.id, NEEDS_PROOF, "Second look: photo shows another room", claim.photo_id)
        if second.verdict == "not_done":
            return ProofItem(item.id, NEEDS_PROOF, "Second look says it is not done", claim.photo_id)
        if second.verdict != "done":
            return ProofItem(item.id, NEEDS_PROOF, "Second look could not tell", claim.photo_id)
        if item.sam_prompt is not None and (sam is None or sam.count == 0):
            return ProofItem(
                item.id, NEEDS_PROOF,
                f"No '{item.sam_prompt}' found in the photo", claim.photo_id,
            )
        if item.min_count is not None:
            best = max(
                [sam.count if sam is not None else 0]
                + [second.count_seen if second.count_seen is not None else 0]
            )
            if best < item.min_count:
                return ProofItem(
                    item.id, NEEDS_PROOF,
                    f"Only {best} of at least {item.min_count} visible in the photo",
                    claim.photo_id,
                )
            return ProofItem(
                item.id, PROVEN,
                f"Confirmed in the photo ({best} seen, {item.min_count} needed)",
                claim.photo_id,
            )
        return ProofItem(item.id, PROVEN, "Confirmed in the photo", claim.photo_id)
    if second.verdict == "not_done" and second.this_room != "no":
        reason = second.reason or claim.seen or "Photo shows it is not done"
        return ProofItem(item.id, PROBLEM, reason, claim.photo_id)
    if second.verdict == "not_done":
        return ProofItem(
            item.id, NEEDS_PROOF,
            f"Problem seen, but not sure this photo is the {item.room_name}",
            claim.photo_id,
        )
    return ProofItem(item.id, NEEDS_PROOF, "Checks disagree", claim.photo_id)


def prove_all(checklist, claims, seconds, sams, assignments, coverage, photo_order,
              decisions, rooms):
    """Apply the proof rule to every item; returns {item_id: ProofItem}."""
    first_photo = {}
    for photo_id in photo_order:
        assignment = assignments.get(photo_id)
        if assignment is None or assignment.confidence not in COVERED_CONFIDENCES:
            continue
        first_photo.setdefault(assignment.room_id, photo_id)
    citing = {}
    for item in checklist:
        claim = claims.get(item.id)
        if claim is not None and claim.status in ("done", "problem") and claim.photo_id:
            citing.setdefault(claim.photo_id, set()).add(item.room_id)
    proofs = {}
    for item in checklist:
        claim = claims.get(item.id)
        photo_id = claim.photo_id if claim is not None else ""
        assignment = assignments.get(photo_id)
        photo_unknown = assignment is None or assignment.room_id == "unknown"
        proofs[item.id] = prove_item(
            item,
            claim,
            seconds.get(item.id),
            sams.get(item.id),
            photo_unknown,
            coverage.get(item.room_id),
            first_photo.get(item.room_id),
            decisions.get(photo_id, "none"),
            rooms,
            sorted(citing.get(photo_id, ())),
        )
    return proofs


def prune_other_issues(other_issues, proofs, claims):
    """Drop issues on photos where a final problem item already reports."""
    problem_photos = set()
    for item_id, proof in proofs.items():
        claim = claims.get(item_id)
        if proof.status == PROBLEM and claim is not None and claim.photo_id:
            problem_photos.add(claim.photo_id)
    return [issue for issue in other_issues if issue.photo_id not in problem_photos]


def verdict(checklist, proofs):
    """Host verdict: ready only when every critical item is proven."""
    result = Verdict(ready=True)
    for item in checklist:
        status = proofs[item.id].status
        if status == PROVEN:
            continue
        if item.critical:
            result.ready = False
            if status == PROBLEM:
                result.problems.append(item.id)
            else:
                result.need_photo.append(item.id)
        else:
            result.worth_a_look.append(item.id)
    return result


def _caption_font(image_height):
    """A font about 3% of the image height, with a plain fallback."""
    size = max(10, round(image_height * 0.03))
    for path in CAPTION_FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text, font, max_width):
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text + "..." if text else ""


def save_evidence(run_dir, proofs, items_by_id, photos_by_id, sams, problem_sams=None,
                  item_ids=None):
    """Save one annotated photo per proven/problem item under evidence/.

    Proven items get their muted-blue SAM boxes; problem items get the red
    problem-object boxes instead. The caption shows label + status on
    line 1 and the reason on line 2. A second copy without the caption
    bar (<item_id>.card.jpg) is saved for the phone cards. With item_ids,
    only those are saved.
    """
    problem_sams = problem_sams or {}
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for item_id in sorted(proofs):
        if item_ids is not None and item_id not in item_ids:
            continue
        proof = proofs[item_id]
        if proof.status not in (PROVEN, PROBLEM):
            continue
        photo = photos_by_id.get(proof.photo_id)
        if photo is None:
            continue
        with Image.open(photo.check_path) as source:
            source.load()
            font = _caption_font(source.height)
            ascent, descent = font.getmetrics()
            line_height = ascent + descent
            pad = max(4, line_height // 3)
            bar_height = pad + line_height * 2 + pad
            canvas = Image.new("RGB", (source.width, source.height + bar_height), "white")
            canvas.paste(source, (0, bar_height))
            card = Image.new("RGB", (source.width, source.height), "white")
            card.paste(source, (0, 0))
        draw = ImageDraw.Draw(canvas)
        label = items_by_id[item_id].label if item_id in items_by_id else item_id
        draw.text(
            (8, pad),
            _fit_text(draw, f"{label} - {proof.status}", font, source.width - 16),
            fill="black",
            font=font,
        )
        draw.text(
            (8, pad + line_height + 2),
            _fit_text(draw, proof.reason, font, source.width - 16),
            fill="black",
            font=font,
        )
        color = (63, 111, 181) if proof.status == PROVEN else (220, 0, 0)
        if proof.status == PROBLEM:
            sam = problem_sams.get(item_id)
        else:
            sam = sams.get(item_id)
        boxes = list(sam.boxes) if sam is not None else []
        box_width = BOX_WIDTH
        if proof.status == PROBLEM and not boxes:
            # A problem photo always shows red: frame the whole photo.
            boxes = [{
                "left": 0, "top": 0,
                "right": source.width - 1, "bottom": source.height - 1,
            }]
            box_width = max(3 * BOX_WIDTH, min(source.width, source.height) // 60)
        card_draw = ImageDraw.Draw(card)
        for box in boxes:
            left, top = int(box["left"]), int(box["top"])
            right, bottom = int(box["right"]), int(box["bottom"])
            draw.rectangle(
                [left, top + bar_height, right, bottom + bar_height],
                outline=color,
                width=box_width,
            )
            card_draw.rectangle(
                [left, top, right, bottom], outline=color, width=box_width
            )
        path = evidence_dir / f"{item_id}.jpg"
        _save_jpeg_atomic(canvas, path)
        _save_jpeg_atomic(card, evidence_dir / f"{item_id}.card.jpg")
        paths[item_id] = path
    return paths


def _save_jpeg_atomic(canvas, path):
    """Write one JPEG atomically (temp file + replace)."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        os.close(fd)
        canvas.save(tmp_name, "JPEG", quality=90)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
