"""Every instruction text sent to Muse Spark, plus model-call constants.

All wording lives here so prompts can be tuned without touching logic.
matcher.py and inspector.py build the requests; this module builds the
words, the photo labels and the image parts.
"""

import base64

REASONING_EFFORT = "medium"
MATCH_PHOTO_SIZE = "overview"  # 768 px copies are enough to match rooms
FIRST_LOOK_PHOTO_SIZE = "check"  # 1600 px copies keep small details (stains)


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
