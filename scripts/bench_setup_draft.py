"""Benchmark setup-draft model/effort combinations on the front porch.

Live calls only, with a throwaway model cache so nothing is replayed:
    .venv\\Scripts\\python scripts/bench_setup_draft.py

Drafts the front-porch room of setup-20261101-222836 (5 host rules,
1 photo) with each combination and prints seconds, rule-item count,
which host rules are missing, and the sam_prompts. A combination the
API refuses is skipped. The setup itself is never modified.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pot import config, setup as setup_mod  # noqa: E402
from pot.meta_client import MetaClient

SETUP_ID = "setup-20261101-222836"
ROOM_ID = "front-porch"

COMBOS = [
    (config.REASONING_MODEL, "medium"),
    (config.REASONING_MODEL, "low"),
    (config.REASONING_MODEL, "minimal"),
    (config.WONDER_MODEL, "low"),
    (config.WONDER_MODEL, "minimal"),
]

# The five host rules in the room's rule text, as keyword checks on the
# drafted rule items (label + requirement, lower case).
HOST_RULES = [
    ("porch clean", lambda text: "porch" in text and "clean" in text),
    ("door clean, no stains",
     lambda text: "door" in text and ("clean" in text or "stain" in text)),
    ("no shoes on the floor",
     lambda text: "shoe" in text and "floor" in text),
    ("seats hold only pillows",
     lambda text: any(word in text for word in ("couch", "seat", "loveseat", "chair"))
     and "pillow" in text),
    ("no dirt on the concrete",
     lambda text: "concrete" in text
     and any(word in text for word in ("dirt", "clean", "stain"))),
]


def main():
    cache_dir = Path(tempfile.mkdtemp(prefix="pot-bench-setup-"))
    client = MetaClient(cache_dir=cache_dir)
    print(f"Setup {SETUP_ID}, room {ROOM_ID} (cache: {cache_dir})")
    print("| Model | Effort | Seconds | Rule items | Missing host rules | sam_prompts |")
    print("|---|---|---:|---:|---|---|")
    for model, effort in COMBOS:
        started = time.perf_counter()
        try:
            draft = setup_mod.draft_room(
                client, SETUP_ID, ROOM_ID,
                effort=effort, model=model, store=False,
            )
        except Exception as exc:  # noqa: BLE001 - refused combos are skipped
            print(f"| {model} | {effort} | - | - | SKIPPED: {exc} | - |")
            continue
        seconds = time.perf_counter() - started
        rules = [item for item in draft["items"] if item["source"] == "rule"]
        texts = [f"{item['label']} {item['requirement']}".lower() for item in rules]
        missing = [name for name, check in HOST_RULES
                   if not any(check(text) for text in texts)]
        prompts = ", ".join(
            f"{item['key']}={item['sam_prompt'] or '-'} " for item in rules
        ).strip()
        print(
            f"| {model} | {effort} | {seconds:.1f} | {len(rules)} | "
            f"{', '.join(missing) or 'none'} | {prompts or '-'} |"
        )


if __name__ == "__main__":
    main()
