"""Every instruction text sent to Muse Spark, plus model-call constants.

All wording lives here so prompts can be tuned without touching logic.
matcher.py and inspector.py build the requests; this module builds the
words, the photo labels and the image parts.
"""

import base64
import os

REASONING_EFFORT = "medium"
VERIFY_EFFORT = "medium"
RECHECK_EFFORT = "low"


def verify_effort():
    """Reasoning effort for verify calls; POT_VERIFY_EFFORT overrides."""
    return os.environ.get("POT_VERIFY_EFFORT", VERIFY_EFFORT)
MATCH_PHOTO_SIZE = "overview"  # 768 px copies are enough to match rooms
FIRST_LOOK_PHOTO_SIZE = "check"  # 1600 px copies keep small details (stains)
VERIFY_PHOTO_SIZE = "check"  # second look also needs full detail


def model_photo_path(photo, size):
    """Which saved copy of a photo to send to the model."""
    if size == "check":
        return photo.check_path
    return photo.overview_path


def photo_label(photo_id):
    return f"Photo {photo_id}"


def photo_parts(photo, size):
    """The input_text label + input_image parts for one photo."""
    raw = base64.b64encode(model_photo_path(photo, size).read_bytes()).decode("ascii")
    return [
        {"type": "input_text", "text": photo_label(photo.id)},
        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{raw}"},
    ]


def _room_line(room):
    bits = [room["name"], room["type"], f"floor {room['floor']}"]
    beds = room.get("beds")
    if isinstance(beds, list):
        bits.append(f"{len(beds)} beds: {', '.join(beds)}")
    elif beds is not None:
        bits.append(f"{beds} bed" if beds == 1 else f"{beds} beds")
    if room.get("towels") is not None:
        bits.append(f"{room['towels']} towel" if room["towels"] == 1 else f"{room['towels']} towels")
    line = f"- {room['id']}: " + ", ".join(bits)
    if room.get("description"):
        line += f". {room['description']}"
    return line


def match_rooms_instructions(rooms):
    room_lines = "\n".join(_room_line(room) for room in rooms)
    return f"""You match cleaning photos to rooms. For each photo below, decide which room it shows.

Rooms:
{room_lines}

Rules:
- The photos are in the order they were taken, and cleaners usually finish one room before starting the next, so neighbouring photos are often the same room. Still, judge each photo by what it actually shows.
- Use beds and room descriptions to tell similar rooms apart (for example a king bed versus two twin beds).
- Say "unknown" rather than guess: if a photo could be several rooms, or is too dark, blurry or shows no recognisable room, use room_id "unknown".
- Confidence: "high" when the room is unmistakable, "medium" when likely but some doubt remains, "low" for a weak guess or "unknown".
- "seen": what the photo shows, in English, at most 15 words.
- Return one entry for every photo, in the same order."""


def _item_line(item):
    line = f"- {item.id}: {item.label} - {item.requirement}"
    if item.min_count is not None:
        line += f" (need at least {item.min_count})"
    return line


def first_look_instructions(room_name, room_description, items):
    item_lines = "\n".join(_item_line(item) for item in items)
    if room_name is None:
        subject = "These checklist items belong to the whole property, not to one room."
        room_guard = ""
    else:
        subject = f"Room: {room_name}"
        room_guard = (
            "- Some photos may show other rooms: use only photos that show this room "
            "(compare what each photo shows with the room name and description above); "
            "never cite a photo of another room as proof for this room.\n"
        )
        if room_description:
            subject += f" ({room_description})"
    return f"""You check whether a cleaned place was cleaned properly. Look only at the photos below.

{subject}

Checklist:
{item_lines}

Rules:
- For each item give status "done" only when one single photo clearly shows it done; "problem" only when one photo clearly shows it NOT done (dirty, missing, broken or left behind).
- Otherwise use "not_shown": the item is not in any photo, or the photo is too dark, blurry or far away to tell.
{room_guard}- Never guess. Judge only what is actually visible.
- photo_id is the one photo that shows the item best; leave it empty ("") when the status is "not_shown".
- seen: what is actually visible in that photo, in English, at most 25 words.
- count_seen: for items that need a minimum number, how many are visible in that one photo; count only what is visible in the cited photo; otherwise null.
- other_issues: damage or problems NOT on the checklist that a photo clearly shows (stain, broken lamp, missing mirror). Leave it empty when there are none.
- Return one claim for every checklist item above."""


def second_look_instructions(room_name, room_description, requirement, min_count):
    subject = f"Room: {room_name}"
    if room_description:
        subject += f" ({room_description})"
    need = f" Minimum number: {min_count}." if min_count is not None else ""
    return f"""You verify one cleaning checklist item from a single photo. Look only at the photo below.

{subject}

Requirement: {requirement}.{need}

Rules:
- this_room: "yes" when the photo clearly shows this room (compare what it shows with the room name and description); "no" when it clearly shows another room; "unclear" when you cannot tell.
- verdict: "done" only when the photo clearly shows the requirement is met; "not_done" only when it clearly shows it is NOT met; otherwise "unclear".
- Never guess. Judge only what is actually visible in this photo.
- count_seen: how many relevant objects are visible in this photo (an integer, or null when the requirement has no minimum number or you cannot count).
- reason: why, in English, at most 25 words.
- problem_object: when something is wrong in this photo, a 1-3 word noun phrase for what is wrong (for example "brown stain", "toothbrush", "blue cloth", "empty shelf"); "" when nothing is wrong."""


def room_decision_instructions(rooms):
    room_lines = "\n".join(_room_line(room) for room in rooms)
    return f"""You decide which room a single photo shows. Look only at the photo below.

Candidate rooms:
{room_lines}

Rules:
- Answer with the id of the room the photo clearly shows.
- Answer "none" when the photo clearly shows none of these rooms, or when you cannot tell.
- Never guess. Compare what the photo shows with the room names and descriptions."""


_LANGUAGE_NAMES = {
    "es": "Spanish",
    "en": "English",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
}


def _language_name(code):
    return _LANGUAGE_NAMES.get((code or "").lower(), code)


def requests_instructions(cleaner_language, entries):
    cleaner = _language_name(cleaner_language)
    lines = []
    for entry in entries:
        lines.append(
            f"- {entry['item_id']} ({entry['room_name']}): {entry['label']} - "
            f"{entry['requirement']} [status: {entry['status']} - {entry['reason']}]"
        )
    item_lines = "\n".join(lines)
    return f"""You write short re-cleaning requests for a cleaner. For each item below write one request.

Rules:
- Write each request in {cleaner} plus an English version for the host.
- For a problem item: say what to fix and ask for a new photo afterwards.
- For an item that needs proof: say exactly what to photograph.
- Keep each request to one sentence.
- Examples (in Spanish): "Limpia la mancha del fondo del microondas y envía una foto nueva." / "Envía una foto de la toalla de manos en el baño de visitas."

Items:
{item_lines}"""
