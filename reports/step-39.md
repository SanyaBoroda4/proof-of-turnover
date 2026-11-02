# Step 39 — Smooth example pop-up, fewer rebuilds in the round

Only web/clean.html changed (plus this report).

## A. Round render key: rebuild only for what the views show

- The optimistic entry in the round key is now the ids of FAILED uploads
  only (the only optimistic state the round shows, via the retry pills),
  so encoding → sending → done transitions no longer rebuild the screen
  under the cleaner's finger or cut the example animations.
- Added `lastPhotoUrl()` (the `#retakeLast` thumbnail depends on it).
- Removed `rooms_done` / `rooms_total` (shown nowhere).
- In camera view the key also leaves out `shots_taken`, the last shot
  id, `lastPhotoUrl()`, `selectedDemo`, and the demo photo count, so a
  finishing upload never rebuilds the camera view mid-tap.

## B. Example animations survive a rebuild

- `exState` now carries `popAt` / `leaveAt` timestamps instead of the
  `popped` flag (reset in `syncExample()` and `resetRunState()`).
- First big-example render for a shot records `popAt`; any render
  within 340 ms replays class `pop` with a negative `animation-delay`
  equal to the elapsed time, so a rebuilt node continues the pop
  instead of restarting or cutting it.
- The 2.5 s timer records `leaveAt` when it adds `leave`; rebuilds
  during the tuck render `leave` with delay `-min(elapsed, 280)` and
  the forwards fill holds the shrunk state, so the photo never jumps
  back to big.
- Reduced motion unchanged (no animations).

## Checks

- `node --check` on the page script: exit 0.
- `pytest tests`: 289 passed.
