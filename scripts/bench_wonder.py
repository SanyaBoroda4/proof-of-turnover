"""Live benchmark: independent second opinion on the Nov 2 runs.

Replays the 20 second-opinion calls from two real runs, each with that
run's photos/<photo>_check.jpg, under four configs. Live calls only,
with a throwaway model cache so nothing is replayed from disk:

    .venv\\Scripts\\python scripts/bench_wonder.py

Truth (Alex): strict NOT-met cases must never come back "met"; the
flexible cases accept not_met/cannot_tell or met/cannot_tell as noted.
Prints one row per case and config, then a summary per config.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pot import config, prompts  # noqa: E402
from pot.checklist import build_checklist, load_property  # noqa: E402
from pot.meta_client import MetaClient  # noqa: E402

RUNS = ["run-20261102-151935", "run-20261102-150612"]

# (run, item_id, photo_id) -> acceptable requirement_met values
STRICT_NOT_MET = {
    ("run-20261102-151935", "hall-bathroom.towels", "P26"),
    ("run-20261102-151935", "hall-bathroom.shelves", "P26"),
    ("run-20261102-151935", "hall-bathroom.spare_toilet_paper", "P30"),
    ("run-20261102-151935", "hall-bathroom.shower", "P25"),
    ("run-20261102-151935", "hall-bathroom.no_guest_items", "P25"),
    ("run-20261102-150612", "master-bathroom.towels", "P23"),
    ("run-20261102-150612", "master-bathroom.shelves", "P23"),
    ("run-20261102-150612", "hall-bathroom.towels", "P29"),
    ("run-20261102-150612", "hall-bathroom.shelves", "P29"),
    ("run-20261102-150612", "hall-bathroom.spare_toilet_paper", "P26"),
}
FLEX_NOT_MET = {  # not_met or cannot_tell
    ("run-20261102-151935", "powder-room.spare_toilet_paper", "P05"),
}
FLEX_MET = {  # met or cannot_tell
    ("run-20261102-151935", "porch.area_tidy", "P01"),
    ("run-20261102-151935", "powder-room.toilet_paper", "P27"),
    ("run-20261102-151935", "hall-bathroom.toilet_paper", "P30"),
}

CONFIGS = [
    (config.WONDER_MODEL, "low"),
    (config.WONDER_MODEL, "medium"),
    (config.WONDER_MODEL, "high"),
    (config.REASONING_MODEL, "medium"),
]

SCHEMA = {
    "type": "object",
    "properties": {
        "evidence": {"type": "string"},
        "requirement_met": {
            "type": "string", "enum": ["met", "not_met", "cannot_tell"],
        },
    },
    "required": ["evidence", "requirement_met"],
    "additionalProperties": False,
}


def _cases():
    import json

    items = {item.id: item for item in build_checklist(load_property("rental-1"))}
    cases = []
    for run in RUNS:
        run_dir = Path("data/runs") / run
        for line in (run_dir / "calls.jsonl").read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            purpose = rec.get("purpose") or ""
            if not purpose.startswith("second opinion "):
                continue
            _, _, rest = purpose.partition("second opinion ")
            item_id, _, photo_id = rest.rpartition(" ")
            key = (run, item_id, photo_id)
            if key in STRICT_NOT_MET:
                truth = "not_met"
            elif key in FLEX_NOT_MET:
                truth = "not_met|cannot_tell"
            elif key in FLEX_MET:
                truth = "met|cannot_tell"
            else:
                truth = "report"
            cases.append({
                "run": run, "item_id": item_id, "photo_id": photo_id,
                "truth": truth, "item": items[item_id],
                "photo": run_dir / "photos" / f"{photo_id}_check.jpg",
            })
    return cases


def _guard_ok(evidence):
    from pot.server import wonder_guard

    return wonder_guard(evidence) is None


def main():
    cases = _cases()
    print(f"{len(cases)} cases")
    cache_dir = Path(tempfile.mkdtemp(prefix="pot-bench-wonder-"))
    client = MetaClient(cache_dir=cache_dir)
    results = {}  # (model, effort) -> dict of stats
    rows = []
    for case in cases:
        item = case["item"]
        raw = case["photo"].read_bytes()
        import base64

        content = [{
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,"
            + base64.b64encode(raw).decode("ascii"),
        }]
        instructions = prompts.second_opinion_instructions(
            item.room_name, item.label, item.requirement, item.min_count)
        for model, effort in CONFIGS:
            started = time.perf_counter()
            try:
                answer = client.respond(
                    f"bench wonder {case['item_id']} {case['photo_id']}",
                    content, instructions=instructions, schema=SCHEMA,
                    model=model, effort=effort,
                )
                seconds = time.perf_counter() - started
                met = answer.get("requirement_met")
                evidence = str(answer.get("evidence", "") or "")
                guard_ok = _guard_ok(evidence)
            except Exception as exc:  # noqa: BLE001 - report and continue
                seconds = time.perf_counter() - started
                met, evidence, guard_ok = f"ERROR {exc}", "", False
            stats = results.setdefault((model, effort), {
                "wrong_met": 0, "guard_fail": 0, "flex_fail": 0,
                "seconds": 0.0, "n": 0,
            })
            stats["seconds"] += seconds
            stats["n"] += 1
            if not guard_ok:
                stats["guard_fail"] += 1
            if case["truth"] == "not_met" and met == "met":
                stats["wrong_met"] += 1
            if case["truth"] in ("not_met|cannot_tell", "met|cannot_tell"):
                allowed = case["truth"].split("|")
                if met not in allowed:
                    stats["flex_fail"] += 1
            rows.append((
                f"{case['run'][-6:]} {case['item_id']} {case['photo_id']}",
                case["truth"].replace("|", "/"), model, effort, str(met),
                f"{seconds:.1f}",
                "ok" if guard_ok else "GUARD",
            ))
    print("| Case | Truth | Model | Effort | Answer | Sec | Guard |")
    print("|---|---|---|---|---|---:|---|")
    for row in rows:
        print("| " + " | ".join(row) + " |")
    print()
    print("| Model | Effort | Wrong met (strict) | Guard fails | Flex fails | Avg sec |")
    print("|---|---|---:|---:|---:|---:|")
    for (model, effort), stats in results.items():
        avg = stats["seconds"] / max(stats["n"], 1)
        print(f"| {model} | {effort} | {stats['wrong_met']} | "
              f"{stats['guard_fail']} | {stats['flex_fail']} | {avg:.1f} |")
    print(f"\nThrowaway cache: {cache_dir}")


if __name__ == "__main__":
    main()
