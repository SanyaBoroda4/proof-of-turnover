# Step 38 — Card first with Take photo, example pop-up, no double-tap zoom

## A. No double-tap zoom

- clean/setup/host: the `*` rule gained `touch-action: manipulation`
  and `-webkit-tap-highlight-color: transparent`, so rapid taps (setup
  steppers, Skip room, any button) press instead of zooming.
- host: the notes text input went 15px → 16px (iPhone zooms into
  smaller inputs).

## B. Round: card view + camera view (web/clean.html)

New `roundView` state (`"card"` default, in the render key; reset in
`resetRunState()`, `endRound()`, and on exit).

- Card view: an opaque `.rcard` layer above the still-playing camera
  (attachCamera/`#camLayer` untouched, so Take photo reveals a fresh
  live picture instantly). Header row (Exit, room, count) + progress
  bar, white `.shotcard` with the instruction (26/32, 22/28 when long),
  example photo, Repeat + mic pill buttons, test row, message pills,
  and a fixed `.takebar` with the retake thumbnail + big `#takePhoto`
  (demo keeps the picker via `cameraHtml`/`wireDemo`).
- Example pop-up: `#exPop` renders big (36svh + "Should look like"
  chip) with a 340 ms pop-in the first time per shot, then after
  2.5 s tucks to a 60 px thumbnail (card gains `tucked`, instruction
  keeps clear). `syncExample()` (run at the start of `renderMaybe()`
  and `render()`) tracks the shot, pops once (`popped`, never replayed
  by re-renders), and tucks via nested timers; `exState.big` is in the
  render key in card view only. Reduced motion disables both
  animations. Tap (big or small) opens the fullscreen example.
- Camera view: today's transparent `.round` restyled like the retake
  sheet — glass `.camtop` with `#camClose`, 20/25 instruction, example
  thumbnail; `#snapCam` bottom-center with pills above. No
  room/progress row, mic, or test links here.
- `#takePhoto` opens the camera view when live, else the file picker;
  `#camClose` returns to the card; `shutterShot()` flips to `"card"`
  right after the flash (and on file-picker fallback), so the next
  shot's card and example pop show at once while the upload continues.

Kept: persistent camera, instant capture + background upload, `#snapFile`
fallback, retake sheet, cards screen, and all listed ids
(`#snapCam #snapFile #demoPick #camView #camLayer #exitLink #skipShot
#finishEarly #micStart #micStop #retakeLast #repeat #exBtn`, plus new
`#takePhoto #camClose #exPop`). No new translations.

## Checks

- `node --check` on the page script: exit 0.
- `pytest tests`: 289 passed.
