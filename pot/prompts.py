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
SETUP_PHOTO_SIZE = "overview"  # 768 px drafts match 1600 px (Step 26)
SETUP_EFFORT = "medium"  # low effort weakened sam_prompts (Step 26)


def pick_boxes_schema():
    return {
        "type": "object",
        "properties": {
            "boxes": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["boxes"],
        "additionalProperties": False,
    }


def pick_boxes_instructions(problem_object, count):
    return (
        f"The attached photo shows {count} numbered boxes (1 to {count}). "
        f"The problem to report is: {problem_object}. "
        f"Return the numbers of the boxes that show what is wrong. "
        f"Return an empty list when none of the boxes shows the problem."
    )


def pick_area_instructions(problem_area, count, requirement):
    return (
        f"The attached photo shows {count} numbered boxes (1 to {count}). "
        f"The numbered boxes are candidate outlines of \"{problem_area}\". "
        f"Return the number(s) of the box(es) outlining the place this check is about "
        f"- \"{requirement}\" - usually one; an empty list if none."
    )


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
- problem_objects: up to 3 different simple singular nouns the photo search can find for the different wrong things ("shoe", "bag", "towel"); "holder" for an empty holder, "shelf" for an empty shelf; [] when nothing is wrong.
- problem_object: the first noun of problem_objects, or "" when it is empty."""


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
    "ru": "Russian",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
}

_REQUEST_EXAMPLES = {
    # (problem example, photo example) in the cleaner's language.
    "es": (
        "Limpia la mancha del fondo del microondas y envía una foto nueva.",
        "Envía una foto de la toalla de manos en el baño de visitas.",
    ),
    "en": (
        "Clean the stain at the back of the microwave and send a new photo.",
        "Send a photo of the hand towel in the guest bathroom.",
    ),
    "ru": (
        "Очистите пятно на задней стенке микроволновки и пришлите новое фото.",
        "Пришлите фото полотенца для рук в гостевом санузле.",
    ),
}


def _language_name(code):
    return _LANGUAGE_NAMES.get((code or "").lower(), code)


def requests_instructions(cleaner_language, entries):
    code = (cleaner_language or "en").lower()
    cleaner = _language_name(code)
    problem_ex, photo_ex = _REQUEST_EXAMPLES.get(code, _REQUEST_EXAMPLES["en"])
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
- Examples (in {cleaner}): "{problem_ex}" / "{photo_ex}"

Items:
{item_lines}"""


def note_instructions(text, language, room_name):
    cleaner = _language_name((language or "en").lower())
    return f"""A cleaner recorded a voice note in {cleaner} while cleaning the {room_name}. She reports damage, missing things, supplies that ran out, or other comments for the host.

Note:
{text}

Rules:
- translation: the note translated into English, faithful and short.
- title: a short English title for the note, at most 8 words.
- category: exactly one of "damage" (something broken, stained or damaged), "missing" (something absent that should be there), "supplies" (consumables that ran out and need restocking), "other" (anything else).
- Write the translation and the title in English."""


def setup_rule_instructions(text, language):
    cleaner = _language_name((language or "en").lower())
    return f"""Translate this host's spoken house rule from {cleaner} into plain English.

Rule:
{text}

Rules:
- Translation: plain English a cleaner can follow.
- Keep numbers and meaning exactly; never drop or soften anything.
- Write the translation in English."""


def setup_draft_instructions(room_name, room_type, rules, standard_checks):
    if rules.strip():
        rules_block = f"Host's rules (English):\n{rules.strip()}"
    else:
        rules_block = "Host's rules: none given."
    check_lines = "\n".join(
        f"- {check['key']}: {check['label']} - {check['requirement']}"
        for check in standard_checks
    )
    return f"""You write the cleaning checklist for one room of a vacation rental from the host's rules and the attached photos of the room, clean and ready for guests.

Room: {room_name} ({room_type})

{rules_block}

Standard checks for a {room_type} (already covered, do not repeat):
{check_lines if check_lines else "- none"}

Rules:
- description: at most 25 words of visible features that tell this room apart from the others (colors, furniture, fixtures). It is used to match the cleaner's photos to rooms. No people, no personal items.
- beds (bedrooms only): one entry per bed as a guest would say it ("king bed", "twin bunk bed"); [] for other rooms. towels (full bathrooms only): the number of bath towels set out for guests in the photos; 0 if none seen; 0 for other rooms.
- Every host rule becomes at least one item with source "rule", one item per separate thing to check. Keep the host's meaning and numbers exactly; never drop or soften a rule. A rule that cannot be checked from a photo goes to unclear_rules instead.
- At most 3 items with source "photo": only things clearly set out for guests that the cleaner must put back every time (pillows arranged on a bed, paper towel roll on its holder, soap at the sink). Not general cleanliness: the standard checks already cover that.
- Items with source "photo" are only guest amenities or decor clearly set out for guests (pillows, throws, towels, toilet paper, soap, coffee, paper towels, decor). Never cups, used toiletries, clothes or other personal things.
- Don't repeat a standard check. When a host rule changes one, reuse that standard key so the new item replaces it (example: "toilet lid open for the photo" -> key "toilet").
- requirement: one sentence, at most 35 words, saying what the photo must show when it's done. If the check is inside something normally closed (fridge, freezer, dishwasher, washer, dryer, oven, microwave, cabinet), start with what to open for the photo ("Fridge doors open for the photo: ...").
- sam_prompt: a 1-3 word noun for an object that must be visible when it's done ("paper towel roll", "pillow"); null for checks about something empty, clean or absent. min_count: the number the rule asks for (or the number set out in the photos); only with sam_prompt, else null. min_count counts every matching object the photo shows, including the ones already in place. Example: "at least one spare toilet paper roll besides the one on the holder" -> sam_prompt "toilet paper roll", min_count 2.
- problem_area: for "must be empty" rules, the whole place in 1-3 words ("refrigerator door", "washing machine"); else null.
- critical: true unless the host calls it optional or nice to have.
- Plain English. Never mention door codes, Wi-Fi passwords or people."""


def tour_instructions(property, checklist, has_plan_image):
    room_lines = "\n".join(_room_line(room) for room in property["rooms"])
    item_lines = []
    for room in property["rooms"]:
        room_items = [item for item in checklist if item.room_id == room["id"]]
        item_lines.append(f"{room['name']}:")
        for item in room_items:
            if item.key == "coverage":
                continue
            flag = "critical" if item.critical else "optional"
            item_lines.append(f"  - {item.id}: {item.label} - {item.requirement} [{flag}]")
    house_items = [item for item in checklist if item.room_id == "house"]
    if house_items:
        item_lines.append("Whole house:")
        for item in house_items:
            flag = "critical" if item.critical else "optional"
            item_lines.append(f"  - {item.id}: {item.label} - {item.requirement} [{flag}]")
    plan_note = (
        "Use the attached floor plan to order the rooms into a sensible walking route. "
        if has_plan_image
        else "There is no floor plan: go floor by floor, lowest first, "
        "keeping the order the rooms are listed. "
    )
    return f"""You plan a cleaner's photo tour through a vacation rental. {plan_note}Group the checklist items below into shots: one photo per shot, room by room.

Rooms:
{room_lines}

Checklist:
{chr(10).join(item_lines)}

Rules:
- Order the shots as a walking route: finish one room before starting the next, following the floor plan when one is attached.
- Each shot names its room and the item ids one photo of that spot can show; keep each shot to one spot (for example "the open fridge", "the beds").
- Do not list coverage items: a room counts as photographed as soon as any photo of it arrives.
- Whole-house items belong in a suitable room's shot, not in their own shot.
- Every critical item must appear in at least one shot.
- Each shot carries room_name_xx, short_xx and instruction_xx for xx = es, en, ru: short spoken sentences that name the room, so the cleaner can tell similar rooms apart.
- instruction_xx tells the cleaner exactly what to do, not just what to photograph: what to open, lift, take off or remove, and what must be in the frame. Take the action from the item requirements below (a door or lid that must be open, a lid taken off only for the photo, two things shown in one photo). At most about 20 words; name the room in it, since some rooms need the same photo. For example: "Open the left fridge door and show all its shelves." / "Lift the toilet lid and show the inside of the bowl." / "Take the lid off the bean hopper and show the beans." / "Show the roll on the holder and the spare roll in one photo."
- Spanish uses Latin American (US/Mexico) words in the tú form: "refrigerador" (not "frigorífico"), "piso" (not "suelo"), "lavandería", "medio baño". For example: "Abre la puerta izquierda del refrigerador y muestra todos los estantes." / "Levanta la tapa del inodoro y muestra el interior de la taza."
- English uses the plain imperative ("Take a photo of…", "Open…", "Show…").
- Russian uses the polite imperative ("Сфотографируйте…", "Откройте…", "Покажите…").
- short_xx is 2-4 words naming the spot the photo shows (for example "lavadora", "puerta izquierda"), so two shots in one room can be told apart.
- When an item's requirement says a door or lid must be open, that item gets its own shot.
- When a requirement says something is opened only for the photo, that shot comes before the room's overview shot.
- Each shot is ONE photo taken from ONE spot that clearly shows every item in it. Never combine in one shot things that need a different angle, distance, or something opened: for example the toilet with the lid open and the sink/vanity are two shots; the inside of the fridge and each fridge door are separate shots. Small things get their own close-up (coffee bean hopper, lint filter, toilet paper). Prefer more shots over crowded shots."""
