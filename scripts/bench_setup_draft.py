"""Benchmark setup-draft model/effort combinations on real rooms.

Live calls only, with a throwaway model cache so nothing is replayed:

    .venv\\Scripts\\python scripts/bench_setup_draft.py
    .venv\\Scripts\\python scripts/bench_setup_draft.py --preset bedroom
    .venv\\Scripts\\python scripts/bench_setup_draft.py --preset bathroom
    .venv\\Scripts\\python scripts/bench_setup_draft.py --room-type kitchen \
        --room-name Kitchen --rules "..." --photo path/to/photo.jpg

With no arguments it drafts the front-porch room of
setup-20261101-222836 as in Step 43. A preset (or explicit room) is
drafted in a throwaway setup under data/setups/ that is deleted
afterwards and never saved as a property. Prints seconds, rule-item
count, missing host rules, and sam_prompt/min_count checks. A
combination the API refuses is skipped.
"""

import argparse
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pot import config, setup as setup_mod  # noqa: E402
from pot.meta_client import MetaClient  # noqa: E402

COMBOS = [
    (config.REASONING_MODEL, "medium"),
    (config.REASONING_MODEL, "low"),
    (config.REASONING_MODEL, "minimal"),
    (config.WONDER_MODEL, "low"),
    (config.WONDER_MODEL, "minimal"),
]


def _rule(name, *words):
    return (name, lambda text: all(word in text for word in words))


PORCH = {
    "setup_id": "setup-20261101-222836",
    "room_id": "front-porch",
    "checks": [
        _rule("porch clean", "porch", "clean"),
        ("door clean, no stains",
         lambda text: "door" in text and ("clean" in text or "stain" in text)),
        _rule("no shoes on the floor", "shoe", "floor"),
        ("seats hold only pillows",
         lambda text: any(w in text for w in ("couch", "seat", "loveseat", "chair"))
         and "pillow" in text),
        ("no dirt on the concrete",
         lambda text: "concrete" in text
         and any(w in text for w in ("dirt", "clean", "stain"))),
    ],
    "counts": [],
}

PRESETS = {
    "bedroom": {
        "room_type": "bedroom",
        "room_name": "Bedroom",
        "photos": ["data/photos/Airbnb/Bedroom_3_1.jpeg"],
        "rules": (
            "Bed made with 4 pillows. Two folded towels at the foot of the bed. "
            "No guest belongings left, closet empty. Desk and nightstand wiped. "
            "Floor vacuumed, nothing under the bed."
        ),
        "checks": [
            _rule("bed made with pillows", "pillow"),
            _rule("folded towels at foot of bed", "towel"),
            ("no guest belongings, closet empty",
             lambda text: "belonging" in text or "closet" in text),
            ("desk and nightstand wiped",
             lambda text: ("desk" in text or "nightstand" in text)
             and any(w in text for w in ("wip", "clean"))),
            ("floor vacuumed, nothing under bed",
             lambda text: "floor" in text or "under the bed" in text),
        ],
        "counts": [
            ("pillows", "pillow", "pillow", 4),
            ("folded towels", "towel", "towel", 2),
        ],
    },
    "bathroom": {
        "room_type": "bathroom",
        "room_name": "Bathroom",
        "photos": [
            "data/photos/Clean House/Half Bath Vanity.jpeg",
            "data/photos/Clean House/Half Bath Toilet.jpeg",
        ],
        "rules": (
            "Toilet lid open for the photo, bowl clean. At least 2 spare toilet "
            "paper rolls besides the one on the holder. 4 bath towels folded "
            "on the shelf. Hand towel on the ring. No guest toiletries left."
        ),
        "checks": [
            ("toilet lid open, bowl clean",
             lambda text: "toilet" in text
             and any(w in text for w in ("lid", "bowl"))),
            _rule("spare toilet paper rolls", "toilet paper"),
            _rule("bath towels on shelf", "towel"),
            _rule("hand towel on ring", "hand towel"),
            _rule("no guest toiletries", "toiletries"),
        ],
        "counts": [
            ("toilet paper rolls", "toilet paper", "toilet paper", 3),
            ("bath towels", "bath towel", "towel", 4),
            ("hand towel", "hand towel", "towel", 1),
        ],
    },
}


def _count_report(rules, expected):
    """Check sam_prompt/min_count of rule items against the host rules."""
    problems = []
    for label, keyword, sam, want in expected:
        candidates = [
            item for item in rules
            if keyword in f"{item['label']} {item['requirement']}".lower()
        ]
        ok = any(
            sam in (item["sam_prompt"] or "").lower()
            and item["min_count"] == want
            for item in candidates
        )
        if not ok:
            found = ", ".join(
                f"{item['key']}={item['sam_prompt'] or '-'}/{item['min_count']}"
                for item in candidates
            ) or "no item"
            problems.append(f"{label}: want {sam}/{want}, got {found}")
    return problems


def _make_throwaway(room_type, room_name, rules, photos):
    """Create a one-room setup under data/setups/; returns its id."""
    setup_id = setup_mod.new_setup_id()
    while setup_mod._setup_dir(setup_id).exists():
        time.sleep(1.1)
        setup_id = setup_mod.new_setup_id()
    room = setup_mod._room_entry("bench-room", room_type, room_name, 1)
    room["rules"] = rules
    setup = {
        "id": setup_id,
        "name": "Benchmark (throwaway)",
        "floors": 1,
        "language": "en",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rooms": [room],
        "property_id": None,
    }
    with setup_mod._lock_for(setup_id):
        setup_mod._save_locked(setup)
    for photo in photos:
        path = Path(photo)
        setup_mod.add_room_photo(
            setup_id, "bench-room", path.name, path.read_bytes())
    return setup_id


def _run(client, setup_id, room_id, checks, counts):
    print("| Model | Effort | Seconds | Rule items | Missing host rules | sam/min checks |")
    print("|---|---|---:|---:|---|---|")
    for model, effort in COMBOS:
        started = time.perf_counter()
        try:
            draft = setup_mod.draft_room(
                client, setup_id, room_id,
                effort=effort, model=model, store=False,
            )
        except Exception as exc:  # noqa: BLE001 - refused combos are skipped
            print(f"| {model} | {effort} | - | - | SKIPPED: {exc} | - |")
            continue
        seconds = time.perf_counter() - started
        rules = [item for item in draft["items"] if item["source"] == "rule"]
        texts = [f"{item['label']} {item['requirement']}".lower() for item in rules]
        missing = [name for name, check in checks
                   if not any(check(text) for text in texts)]
        problems = _count_report(rules, counts)
        prompts = ", ".join(
            f"{item['key']}={item['sam_prompt'] or '-'}/{item['min_count']}"
            for item in rules
        )
        print(
            f"| {model} | {effort} | {seconds:.1f} | {len(rules)} | "
            f"{', '.join(missing) or 'none'} | "
            f"{'; '.join(problems) if problems else ('ok' if counts else prompts or '-')} |"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark setup drafts.")
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument("--room-type")
    parser.add_argument("--room-name", default="Room")
    parser.add_argument("--rules")
    parser.add_argument("--photo", action="append", default=[])
    parser.add_argument("--keep-setup", action="store_true",
                        help="keep the throwaway setup folder afterwards")
    args = parser.parse_args(argv)

    cache_dir = Path(tempfile.mkdtemp(prefix="pot-bench-setup-"))
    client = MetaClient(cache_dir=cache_dir)
    cleanup = None
    try:
        if args.preset or args.room_type:
            preset = PRESETS.get(args.preset, {})
            room_type = args.room_type or preset["room_type"]
            room_name = args.room_name if not args.preset else preset["room_name"]
            rules = args.rules or preset.get("rules", "")
            photos = args.photo or preset.get("photos", [])
            if not rules or not photos:
                parser.error("a room needs --rules and at least one --photo")
            setup_id = _make_throwaway(room_type, room_name, rules, photos)
            if not args.keep_setup:
                cleanup = setup_id
            print(f"Throwaway setup {setup_id}, room bench-room "
                  f"({room_type}, {len(photos)} photo(s); cache: {cache_dir})")
            _run(client, setup_id, "bench-room",
                 preset.get("checks", []), preset.get("counts", []))
        else:
            print(f"Setup {PORCH['setup_id']}, room {PORCH['room_id']} "
                  f"(cache: {cache_dir})")
            _run(client, PORCH["setup_id"], PORCH["room_id"],
                 PORCH["checks"], PORCH["counts"])
    finally:
        if cleanup:
            shutil.rmtree(setup_mod._setup_dir(cleanup), ignore_errors=True)


if __name__ == "__main__":
    main()
