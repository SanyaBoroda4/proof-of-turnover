# Step 29: dress rehearsal, new turnover button, screen stays awake, upload timeout

`.env` never opened or printed. No changes to the proof rule, scoring,
prompts, tour files or the setup page.

## Status: full loop proven, live-model judgments blocked by the API key

Two full 32-shot rehearsals ran on this PC against a local server
(`run-20261101-145147`, `run-20261101-145738`, pace 8 s, language en).
The whole phone flow works end to end: start → 32 uploads in tour order →
cards → fridge retake → resolves → finish → `awaiting_signoff`.

But every model call failed with **HTTP 401 Unauthorized** (161/161 calls
in each run, 0 live). `scripts/smoke_test.py` fails the same way on all
three checks, including plain `GET /v1/models`, so the key itself is
rejected — it worked yesterday (step 20, Oct 31). Nothing was judged:
no item proven (except the 13 automatic room-coverage items), no problem
verdicts, all 62 cards "needs photo" with fallback request texts (the
item requirements, by design when the requests call fails). The runs are
kept under `data/runs/` (gitignored) for inspection.

To get the real rehearsal: put a fresh key in `.env` (`MODEL_API_KEY`)
and re-run:

    $env:POT_ACCESS_CODE='<code>'; .venv\Scripts\python -m pot.server --host 127.0.0.1 --port 8000
    .venv\Scripts\python scripts\rehearsal.py --base http://127.0.0.1:8000 --code <code> --language en --pace 8

## Part A — three filming-day features (`web/clean.html`, page-only)

1. **New turnover button** on the final screen: en "New turnover",
   es "Nuevo turno", ru "Новая уборка". It clears `RUN`, drops `?run=`
   from the URL, resets all run state and shows the choice screen
   (Set up a new house / Start turnover). Starting always POSTs
   `/api/runs`, i.e. always a fresh run, never the finished one.
2. **Screen wake lock**: `navigator.wakeLock.request("screen")` when the
   round starts (and when reopening a live run), again on
   `visibilitychange` when the page becomes visible and the run is not
   finished, released on the final screen. No wake lock → silently
   nothing.
3. **Upload timeout**: photo uploads (`sendShot` and card retakes) go
   through `apiUpload()` — `AbortController` aborts after 30 s. Round
   uploads show the existing "Photo not sent – tap to send again" retry
   thumbnail; card retakes show that same text and the card returns to
   the queue.

Verified with `node --check` on the extracted page script (passes);
the user verifies behavior in a browser.

## What broke in the rehearsal and what was fixed

1. **Rehearsal finished nothing (`scripts/rehearsal.py`, first run)**:
   after resolving all 62 cards the script refused to call `/finish`
   because `status["open"]` still listed everything. Cause: `open` is
   the host verdict (`proof.verdict`), which ignores resolutions by
   design; the phone-equivalent list is `status["cards"]`, which
   excludes resolved items. The script now gates on `cards` (+ `agent_state`).
   Second run: `Finished at 2026-11-01T15:01:57; agent_state=awaiting_signoff`.
2. **`scripts/timing.py` crashed** with `TypeError: '<' not supported
   between instances of 'dict' and 'dict'`: `min()` over `(time, row)`
   tuples compares the dicts when two `card_ready` events share the
   same millisecond timestamp (common when photos fail fast). Fixed
   both `min()` calls with `key=lambda pair: pair[0]`.
   Regression test: `test_timing_same_millisecond_card_ready` (failed
   before the fix with the same TypeError, passes after).

No server (`pot/`) changes were needed: no crash, no photo stuck in
checking, no timeouts, no 429s across both runs.

## Photo map (`data/inputs/rehearsal_map.json`, 32/32, gitignored)

Pass shots use the Clean House photo that best shows the shot's items;
exactly 4 problem shots come from `.../Specific Common Issues - 2 (safe)`
via `answer_key_issues2.v2.json`. Every mirror/vanity photo used was
viewed and shows no person. Hands are visible in three mandated problem
photos (hand on the fridge door in photo_4, hand on the holder in
photo_2, blurry feet reflection on the washer front in photo_9) — there
are no alternative photos of these problems, and the folder was already
vetted as "(safe)" (photo_7/8 reflections blurred).

| shot | file (short) | expect |
|---|---|---|
| porch-01 | issues2/photo_10 | PROBLEM: shoes, bag, toy on porch |
| foyer-01 | Entrance Corridor.jpeg | pass: corridor floor + baseboards |
| powder-room-01 | Powder Toilet.jpeg | pass: toilet + rolls |
| powder-room-02 | Powder Vanity.jpeg | pass: sink, mirror, soap |
| laundry-01 | issues2/photo_9 | PROBLEM: clothes in washer |
| laundry-02 | Dryer Filter.jpeg | needs-photo: filter, not dryer drum |
| laundry-03 | Dryer Filter.jpeg | pass: lint filter close-up |
| laundry-04 | Dryer Filter.jpeg | needs-photo: floor not shown |
| kitchen-01 | Kitchen countertop.jpeg | needs-photo: no bean photo exists |
| kitchen-02 | Opened Fridge.jpeg | pass: empty main compartment |
| kitchen-03 | issues2/photo_4 | PROBLEM: left fridge door full |
| kitchen-04 | Opened Fridge.jpeg | needs-photo: right door not shown |
| kitchen-05 | Freezer.jpeg | pass: open freezer |
| kitchen-06 | Opened Microwave.jpeg | pass: open microwave |
| kitchen-07 | Stove and Range.jpeg | needs-photo: no open-oven photo |
| kitchen-08 | Empty Dishwasher.jpeg | pass: empty racks |
| kitchen-09 | issues2/photo_2 | PROBLEM: paper towel holder empty |
| kitchen-10 | Empty Kitchen Garbage Bins.jpeg | pass: both bins empty + liners |
| dining-room-01 | Dining Table.jpeg | pass: clear table + chairs |
| patio-01 | issues2/photo_8 | pass: no Clean patio photo; tidy patio |
| living-room-01 | Living Room Carpet.jpeg | needs-photo: no TV-stand photo |
| living-room-02 | Living Room Couch and Carpet.jpeg | pass: couch + pillows + carpet |
| living-room-03 | Fan Living Room.jpeg | pass: ceiling fan |
| master-bedroom-01 | Master Bedroom bed.jpeg | pass: made bed; bed_2 out of frame |
| master-bathroom-01 | Master Toilet.jpeg | pass: toilet + rolls + shelves |
| master-bathroom-02 | Master Vanity.jpeg | pass: sink + folded towels |
| master-bathroom-03 | Master Tub.jpeg | pass: clean tub + shampoo |
| hall-bathroom-01 | Half Bath Toilet.jpeg | pass: toilet + 2 rolls + shelves |
| hall-bathroom-02 | Half Bath Vanity.jpeg | pass: clean sink + hand towel |
| hall-bathroom-03 | Half Bath Folder Towels.jpeg | pass?: towels + curtain edge |
| bedroom-1-01 | Bed room 1 carpet.jpeg | pass: made bed foot + carpet |
| bedroom-2-01 | Bedroom 2.jpeg | pass: made bed + carpet |

## Timing summary (`run-20261101-145738`, 33 photos, 161 calls)

Every photo finished in under a second because every model call failed
fast (401, 1 attempt each — 401 is not retried). No 429s.

Per photo (received → finished): P01 0.4 s, P02 0.3 s, P03 0.4 s,
P04 0.4 s, P05 0.3 s, P06 2.6 s, P07 0.2 s, P08 0.2 s, P09 0.3 s,
P10 0.2 s, P11 0.5 s, P12 0.2 s, P13 0.2 s, P14 0.2 s, P15 0.6 s,
P16 0.2 s, P17 0.7 s, P18 0.5 s, P19 0.4 s, P20 0.3 s, P21 0.3 s,
P22 0.4 s, P23 0.2 s, P24 0.5 s, P25 0.7 s, P26 0.4 s, P27 0.5 s,
P28 0.4 s, P29 0.4 s, P30 0.4 s, P31 0.5 s, P32 0.5 s, P33 0.2 s.

Slowest calls (all `failed`, attempts=1):
2.53 s second look laundry.dryer P06; 1.69 s first look laundry (P06);
0.50 s second look kitchen.paper_towels P17; 0.48 s first look
kitchen (P15); 0.41 s second look master-bathroom.toilet P25; 0.40 s
sam master-bathroom.shelves P25 (towel); 0.39 s second look
kitchen.coffee_machine_clean P17; 0.38 s first look master-bedroom (P24).

Last upload 15:01:51 → all checked 0.2 s → first card ready: none new
after the last upload. First card overall: porch.area_tidy at
14:57:38 (right after the first photo). Open critical items: 62.

## Cards (62, all "needs photo", fallback requirement texts)

All request texts below are the item requirements (`requests._fallback`),
because the requests model call also got 401 — not real generated
requests. Order is tour order, as the phone shows them.

- porch.area_tidy [P01]: Front porch and the walkway in front of it clean: no stains or trash, no shoes, toys, bags or clothes left out (a throw blanket on the seats is fine)
- porch.pillows [P01]: Outdoor pillows on the seats
- foyer.floor_clear [P02]: Floor is clean: nothing left on it, no spots, spills or crumbs
- powder-room.toilet [P03]: Toilet lid open for the photo: inside of the bowl clean (no stains or residue), water at a normal level (not clogged, no overflow), seat and rim dry and clean
- powder-room.toilet_paper [P03]: A new, full toilet paper roll on the holder next to the toilet (a folded end is fine)
- powder-room.spare_toilet_paper [P03]: Besides the roll on the holder, at least one spare toilet paper roll, for example on the toilet tank (the photo shows at least 2 rolls)
- powder-room.sink [P04]: Sink and mirror are clean
- powder-room.hand_towel [P04]: A hand towel is in place
- powder-room.no_guest_items [P04]: No guest items left on the sink or the floor
- laundry.machines [P05]: Washer lid open: drum empty, no clothes or towels left inside
- laundry.dryer [P06]: Dryer door open: drums
...[truncated 9958 chars]