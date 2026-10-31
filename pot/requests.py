"""One short re-clean request per unproven critical item, in two languages.

A single Muse Spark call writes each request in the property's
cleaner_language plus an English version for the host.
"""

from pot import prompts
from pot.meta_client import MetaClientError
from pot.proof import PROVEN


def requests_schema(item_ids):
    return {
        "type": "object",
        "properties": {
            "requests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string", "enum": list(item_ids)},
                        "cleaner": {"type": "string"},
                        "host": {"type": "string"},
                    },
                    "required": ["item_id", "cleaner", "host"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["requests"],
        "additionalProperties": False,
    }


def write_requests(client, property, checklist, proofs, effort=None, item_ids=None,
                   language=None):
    """Write requests for unproven critical items; returns (requests, notes).

    Requests are grouped by room in house order (whole-house items last).
    Makes no call when everything critical is proven. With item_ids, only
    those items are considered (one tour photo writes only its own shot).
    The cleaner text uses language (default: the property's).
    """
    effort = prompts.REASONING_EFFORT if effort is None else effort
    language = language or property.get("cleaner_language", "en")
    by_id = {item.id: item for item in checklist}
    room_order = {room["id"]: index for index, room in enumerate(property["rooms"])}
    wanted = [
        item for item in checklist
        if item.critical and proofs.get(item.id) is not None
        and proofs[item.id].status != PROVEN
        and (item_ids is None or item.id in item_ids)
    ]
    if not wanted:
        return [], []
    entries = [
        {
            "item_id": item.id,
            "room_name": item.room_name,
            "label": item.label,
            "requirement": item.requirement,
            "status": proofs[item.id].status,
            "reason": proofs[item.id].reason,
        }
        for item in wanted
    ]
    try:
        answer = client.respond(
            f"requests ({len(entries)} items)",
            [{"type": "input_text", "text": "Write the re-cleaning requests."}],
            instructions=prompts.requests_instructions(language, entries),
            schema=requests_schema([item.id for item in wanted]),
            effort=effort,
        )
    except MetaClientError as exc:
        notes = [f"Requests: check failed: {exc}"]
        return [_fallback(item, proofs[item.id]) for item in wanted], notes
    notes = []
    found = {}
    raw = answer.get("requests") if isinstance(answer, dict) else None
    if not isinstance(raw, list):
        notes.append("Requests: bad answer shape, fell back to requirements")
        raw = []
    wanted_ids = {item.id for item in wanted}
    for entry in raw:
        if not isinstance(entry, dict):
            notes.append("Requests: skipped a malformed entry")
            continue
        item_id = entry.get("item_id")
        if item_id not in wanted_ids:
            notes.append(f"Requests: unknown item id {item_id!r}")
            continue
        if item_id in found:
            notes.append(f"Requests: duplicate entry for {item_id}, kept the first")
            continue
        item = by_id[item_id]
        cleaner = entry.get("cleaner") or ""
        host = entry.get("host") or ""
        if not cleaner.strip() or not host.strip():
            notes.append(f"Requests: empty text for {item_id}, fell back to requirement")
            found[item_id] = _fallback(item, proofs[item_id])
            continue
        found[item_id] = {
            "item_id": item_id,
            "room_id": item.room_id,
            "room_name": item.room_name,
            "status": proofs[item_id].status,
            "cleaner": cleaner,
            "host": host,
        }
    for item in wanted:
        if item.id not in found:
            notes.append(f"Requests: no request returned for {item.id}, fell back to requirement")
            found[item.id] = _fallback(item, proofs[item.id])
    ordered = sorted(
        found.values(),
        key=lambda r: (room_order.get(r["room_id"], len(room_order)), r["item_id"]),
    )
    return ordered, notes


def _fallback(item, proof):
    return {
        "item_id": item.id,
        "room_id": item.room_id,
        "room_name": item.room_name,
        "status": proof.status,
        "cleaner": item.requirement,
        "host": item.requirement,
    }
