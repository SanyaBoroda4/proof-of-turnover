# Step 43: voice in sync, listing examples, problem photo visible, wide camera, faster setup draft

## What changed

### Voice: one natural voice, instant, in sync
- New `POST /api/voice/prefetch`: accepts up to 80 texts (1–500 chars each) behind the access code, returns immediately, and synthesizes missing clips in the background in the given order, at most 3 at a time.
- Voice synthesis is now deduplicated in `pot/voice.py`: if a text is already being synthesized, an on-demand `POST /api/voice` waits for that same result. If its text is still queued behind prefetches, it is promoted and synthesized right away instead of waiting behind the queue.
- When a tour loads, the phone sends all remaining shot lines to the prefetch endpoint, current shot first. When the cards screen starts and whenever the card set changes, it sends all card texts plus “Photos done” and “All done”. The existing next-2 client-side clip cache stays.
- The 2-minute neural pause from Step 42 is gone. Every line tries the neural voice first with an 8-second fetch timeout; a failure uses the phone voice for that one line only. After 3 consecutive 503 answers the phone voice is used for the rest of the session. A failed prefetch never changes the voice. The phone fallback prefers a British English female voice (Kate, Serena, Martha, Stephanie), then en-GB, then en-US.
- Speech is generation-based: a new shot or card instruction stops the current neural clip and phone speech, clears the queue, and speaks the new text. “Photos done” and the first card are queued together, with the first interrupting the round voice. Skip area, the shutter, resolving a card, and Later all stop the old voice immediately.
- Repeat is fixed: handlers are detached before the audio element is reset, stale-generation callbacks are ignored, and Repeat replays the current line from its cached clip from the start, exactly once, with no new fetch.

### Example photos from the Airbnb listing
- Added `scripts/fetch_listing_photos.py`, which downloads the 13 named photos from Alex's own Airbnb listing into `data/photos/Airbnb/`.
- Updated `data/inputs/examples_map.json` for porch-01, foyer-01, laundry-04, kitchen-10, kitchen-01, living-02, dining-01, master-bedroom-01, bedroom-1-01, bedroom-2-01, master-bathroom-00, hall-bathroom-00, and hall-bathroom-04 (with the specified crops for porch-01 and kitchen-01), then ran the fetch and `scripts/apply_examples.py`.
- Example files now present: **34**. Shots still without an example: powder-00, powder-02, powder-03, laundry-01, kitchen-03, kitchen-04, kitchen-06, kitchen-12, living-01.
- Approving a turnover now learns an example only for shots that have none yet; it never overwrites an existing example.
- The big example pop-up now uses `object-fit: contain`, so whole-room photos are not cropped.

### Problem photo never hidden
- The “Should look like” inset no longer sits on top of the card photo. It is now its own row under the photo: a 64 px thumbnail plus the label; tapping it opens the full example. The problem photo and its red boxes stay fully visible, and the photo's max-height was reduced by the row's height so cards still fit the screen.

### Wider camera for whole-room photos
- The live camera preview (round and retake sheet) now uses `object-fit: contain` on black, so the cleaner sees the whole frame that will be saved.
- The 13 overview shots in `properties/rental-1/tour.json` are marked `"overview": true`; `fixup_tour()` keeps a boolean `overview`, the tour schema accepts it as optional, and the tour planner rule says to set it on each room's first shot. The rental tour still fixes up with no notes.
- For an overview shot the page widens the lens while the card is already showing: track zoom to its minimum below 1 when offered, otherwise it switches to an ultra-wide back camera (new stream first, old stream stopped only after the new one plays). Detail shots return to zoom 1 / the normal camera. Errors keep or restore the normal camera. A small “Wide” pill shows in the camera view while wide is on, and `&wide=0` turns the feature off.

### Faster “Make checklist”
`scripts/bench_setup_draft.py` drafted the front-porch room of setup-20261101-222836 (5 host rules, 1 photo) live with a throwaway cache:

| Model | Effort | Seconds | Rule items | Missing host rules | sam_prompts |
|---|---|---:|---:|---|---|
| muse-spark-1.3-contributor | medium | 60.4 | 5 | none | area_tidy=-, front_door=-, shoes=-, couch=-, concrete=- |
| muse-spark-1.3-contributor | low | 21.1 | 5 | none | porch_clean=-, door=-, shoes_floor=-, couch=pillow, concrete=- |
| muse-spark-1.3-contributor | minimal | 11.4 | 5 | none | porch_clean=-, front_door_clean=-, no_shoes_floor=-, loveseat_pillows_only=pillow, concrete_clean=- |
| muse-wonder-1.0 | low | 13.7 | 5 | none | porch_clean=-, door_clean=-, no_shoes_floor=-, couch_only_pillows=pillow, no_dirt_concrete=- |
| muse-wonder-1.0 | minimal | 6.3 | 4 | porch clean merged into the concrete item | front_door_clean=front door, no_shoes_on_porch_floor=-, couch_only_pillows=pillow, concrete_no_dirt=- |

Choice: **Muse Spark at minimal effort** (`SETUP_MODEL` defaults to `REASONING_MODEL`, `SETUP_EFFORT` defaults to `minimal`). It is the fastest combination that keeps all five host rules as separate items with sensible sam_prompts (area/absence checks have none; the pillows object rule has `pillow`). Muse Wonder minimal was faster but merged two host rules into one item, so it was not eligible; no eligible combination reached 8 s. While drafting, the setup button now reads “Making checklist… N s” with the seconds counting up.

### End of round in test mode
- On “Photos done”, when shots were skipped, the cards screen shows a small localized line such as “2 photos taken · 31 skipped (test)”.

## Tests

- New coverage: voice prefetch dedupe/on-demand join and order/3-at-a-time limit, the prefetch endpoint (limits and validation), approve never overwriting an example, `fixup_tour` keeping `overview`, the setup draft model/effort settings, and the updated cleaner-voice behaviour test (per-line neural retry, 3×503 phone-only, cached Repeat).
- Full suite: **307 passed**. Page scripts pass `node --check`.
- Nothing under `data/` is committed; the listing photos, example files, and voice cache stay local.
