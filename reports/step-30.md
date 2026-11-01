# Step 30: key check, back navigation, finish early for tests, AI-failure warnings, real rehearsal

`.env` never opened or printed. No changes to the proof rule, scoring or
prompts. Step 29's work was committed first (`9d6f9b7`), then this step.

## 0. Key check

`scripts/smoke_test.py`: all 3 checks pass with the new key (9 models,
`spark` answers "hello", SAM finds 3/3 squares). Full suite green (271),
then Step 29 committed as
"Step 29: dress rehearsal, new turnover button, screen stays awake,
upload timeout".

## 1. Never fail silently when the AI service fails

- **Startup check** (`pot/meta_client.py` `list_models()`, `pot/server.py`
  `api_startup_line()`): one `GET /v1/models` (15 s timeout, no retries)
  at server start. Prints `Meta API: OK` (seen live at rehearsal start)
  or `Meta API: key rejected (401) — put a valid MODEL_API_KEY in .env`.
  Never prints the key. Skipped in replay mode.
- **Host banner** (`web/host.html`, `status["api_status"]`): red banner
  `AI checks are failing: <reason>` while model calls fail for API
  reasons (401/403, 5xx, timeouts/connection errors after the retry);
  clears on the next successful call. Reasons: `key rejected (401)`,
  `Meta API error (503)`, `Meta API unreachable (timeout)`.
- **Not-checked photos** (`pot/runs.py`, `pot/server.py`, `web/clean.html`):
  when any check call (first/second look, SAM) for a photo fails for an
  API reason, the photo is `unchecked` — saved, judged nothing, merged
  nothing — and its items make no cards. Judgments from real answers
  (including other photos') stay untouched. The phone shows a small
  line at the top (en `⚠ Some photos couldn't be checked — they are
  saved`, es/ru translated) and, on the cards screen, one card
  `N photos couldn't be checked` with `Try again`, which POSTs the new
  `POST /api/runs/<id>/recheck-unchecked` endpoint. `/finish` refuses
  while photos are unchecked (`N photos could not be checked, try
  again`). Unchecked + banner state survive a server restart
  (persisted in `first_look.json` / recomputed from `calls.jsonl`).

## 2. Back navigation

- Phone back button/gesture goes one screen back inside the app
  (`history.pushState` per screen + `popstate`): clean
  language → start → house list → round/cards (back from a live run
  asks `Leave this turnover? Your photos are saved.` and lands on the
  house list; back from the final screen starts over); setup house
  form → room 1 → room 2 … → review → done. The in-app back links use
  the same stack.
- `setup.html` has `‹ Home` at the top of every screen → the `/clean`
  start screen. The setup is kept (server side + `pot-setup` /
  `pot-setup-at` in localStorage) and resumes where it was when the
  host comes back. The round has a small `Exit` link at the top with
  the same confirm; starting after that creates a new run, as now.
- `/clean` with a previously chosen language opens straight on the
  start screen, with the `Idioma · Language · Язык` link at the bottom;
  no auto-speech, speech starts with the next tap.

## 3. "Finish photos now" for tests and demos only

With `?test=1` or `?demo=1` (kept when moving between `/clean` and
`/setup`), the round shows a small `Finish photos now (test)` link at
the bottom → confirm → new `POST /api/runs/<id>/end-round`. Untaken
shots are skipped: no cards for items shown only in skipped shots
(cards still appear for photos taken, including items shared with a
skipped shot), and `/finish` + `agent_state` excuse them (including
room-coverage items of unphotographed rooms). The host page shows
`Ended early: N shots skipped (test)`. Without test/demo in the link
nothing changes for the real cleaner. `scripts/rehearsal.py` also
retries `unchecked` photos (3 rounds) before resolving.

## 4. Real dress rehearsal (`run-20261101-153858`, pace 8, en)

First-try clean pass, no crashes, no stuck photos, no unchecked, no
429s. 167 calls, all `live`. Checks kept up with the walking pace:
only P31/P32 were still checking at the last upload.

Per photo (received → finished): P01 36.0 s, P02 7.7 s, P03 15.4 s,
P04 26.0 s, P05 25.3 s, P06 16.7 s, P07 10.7 s, P08 13.8 s,
P09 14.0 s, P10 7.6 s, P11 30.2 s, P12 13.4 s, P13 5.6 s, P14 13.3 s,
P15 12.8 s, P16 7.4 s, P17 20.8 s, P18 19.8 s, P19 17.5 s, P20 7.8 s,
P21 18.0 s, P22 27.2 s, P23 11.9 s, P24 27.1 s, P25 29.4 s,
P26 26.2 s, P27 18.9 s, P28 23.4 s, P29 19.9 s, P30 14.4 s,
P31 28.0 s, P32 5.5 s, P33 (retake) 13.7 s.

Slowest calls (all live, 1 attempt): 21.91 s first look bedroom-1
(P31); 21.65 s first look living-room (P22); 21.33 s first look
master-bedroom (P24); 21.12 s second look bedroom-1.no_belongings
(P31); 19.14 s first look master-bathroom (P26); 18.26 s first look
powder-room (P04); 17.95 s first look hall-bathroom (P28); 17.17 s
pick problem boxes porch.area_tidy P01 (shoe).

Last upload 15:43:06 → all checked 20.1 s → first card ready: none
new after the last upload. First card overall: powder-room.toilet_paper
at 15:39:29. Open critical items: 34.

The 4 planted problems all became **problem** cards **with red boxes**
(verified: `problem_sam` + evidence file for each; porch evidence
viewed — shoes boxed in red):
porch.area_tidy (P01), laundry.machines (P05), kitchen.fridge_left_door
(P11), kitchen.paper_towels (P17). Three extra problem judgments
(model's own call, reported as-is): powder-room.hand_towel (no towel
in photo), master-bathroom.toilet_paper (roll not full), and a real
catch — kitchen.microwave (brown stains on the back wall that the
"Clean House" photo actually shows).

Fridge retake: **not proven** — P33 (empty main compartment) says
nothing about the left-door shelves, so the P11 problem verdict stands
and the card stayed (then resolved `its_fine` in the sweep, as
scripted). Correct per the proof rule; for filming, the retake photo
should show the emptied left-door shelves.

End state: finished 15:43:42, `agent_state=awaiting_signoff`,
41 critical items proven, 34 cards resolved (1 `cant_fix`/supplies:
porch.area_tidy; 33 `its_fine`), 0 fixed-by-cleaner, `api_status` ok.

All 34 cards with their real request texts (tour order):

- porch.area_tidy [problem, P01]: Remove the shoes by the door, the clothes on the loveseat, and the yellow toy from the walkway and send a new photo.
- powder-room.toilet_paper [needs photo, P03]: Send a photo of the new full toilet paper roll on the holder next to the toilet in the powder room.
- powder-room.spare_toilet_paper [needs photo, P03]: Send a photo showing at least two toilet paper rolls in the powder room including the spare roll on the toilet tank.
- powder-room.hand_towel [problem, P04]: Place a hand towel in the powder room and send a new photo.
- powder-room.no_guest_items [needs photo, P04]: Send a photo of the powder room sink and floor showing no guest items left.
- laundry.machines [problem, P05]: Remove all clothes from the washer drum, leave the lid open with the drum empty, and send a new photo.
- laundry.dryer [needs photo, P06]: Send a photo of the open dryer door showing the empty drum with no clothes or towels inside.
- laundry.floor_clear [needs photo, P08]: Send a photo of the laundry room floor showing it is clear and clean.
- laundry.no_dirty_linens [needs photo, P08]: Send a photo of the laundry baskets and floor showing no dirty linens are left.
- kitchen.coffee_beans [needs photo, P09]: Send a photo of the espresso machine bean hopper with the lid off showing coffee beans inside.
- kitchen.fridge_left_door [problem, P11]: Remove all jars, bottles, milk and food from the left fridge door shelves so they are completely empty and send a new photo.
- kitchen.fridge_right_door [needs photo, P12]: Send a photo of the open right fridge door showing all door shelves empty.
- kitchen.microwave [problem, P14]: Clean the brown stains on the back wall inside the microwave and send a new photo.
- kitchen.oven [needs photo, P15]: Send a photo of the open oven showing the clean floor, walls and racks.
- kitchen.counters [needs photo, P17]: Send a photo of the wiped kitchen counters and empty clean sink.
- kitchen.stovetop [needs photo, P17]: Send a photo of the wiped glass stovetop with no spills, crumbs or rings.
- kitchen.paper_towels [problem, P17]: Put a paper towel roll on the empty gold holder on the counter and send a new photo.
- kitchen.trash [needs photo, P18]: Send a photo of both empty kitchen trash bins with new liners.
- dining-room.floor_clear [needs photo, P19]: Send a photo of the dining area floor showing it is clear with no spots, spills or crumbs.
- living-room.sofa_bed_linens [needs photo, P21]: Open the TV stand doors and send a photo showing the sofa-bed linens and pillow inside, then close the doors again.
- living-room.pillows [needs photo, P22]: Send a photo of the couch and armchairs showing throw pillows in place with none on the floor and the TV console doors closed.
- master-bedroom.bed_2 [needs photo, P24]: Send a photo of the made queen bed in the master bedroom with sheets and blanket pulled up and pillows in place.
- master-bedroom.no_belongings [needs photo, P24]: Send a photo of the master bedroom showing no guest belongings, trash, or laundry left in the room.
- master-bathroom.toilet [needs photo, P25]: Send a photo of the master bathroom toilet with the lid open showing the clean inside bowl, normal water level, and clean seat and rim.
- master-bathroom.toilet_paper [problem, P25]: Replace the toilet paper in the master bathroom with a new, full roll on the holder and send a new photo.
- master-bathroom.towels [needs photo, P26]: Send a photo of the at least 2 clean folded bath towels in the master bathroom.
- master-bathroom.sink [needs photo, P26]: Send a photo of the clean sink, counter and mirror in the master bathroom.
- master-bathroom.no_guest_items [needs photo, P27]: Please send a photo of the master bathroom counter and tub showing no guest toothbrushes toiletries or other items left.
- master-bathroom.shower [needs photo, P27]: Please re-clean the master bathroom shower and tub to remove hair and used bottles and send a new photo.
- hall-bathroom.toilet [needs photo, P28]: Send a photo of the hall bathroom toilet with the lid open showing the clean inside bowl, normal water level, and dry clean seat and rim.
- hall-bathroom.towels [needs photo, P29]: Send a photo of the at least 2 clean folded bath towels in the hall bathroom.
- hall-bathroom.shower [needs photo, P30]: Send a photo of the hall bathroom shower showing it is clean with no hair or used bottles.
- bedroom-1.beds_made [needs photo, P31]: Send a photo of the made bed in Bedroom 1 showing sheets and blanket pulled up with pillows in place.
- bedroom-1.no_belongings [needs photo, P31]: Send a photo of Bedroom 1 showing no guest belongings, trash or laundry left in the room.

## Tests — 276 passed (was 271)

- `test_api_failure_leaves_photo_unchecked_without_cards` — 401s →
  photo unchecked, no cards, `api_status` banner field, finish refused.
- `test_recheck_unchecked_retries_and_clears` — flip the fake to
  healthy, POST recheck, photo proves, banner clears, second call is a
  no-op.
- `test_end_round_skips_untaken_shots_and_finish_allows_them` —
  skipped shots, taken-shot cards stay, finish works after resolving.
- `test_unchecked_and_skipped_survive_reload` — persistence round-trip.
- `test_api_startup_line_messages` — OK / 401 / failure / crash lines.
- Page changes (`clean`/`setup`/`host`) verified with `node --check`
  (all pass); behavior verified in a browser by the user.
- Debugging note: the end-round test first caught two real gaps —
  items shared with a skipped shot made no card while still blocking
  signoff (now they card off their taken shots), and critical
  room-coverage items of unphotographed rooms blocked signoff (now
  excused by the early end).
