# Step 37 — Card buttons on screen, choice sheet, retake race fix, toast banner

Only web/clean.html changed (plus this report). All element ids kept.

## A. Retake race fix (cardSnap)

`cardSnap()` now takes `let settled = false;`, set to `true` first thing
in both the upload success and the failure branch. `remove()` (after the
220 ms fly-out) only writes `retakePhotos[itemId] = null` when nothing
settled yet, and only splices the card out of `cardOrder` when the key
is still present — so an instant success keeps its photo id (the card
settles with the "✓ fixed" toast via `reconcileCards()`) and an instant
failure leaves the card in place instead of hiding it forever.

## B. Card layout that fits a phone

- `cardHtml()` order is now tag row → instruction → photo → actions →
  Later (instruction above the photo).
- The "Fixed – new photo" button and the Can't fix / It's fine row are
  wrapped in `.cardactions`: sticky to the card bottom with a soft top
  shadow, so the buttons stay reachable while the photo scrolls.
- Card photos are capped at `max(200px, 100svh − 430px)` with
  `object-fit: contain` on linen — red boxes are never cropped, and
  tap-to-zoom still shows the full photo.
- Verified no ancestor of `.card` (`.stack`, `.wrap`, `#app`, `body`)
  sets `overflow`, so sticky works.

## C. Can't fix / It's fine open a bottom sheet

- The inline panel left `cardHtml()`; `renderCards()` now renders a
  `.pbackdrop` + `.psheet` dialog after the stack when `cardPanel`
  belongs to the shown card, with a title per action (new translated
  strings `whyCant` / `sureFine`; demo retake reuses `T().fixed`) and
  the unchanged `panelHtml()` body (voice note, reason chips, Confirm,
  demo picker — same ids, still wired by `wireCard()` inside `#app`).
- The sheet slides up once per open (`panelFresh`, set in the two
  handlers and the demo retake branch); note-recording re-renders don't
  replay it, and reduced-motion disables it.
- `#panelX` and backdrop taps close the sheet (panel/note state
  cleared); both are disabled while recording or transcribing.
- After a reason or Confirm the sheet closes and the next card slides
  in, as before.

## D. Toast as a top banner

`.toast` is now a full-width banner under the safe area (rounded 16 px,
centered, wrapping, pointer-transparent) instead of a centered pill, so
it no longer half-covers the "Photos done" title.

## Checks

- `node --check` on the page script: exit 0.
- `pytest tests`: 289 passed.
