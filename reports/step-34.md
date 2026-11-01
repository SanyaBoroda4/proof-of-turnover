# Step 34 — Steady camera, full instructions, test links, clean card photos

Fixes from the reviewer's Chromium test of Step 33. Colors from Step 35
unchanged; no new features.

## 1. Steady camera, instant shutter (web/clean.html)

- One persistent `<video id="camView">` now lives in a fixed full-screen
  `#camLayer` outside `#app`, so re-renders never recreate it. The round
  screen (`.round`) and the retake sheet (`#sheet`) are transparent and
  show the layer through; both get a `.nocam` dark background in demo
  and file-fallback mode. The sheet reuses the same element (no more
  `#sheetVideo`). The layer hides whenever the stream stops.
- Shutter tap: the current frame is drawn to a canvas synchronously,
  the flash fires, and the page moves to the next shot and speaks its
  instruction immediately; the JPEG encode and upload run in the
  background through `snapShot(shot, file, quiet)` / `sendShot()`
  (quiet skips the second speak). New `encoding` optimistic state: it
  blocks `roundCurrent()` like `sending` but keeps failed shots
  re-tappable; polling treats it as busy.
- If the video has no frame yet at tap time, the page shows "Starting
  camera…" (`camStarting`, translated) and waits up to 1.5 s
  (`waitForVideo` poll), then takes the photo. The file picker opens
  only when the camera really failed (`camMode === "file"` or stream
  dead), never on a timing gap. The sheet has the same wait with its
  own pill (`#sheetWait`), guarded if the sheet closes mid-wait.
- Also fixed on the way: the sheet's fallback hint had no styling
  (`.novid` was scoped to `.round`); the selector now covers `#sheet`.

## 2. Full instructions

The 3-line clamp is gone: the top panel grows with the text, and
instructions longer than 90 characters render at 22/27 (`.long`)
instead of 26/31. Never truncated.

## 3. Test links in the top panel

"Skip (test)" / "Finish photos now (test)" moved from the bottom of the
screen into one `.testrow` row under the instruction. Nothing can
overlap the shutter anymore: worst-case panel (~265 px: header, bar,
5-line instruction, test row) plus bottom bar (~182 px) is ~447 px,
fitting 375x667 and 390x844 (verified by layout math; no browser here
to screenshot).

## 4. Caption-free card photos

- `save_evidence()` (pot/proof.py) now also saves
  `evidence/<item_id>.card.jpg`: same boxes/frame, no caption bar. The
  atomic temp-file save was factored into `_save_jpeg_atomic()`.
- `RunState.read_evidence(item_id, card=False)` serves the card copy
  for `?card=1`, falling back to the captioned photo when no card copy
  exists (old runs). The evidence route takes `card: bool = False`
  with the same `LONG_CACHE` headers.
- Phone cards and the sheet use `&card=1` (`cardPhotoUrl`); the host
  page keeps the captioned version. Send-back stale cleanup removes
  both copies, and `.card.jpg` files are excluded from the evidence id
  set on load.
- Tests: `test_evidence_card_copy_has_no_caption_bar` (card is exactly
  the 32x24 source with the right box color, proven + problem) and
  `test_evidence_card_param_serves_uncaptioned_copy` (200, JPEG,
  long cache, same width, shorter height, different bytes).

## 5. Final stamp text

"Ready for guests" (es "Listo para huéspedes", ru "Готово для гостей")
when nothing was reported or marked fine; otherwise "Sent to host"
(es "Enviado al anfitrión", ru "Отправлено хозяину"). Decided from
`finalCounts()` (`cantFix + fine > 0`).

## 6. Example caption

The round example thumbnail has a tiny "Example" caption underneath
(es "Ejemplo", ru "Пример"; `exampleCap`).

## Checks

- `pytest tests`: 289 passed (287 before + 2 new).
- `node --check` on the clean/setup/host scripts: all exit 0.
- Id audit: `#camView` exists exactly once (static shell, never
  rebuilt); no `sheetVideo`/`testlinks`/`line-clamp` remnants; all Step
  33 ids and API calls unchanged.
