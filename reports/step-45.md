# Step 45: no redraw loop on whole-room cards, camera safety net, small fixes

## What changed

- **Wide-camera render loop fixed (`web/clean.html`).** `syncWideCamera()` now remembers the shot it last handled and tries a wide/normal lens change at most once per shot. When widening turns out to be impossible — no track zoom below 1 and no ultra-wide camera, or an error — it remembers that for the whole session and never tries again. It calls `renderMaybe()` only when `camWide` actually changed, so a phone that cannot widen no longer rebuilds the card screen in a loop and “Take photo” stays tappable. The handled-shot memory resets whenever the camera stream restarts.
- **Camera safety net.** If switching cameras fails and the old stream is no longer live, `switchCamera()` restarts the normal camera with `startCamera()`; if that also fails, it sets `camMode = "file"`, so “Take photo” opens the file picker as before instead of leaving a dead camera.
- **No false API banner.** `RunState.record_call()` (and the reload path that rebuilds `api_down` from the call log) ignores `second opinion` calls entirely: a failed Muse Wonder opinion can no longer show the red “AI checks are failing” banner or clear a real failure.
- **One startup models fetch.** The server prints “Checking Meta API…” first, then fetches the model list once and derives both the “Meta API” line and the “Muse Wonder” line from it.
- **Deflaked prefetch test.** `test_prefetch_order_and_limit` no longer asserts the exact start order of the first three concurrent syntheses; it asserts that the first three texts are the ones that start, that at most 3 run at once, and that all 5 finish and cache.

## Tests

- New Node behaviour test (`tests/test_clean_wide.py`): with a fake track without zoom and a fake device list without an ultra-wide camera, repeated `syncWideCamera()` calls for one overview shot try the camera exactly once and call `renderMaybe()` at most once. Before the fix this test saw the camera tried 3 times in 3 calls.
- New tests for second opinions not changing `api_down`, and for the startup check fetching the model list exactly once.
- Full suite run three times in a row: **310 passed** each time. The cleaner page script passes `node --check`.
