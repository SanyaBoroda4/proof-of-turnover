# Step 33 — In-page camera and Haint blue design

## Part A — In-page camera (web/clean.html)

- The round screen is camera-first: a full-bleed live viewfinder
  (`<video playsinline muted autoplay>`, `getUserMedia` with
  `facingMode: "environment"`, ideal 1920x1440). The shutter (#snapCam)
  captures the current frame to a canvas, encodes JPEG quality 0.9, and
  feeds the same `snapShot()`/`sendShot()` path as before, with the same
  30 s upload timeout and "Photo not sent – tap to send again" retry.
  One tap takes and sends the photo; a short white flash confirms it and
  the stream stays open for the next shot (the `<video>` element is
  rebuilt per shot but the `MediaStream` persists and is reattached).
- Card retakes ("Fixed — take new photo") open the same camera as a
  full-screen sheet (#sheet) showing the card's instruction and the
  shot's example photo. One tap captures, sends, closes the sheet, and
  the next card shows. The fixed card flies out (220 ms, skipped under
  `prefers-reduced-motion`) and the marsh toast pill confirms.
- Fallback: if the camera can't start (denied, not https, no
  `getUserMedia`), the shutter opens today's file input (#snapFile) via
  the unchanged `pendingShot`/`pendingCard` handler, and one line
  explains how to allow the camera (translated). No repeated permission
  prompts: once in file mode the page stops retrying until a new
  turnover. Demo mode (?demo=1) keeps the demo photo picker (#demoPick)
  and never touches the camera.
- Lifecycle: the stream stops when leaving the round (round → cards via
  screen tracking, Exit, new turnover), when the sheet closes, on the
  final screen, and when the page hides; it restarts when coming back to
  the round/sheet or when the page becomes visible again.

## Part B — Haint blue design

Tokens as specified (`--linen #F3F6F5`, `--ink #1B2A2C`,
`--haint #A8D5CF`, `--marsh #1F5E59`, `--brick #B5432F`,
`--dune #C9962E` on `#FBF3DF`, white cards), Onest 400/500/700 with
system-ui fallback, hero instruction 26/31 bold, cards/buttons 20 px
radius, round chips/shutter, one soft shadow only on floating things,
48 px+ targets, focus rings, `prefers-reduced-motion` respected, inline
SVG stroke icons only.

- Start: "Proof of Turnover" wordmark with house mark; "Start turnover"
  (marsh) and "Set up a new house" (white) tiles; language link bottom.
- Round: frosted top card (Exit icon, room name, "5 of 39", thin haint
  bar, hero instruction clamped to 3 lines); bottom bar with example
  thumbnail (tap = full-screen "How it should look"), 78 px shutter,
  Repeat + Record round buttons; last-photo thumbnail with check above
  the example (tap = retake last); test links as small text at bottom.
- Cards: "Photos done" + "N to fix" header; current card on a stack with
  two peeking shells; brick "Fix this" / dune "Missing photo" tag plus
  "2 of 4" counter; photo with red boxes and example inset labeled
  "Should look like"; 22 px instruction; full-width "Fixed — take new
  photo" with camera icon; "Can't fix" / "It's fine" row; "Later" text.
- Final: round stamp (haint ring, marsh check, 300 ms 1.15→1) with the
  translated done text, then stats and "New turnover".
- The middle-dot meta strings ("Room 1 of 13 · Photo 0 of 39") are gone;
  `T().progress` was removed and counts render as separate pieces.
- setup.html: same tokens/type/icons; room screens use a 3-up photo
  grid with an add tile (the #rphotos input is now label-triggered),
  rules box, checklist rows with "your rule" (marsh chip) / "from photo"
  chips, same button styles. "‹ Home" kept on every screen.
- host.html: layout untouched; status banner (incl. the brick API-failure
  banner), "Needs your decision" cards, before/after pairs, news strip,
  and item list restyled to the tokens. No JS changes.
- Every string stays translated es/en/ru; new keys: ofTotal, toFix,
  fixTag, missingTag, shouldLook, howShould, camHint, close, addPhoto.

Kept ids/data-attributes verified present: #snapCam, #snapFile,
#demoPick, #cardFixed, #cardCant, #cardFine, #cardLater, #cardConfirm,
#finish, #goSetup, #goTurnover, #prop, #start, #exitLink, #finishEarly,
#micStart, #micStop, #retakeLast, [data-lang], [data-reason], plus all
setup page ids and the other existing ids (#retryUnchecked,
#newTurnover, #repeat, #skipShot, #cardMic, #cardMicStop,
#demoPickCard, #snapDemoCard, #toChoice, #changeLang, #overlay, #big,
[data-retry], [data-full]). No API call changed.

## Part C — Small fixes

- C1 approve-overwrite: removed the `has_example` skip in the signoff
  approve path (pot/server.py), so the newest approved proving photo
  always becomes the shot's example. Test:
  `test_approve_overwrites_existing_examples_with_proving_photos`.
- C1 example review (looked at each photo; no people/reflections):
  - powder-03: confirmed wrong — Powder Vanity.jpeg shows a clean sink
    but the towel ring empty. No other powder photo shows a towel, so
    the shot now has no example (stored file deleted).
  - bedroom-1-01: confirmed wrong — Bedroom 1 table.jpeg is the kids'
    table with a globe. Replaced with "Bed room 1 carpet.jpeg" (made
    bed corner, clear carpet).
  - master-bathroom-03: confirmed wrong — Master Vanity.jpeg centers a
    folded washcloth with almost no sink. Replaced with
    "Master Vanity (2).jpeg" (clean sink, faucet, mirror, clear
    counter; mirror shows no person).
  - `data/inputs/examples_map.json` updated; rental-1 now serves 19
    examples (verified via `/api/runs/<id>/tour`: powder-03 has no
    `example_url`, the two replacements serve HTTP 200).
- C2: `progress.shots_taken` now counts skipped shots as done
  (pot/runs.py `status()`; the filtered skipped list is computed once
  and reused). Test: `test_progress_counts_skipped_shots_as_done`.
- C3: the per-noun pick refines in the parallel live path
  (pot/recheck.py) now run in a `ThreadPoolExecutor` instead of one
  after another; notes still merge in sorted key order so output is
  deterministic. Test: `test_parallel_problem_noun_picks_run_together`
  (peak concurrency 2 for one item × two nouns, 6 boxes kept).

## Checks

- `pytest tests`: 287 passed (284 before + 3 new), 26 s.
- `node --check` on the extracted scripts of clean.html, setup.html,
  host.html: all exit 0.
- Smoke: server on 127.0.0.1:8001 ("Meta API: OK"), /clean /setup /host
  all 200 with the new shell and Onest; fresh rental-1 run has 39 shots,
  19 with examples, progress 0/39.
- No chromium on PATH, so no headless DOM run; the camera, wake lock,
  and flash still need the host's real-phone check through the tunnel
  before filming.
