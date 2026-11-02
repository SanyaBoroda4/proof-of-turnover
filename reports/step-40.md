# Step 40 — Overview first, trash + recycling, skip area, more example photos

## A. rental-1: 43-shot tour (tour.json; tour.v2.json keeps the 39-shot one)

- property.json: `kitchen.trash` is now one bin ("Trash bin emptied,
  new liner", min_count 1); new critical `kitchen.recycling` ("Recycling
  bin emptied, new liner", same sam_prompt). Checklist grows 82 → 83.
- New order starts every room with an overview: new powder-00 /
  master-bathroom-00 / hall-bathroom-00 doorway shots, laundry-04 /
  kitchen-10 / living-02 rewritten as overviews, new kitchen-13
  (recycling) after the reworded kitchen-11 (trash). New texts in
  en/es/ru in the shots' existing style (tú form; mirror warning in
  bathrooms). All other ids, items, and wording kept.
- `fixup_tour()` on the new tour returns no notes.

## B. Future houses

`tour_instructions()` gained the overview rule: each room starts with
one doorway overview shot (floor, tidiness, no guest items), then
details; no mirror appearances in bathrooms.

## C. "Skip area" (test mode only)

- New `POST /api/runs/{id}/skip-room {"room_id"}`: skips every shot of
  the room with no photo and not skipped yet (one `shot_skipped`
  timeline event per shot, same card/finish effect as skip-shot);
  returns the skipped shot ids; 400 for an unknown room.
- `#skipShot` (id kept) calls it with the current shot's room, so one
  tap jumps to the next area. Labels dropped "(test)": "Skip area" /
  "Finish photos now" (es "Saltar área" / "Terminar fotos ahora", ru
  "Пропустить зону" / "Закончить фото сейчас"); the host "Ended early"
  line too. Test/demo gating unchanged.
- Test: `test_skip_room_skips_every_untaken_shot_of_the_room` (400 on
  unknown room, whole-room skip, taken/already-skipped excluded, cards
  intact).

## D. Example photos (data/ only, uncommitted)

- New `scripts/apply_examples.py`: saves every map entry with a file
  via `save_example("rental-1", ...)`; entries with a fractional
  `crop` are EXIF-transposed, cropped, then saved. Writes only named
  shots, never deletes.
- 12 new examples (3 with crops to remove fingers/legs/a foot, all
  three verified clean): kitchen-05, kitchen-11, kitchen-13,
  laundry-02, laundry-03, patio-01, master-bedroom-01,
  master-bathroom-00, master-bathroom-01, hall-bathroom-00,
  hall-bathroom-01, hall-bathroom-04. `data/examples/rental-1` now
  holds 31 files.
- Still without an example (12): kitchen-01, kitchen-03, kitchen-04,
  kitchen-06, kitchen-12, laundry-01, laundry-04, living-01, porch-01,
  powder-00, powder-02, powder-03.

## Checks

- `pytest tests`: 290 passed (289 + skip-room; checklist count updated).
- `node --check` on the clean page script: exit 0.
