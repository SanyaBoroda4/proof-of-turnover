# Step 55e — replay recording

Recorded live on the final code (after Steps 55a–55d, plus the
deterministic-item-order fix below) with a fresh server:
`POT_MODEL_CACHE_DIR=replay/model_cache` (started empty),
`POT_VOICE=device`, a temp runs folder (never `data/runs`).

## Deterministic item order (fix found while recording)

The first recording replayed with cache misses: the check worker
built its item list by iterating a Python `set`, so the item order
inside each model request — and therefore its cache key — changed
from process to process. `_check_worker` now builds the list in
checklist order. The cache was wiped and re-recorded after the fix;
the replay then matched every call.

## Pass 1 — `--map replay/map.json --demo-run --leave-cards`

10 photos (foyer, powder room ×4, laundry ×4, dining), 9 rooms skipped
with skip-room. All checked 50 s after the last upload. Two cards:

| Photo | Items | Status |
|---|---|---|
| P02 powder-00 | powder-room.toilet_paper | problem — partly used roll on the holder |
| P02 powder-00 | powder-room.spare_toilet_paper | needs photo — checks disagree |
| P06 laundry-04 | laundry.floor_clear | problem — white laundry pile on the floor |
| P06 laundry-04 | laundry.no_dirty_linens | problem — same pile (shown once on the card, Step 55a) |

## Pass 2 — `--demo-run --actions replay/actions.json`

- Laundry card: new photo `replay/photos/11.jpg` (shot laundry-04,
  retake_items = both laundry items) → both proven, with a
  `.before.jpg` for each (honest “Fixed by cleaner”).
- The powder card is left open in `actions.json` for the judge.

## Wonder priming (separate throwaway run, not in actions.json)

Pass 1 produced no “It's fine” card, so no second opinion would have
been recorded. In a throwaway run on the same recording server the
powder card was answered “It's fine” once, recording live Muse
Wonder opinions for both powder items (“still a problem”, with
evidence sentences). Those answers are now in the cache: if a judge
answers the open powder card with “It's fine” (or “Can't fix” with a
reason other than supplies), the host page shows the recorded
second opinion. Answering “Can't fix → Out of supplies” needs no
model call, as designed.

## Pass 3 — `python scripts/replay_demo.py` (no key, cache only)

- 53 calls in the run's call log, **every one a cache hit
  (misses = 0)**; no unchecked photos.
- Same cards as pass 1; the laundry items are proven with
  `has_before`; the powder card is open.
- Browser/API flow check: answered both powder items
  “Can't fix → Out of supplies” (200), “Send to host” (200),
  approve without force correctly refused (run in `needs_input`
  because the 9 skipped rooms stay not-photographed), approve with
  the host confirm (200) → final state `done`. Cleaner and host
  pages return 200.

## Numbers and privacy

- `replay/model_cache`: **53 files**.
- Privacy check of the cache text (photo base64 excluded): no
  street or city, no people's names, no emails, no “Airbnb”, no key
  or token text. One regex hit for an email pattern is a base64
  fragment inside image data — not an address.

Reports of the earlier Step 54 run are in `reports/rehearsal-54.md`.
