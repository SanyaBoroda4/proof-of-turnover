# Step 32: porch seats rule, box every wrong thing, one-photo shots, example photos, skip in test mode

`.env` never opened or printed. No changes to the proof rule or scoring.
Page changes minimal (redesign comes next step).

## A. Porch rules (`properties/rental-1/property.json` only)

- `porch.area_tidy`: "Porch floor, steps, walkway and tables clean: no
  shoes, toys, bags, packages, trash or stains." (patio: "Back porch
  floor, ...").
- New critical items `porch.seats_only_pillows` /
  `patio.seats_only_pillows`: "Seats hold only the outdoor pillows (a
  throw blanket is fine): nothing else on the loveseat or chairs — no
  bags, clothes, towels, toys, boxes or packages." `sam_prompt` null,
  `problem_area` null. Pillows items kept.
- Checklist grows 80 → 82 items (`test_rental_1_v6_checklist` updated:
  new count plus assertions on the new items).

## B. Box every wrong thing, not just one

- Second look returns `problem_objects`: up to 3 different simple
  singular nouns (`["shoe", "bag", "towel"]`); `problem_object` stays
  as the first one for older code paths (prompt + schema +
  `SecondLook.problem_objects`, tolerant of old answers that only have
  the single noun).
- Problems without `problem_area`: one SAM call per noun in parallel,
  each refined with today's rules (1 box kept, 2–8 one pick call, more
  than 8 one area box), then all kept boxes drawn, at most 10 in total
  (`PROBLEM_BOX_CAP`, with a note when truncated). `problem_area`
  items unchanged. Live (`recheck.py` chaining) and batch
  (`segment_problem_objects`) paths both, sharing
  `problem_nouns()` + `combine_problem_boxes()`.

## C. One photo per shot

- Tour prompt gains the one-photo paragraph (one photo from one spot;
  toilet-bowl vs vanity and fridge-interior vs doors are separate
  shots; close-ups for small things; more shots over crowded shots).
- `properties/rental-1/tour.json` backed up as `tour.v1.json` (32
  shots), regenerated with `python -m pot.tour rental-1` → **39
  shots**, no fixup extras, all 77 critical items covered
  (asserted programmatically). Every shot checked doable as one photo:

porch-01 (floor, pillows, seats), foyer-01 (floor, baseboards),
powder-01 (toilet), powder-02 (holder + spare roll),
powder-03 (sink, towel, clear), laundry-01 (washer), laundry-02
(dryer), laundry-03 (lint filter), laundry-04 (floor, linens),
kitchen-01 (beans), kitchen-02 (fridge), kitchen-03 (left door),
kitchen-04 (right door), kitchen-05 (freezer), kitchen-06
(microwave), kitchen-07 (oven), kitchen-08 (dishwasher),
kitchen-09 (stovetop), kitchen-10 (counters, cabinets, machine),
kitchen-11 (trash), kitchen-12 (paper towels), living-01 (TV
linens), living-02 (couch, pillows, floor),
living-03 (fan), dining-01 (table, chairs, floor), patio-01
(floor, pillows, seats), master-bedroom-01 (both beds, floor),
master-bathroom-01 (toilet), master-bathroom-02 (rolls),
master-bathroom-03 (sink), master-bathroom-04 (tub),
master-bathroom-05 (shelves, towels), hall-bathroom-01..05
(same split), bedroom-1-01, bedroom-2-01 (bed, room).

Note: Step 29's `data/inputs/rehearsal_map.json` (gitignored) still
maps the old 32 shot ids; no rehearsal was re-run in this step.

## D. Example photos ("how it should look")

- New `pot/examples.py`: `data/examples/<property>/<shot>.jpg`,
  1200 px long side max (no upscaling), EXIF stripped.
  `GET /api/runs/<id>/tour` adds `example_url` per shot that has one;
  served by `GET /api/properties/<id>/examples/<shot_id>` behind the
  access code. The shot screen shows the example small (max 200 px).
- rental-1: `data/inputs/examples_map.json`, 20 shots with examples,
  19 without. Every candidate was viewed; exclusions for people
  (foot+fingers in Freezer, hand in Dryer Filter and Kitchen
  countertop peninsula, legs/feet in both open-bin photos), for wrong
  content (Opened Microwave shows the stained wall, bins with closed
  lids, lid-closed toilets for lid-open shots, towel bar for the
  shelves shot), and for missing subjects (porch, patio, washer,
  dryer, beans, fridge doors, trash with liners, paper roll, TV
  stand, both beds in one frame, hall tub, powder spare roll).
- Learned examples: on host approve, each shot without an example
  whose critical items were all proven by one photo learns that
  photo. Setup-made houses: on save, each shot gets its room's first
  setup photo.

## E. Skip a shot in test mode

- `POST /api/runs/<id>/skip-shot {shot_id}` (400 for unknown or
  already-taken shots): the shot counts as skipped exactly like
  end-round (no cards for skipped-only items, finish allows them,
  including coverage when all of a room's shots are skipped). Small
  `Skip (test)` button on the shot screen, only with `?test=1` or
  `?demo=1`; round flow (`roundCurrent`/`allTaken`) treats skipped
  shots as done.

## F. Tests + live verification — 284 passed

- New: second-answer list parsing (×2), multi-noun combine + 10-cap
  (×2), `example_url` + file serving, approve-learns-examples,
  setup-save examples, skip-shot end to end. `conftest.py` now also
  isolates `EXAMPLES_DIR`. `clean.html` passes `node --check`.
- Live: `python -m pot.turnover` on `.../Specific Common
  Issues - 2 (safe)` (`run-20261101-170038`, 53 calls, 115 s) +
  `score_first_look` with `answer_key_issues2.v2.json`: rooms
  **11/11**, first look **14/14**, finals **14/14** (new seats items
  not in the key; both extra findings are real: dryer-area clothes
  and the loveseat clothes/sandals).
- Porch boxes (photo_10): `porch.area_tidy` nouns `["shoe", "toy"]`
  → 7 boxes; `porch.seats_only_pillows` nouns `["clothes", "shoe"]`
  → 7 boxes. The seats evidence (viewed) boxes the black clothes and
  sandals on the loveseat plus the shoes by the door — the host's
  exact complaint, fixed.
