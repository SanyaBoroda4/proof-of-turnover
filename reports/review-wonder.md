# Step 44: code review by Muse Wonder

Review base: commit `67dddf4` (after Step 43). Scope read: `pot/`, `web/`, `scripts/`, `tests/`, `properties/demo-house`, `properties/rental-1`, `README.md`, `AGENTS.md`, `requirements.txt`, and `.env.example`. No code was changed for this review. Findings are ranked by impact for Monday's iPhone filming, Tuesday's judge README/replay work, and Wednesday's deadline.

## Findings

### 1. Blocker — A judge cannot install and run the project from the README
- **Where:** `README.md:9-16`, `requirements.txt:1-15`
- **What is wrong:** The only start command assumes an existing Windows `.venv` (`.venv\Scripts\python -m pot.server`). A fresh clone has no `.venv` (it is gitignored), and the README never says which Python version to use, how to create the environment on Windows/macOS/Linux, how to install `requirements.txt`, how to copy `.env.example` to `.env`, or how to run the tests, setup page, demo, or replay. Several pinned packages are binary wheels, so the Python version matters.
- **Fix:** On Tuesday, rewrite the README with copy-paste setup for Windows, macOS, and Linux: supported Python version, venv creation, `pip install -r requirements.txt`, `.env` creation, own-key start, no-key replay start, demo property, and test command. Verify each path from a clean clone.
- **Effort:** M. **Safe before filming:** Yes (documentation only).

### 2. Blocker — The no-key judge replay is not yet runnable from the repository
- **Where:** `scripts/make_demo.py:8-9`, `pot/config.py:45-47`, `pot/meta_client.py:356-362`
- **What is wrong:** Replay answers exist only in `data/model_cache/`, and the demo photos live in `data/demo-build/`; `data/` is gitignored, so none of that ships. The bundle script itself says the recorded cache “gets added on Tuesday”. With `POT_REPLAY=1` today, the first unrecorded request raises a cache miss, which the app correctly treats as an AI failure and marks photos unchecked. Example photos for the tour are also local-only under `data/`.
- **Fix:** Finish the Tuesday replay bundle deliberately: demo photos in the exact byte form the server will ingest, the matching model cache, example photos if the demo should show them, `POT_REPLAY=1`/`POT_VOICE=device` instructions, and a clean-machine test that completes a full demo run with networking disabled.
- **Effort:** M. **Safe before filming:** No — build and test it Tuesday, not between rehearsals.

### 3. Blocker — A countable item can be “proven” when SAM found too few
- **Where:** `pot/proof.py:110-125`
- **What is wrong:** For a countable item, proof uses `max(sam.count, second.count_seen)`. If SAM finds 1 of at least 4 towels but the second look says it sees 5, the item is proven even though SAM did not find enough. That contradicts the proof rule in `AGENTS.md`: SAM must find enough of them on the same photo.
- **Fix:** When `sam_prompt` is set, require `sam.count >= min_count` for proof; use the second look's count only for items with no SAM prompt. Add a proof test with SAM below the minimum and the second look above it.
- **Effort:** S. **Safe before filming:** No — change it only with a full rehearsal afterwards, because it can change which demo items are proven.

### 4. High — One shared access code gives the cleaner host powers
- **Where:** `pot/server.py:486-503`, `pot/server.py:688-719`, `pot/server.py:942-948`
- **What is wrong:** The same `?code=` authenticates every page and API. Anyone with the cleaner link — which is printed as a QR code and stays in the phone's browser history — can call host-only actions directly, including approving a turnover, sending items back, creating setups, and deleting setup photos. The proof flow can therefore be bypassed by the person being checked.
- **Fix:** Split the codes: a cleaner code for run/photo/note routes and a separate host code for sign-off, setup, and run administration. Keep old links working only until filming is done.
- **Effort:** M. **Safe before filming:** No — link and QR changes are too risky the day before filming.

### 5. High — Test/demo skipping is only hidden in the page, not enforced by the server
- **Where:** `pot/server.py:756-780`, `pot/runs.py:561-606`
- **What is wrong:** `POST /end-round`, `/skip-shot`, and `/skip-room` work on any run for anyone with the shared code, at any time. They are meant to exist only behind `?test=1`/`?demo=1`, but that flag lives only in `web/clean.html`. Skipped-only items are then excused by `/finish`, so a real turnover can be finished with whole rooms unphotographed without using the test UI at all.
- **Fix:** Mark runs as test/demo at creation (the cleaner page already knows the flag) and have the three routes reject non-test runs with 400. Keep the host's “shots skipped” display for test runs.
- **Effort:** M. **Safe before filming:** No — Alex uses Skip area in test mode while filming; gate it after Monday.

### 6. High — Tour fix-up silently crowds omitted items onto a room's last shot
- **Where:** `pot/tour.py:186-192`, `pot/setup.py:764-774`
- **What is wrong:** If the tour planner omits checklist items, `fixup_tour()` attaches all of them to the last shot of the room without asking the model again. Setup save then discards the fix-up notes, so the host never learns that several items were squeezed into one photo — exactly the crowded, multi-angle shot the one-photo-per-shot rule forbids. New houses planned by judges can get unusable shots with no warning.
- **Fix:** Surface fix-up notes in the save result and setup review screen; better, re-plan or split shots when items were force-attached instead of silently accepting a crowded shot.
- **Effort:** M. **Safe before filming:** No (rental-1's saved tour is unaffected, but tour code is proof-adjacent).

### 7. High — The same-Wi-Fi instructions cannot give the iPhone the in-page camera or microphone
- **Where:** `README.md:20-26`, `web/clean.html:933-955`
- **What is wrong:** The README presents the LAN `http://<pc-ip>:8000` link as a normal way to run the tour. On iPhone Safari, `getUserMedia` and `MediaRecorder` require a secure context, so on plain HTTP the in-page camera falls back to the file picker and voice notes cannot record. Only the tunnel paragraph mentions HTTPS, and only as a microphone requirement.
- **Fix:** State plainly in the README and startup output: use the Cloudflare HTTPS tunnel for the real phone tour (camera, microphone, and neural voice playback); the LAN HTTP link is only a fallback with the system camera and no voice notes.
- **Effort:** S. **Safe before filming:** Yes (documentation/startup text only).

### 8. Medium — Server startup can wait ~30 s before serving, and checks the model list twice
- **Where:** `pot/server.py:1080-1085`, `pot/server.py:130-145`
- **What is wrong:** Startup calls `list_models()` once for the Meta line and again inside `wonder_startup_line()`, each with a 15 s timeout, before uvicorn starts. On a bad network the host sees a silent terminal for up to half a minute and may start a second server on another port.
- **Fix:** Fetch the model list once and pass the ids to both startup lines; print a “checking…” line first.
- **Effort:** S. **Safe before filming:** Yes, with the existing startup-line tests plus one shared-list test.

### 9. Medium — The host can approve a turnover that is still being checked or still open
- **Where:** `pot/server.py:688-702`, `web/host.html:306-312`, `web/host.html:327-335`
- **What is wrong:** “Approve turnover” is always enabled and the sign-off route does not check `agent_state`. A misclick — or a click while photos are still checking — marks the run done and learns example photos from a half-finished turnover. The open items are recorded in the sign-off, but the banner says “Approved” all the same.
- **Fix:** Disable the button (and reject in the route) until the run is `awaiting_signoff`, or require an explicit confirm that names the open/checking counts.
- **Effort:** S. **Safe before filming:** No — sign-off behaviour is part of the filmed flow; change it after Monday.

### 10. Medium — Photo and setup uploads have no size limit before the image is decoded
- **Where:** `pot/server.py:610-618`, `pot/server.py:943-948`, `pot/photos.py:143-155`
- **What is wrong:** Run photos are streamed to a temp file with no byte cap, and setup photos are read fully into memory. Pillow then decodes whatever arrived; a huge or decompression-bomb image from anyone with the code can fill the disk or memory of the host PC. Voice notes are capped, photos are not.
- **Fix:** Stream with a hard cap (for example 25 MB) and reject over it with 413; set `Image.MAX_IMAGE_PIXELS` to a sane bound for a phone photo.
- **Effort:** S. **Safe before filming:** Yes, if the cap is generous and covered by one upload test.

### 11. Medium — Every photo starts second looks and SAM for all its items before the first look answers
- **Where:** `pot/recheck.py:104-120`, `pot/server.py:335`, `pot/meta_client.py:212-214`
- **What is wrong:** The parallel path launches a second look and a SAM call for every item in the shot immediately, including items the first look will call `not_shown`. With 8 photo workers sharing 10 global Meta slots, a walking cleaner who shoots several rooms quickly creates a large queue of partly wasted calls, which is how 429s and 25 s live-timeouts (and therefore “couldn't be checked” retries) happen under load.
- **Fix:** Start the second look and SAM when the first look has cited the photo for that item. The second look still never sees the first answer, so independence is unchanged; only the start time moves.
- **Effort:** M. **Safe before filming:** No — this is the hot checking path; change it only with a timed rehearsal.

### 12. Medium — The faster setup draft default was chosen from one porch room only
- **Where:** `pot/config.py:23-24`, `scripts/bench_setup_draft.py:14-25`
- **What is wrong:** `SETUP_EFFORT=minimal` is now the global default for every room type, based on a single front-porch benchmark. Bedrooms and bathrooms depend on sensible `sam_prompt`/`min_count` output (towels, beds, pods); minimal effort was never measured there. Setup is the first thing a judge with a new house tries.
- **Fix:** Extend the bench to one bedroom and one bathroom with countable rules, and pick the default per room type if minimal under-specifies counts or SAM prompts.
- **Effort:** M. **Safe before filming:** Yes (benchmark only; change the default after filming if it moves).

### 13. Medium — The recorded model cache can leak private text if the replay bundle is shared as-is
- **Where:** `pot/meta_client.py:403`, `pot/config.py:46`
- **What is wrong:** Cache records store the full request text: room descriptions, host rules, checklist requirements, and voice-note transcripts are saved verbatim (only image bytes are hashed). A public judge bundle built from real rental-1 recordings would publish that text, and `scripts/make_demo.py` does not sanitize it.
- **Fix:** Build the replay bundle only from the synthetic demo house, or add a cache sanitizer/review step before anything is shared; state in the README what the bundle contains.
- **Effort:** S. **Safe before filming:** Yes (bundle hygiene for Tuesday).

### 14. Medium — Cross-platform operation is asserted nowhere and tested nowhere
- **Where:** `requirements.txt:1-15`, `README.md:9-11`
- **What is wrong:** The code is mostly `pathlib`-based, but every documented command is Windows PowerShell, and there is no CI or recorded test run on macOS/Linux. Binary pins (`opencv-python-headless`, `pillow-heif`, `imageio-ffmpeg`) are the usual failure point, followed by `.env` discovery (`load_dotenv()` reads the working directory's `.env`, `pot/config.py:12`).
- **Fix:** On Tuesday, run the clean-clone README verbatim on one Mac or Linux machine (or a container) and fix what breaks; note the supported Python version in the README.
- **Effort:** M. **Safe before filming:** Yes (verification work, no product change).

### 15. Medium — There is no committed browser test for the camera, wide-lens, host, or setup flows
- **Where:** `tests/test_clean_voice.py:97-105`, `tests/fakes.py:205-211`
- **What is wrong:** The suite is strong on server logic with a fake Meta client, but the only page test extracts the speech functions into Node. The Step 43 wide-camera switching, the retake sheet layering, card layout on a short iPhone viewport, and the host/setup pages have no automated DOM coverage, so a filming-day regression can only be caught by hand.
- **Fix:** After filming, add one small headless-browser smoke (fake camera + fake Meta server) covering: card view → Take photo → shutter → next card, retake sheet shows the video, and the cards fit at 390×664. Until then, run the manual checklist before Monday.
- **Effort:** M. **Safe before filming:** Yes (tests only), but do not let it delay the manual rehearsal.

### 16. Medium — AGENTS.md and the README describe a product that no longer matches the code
- **Where:** `AGENTS.md:5-13`, `README.md:31-32`, `scripts/smoke_test.py:24-35`
- **What is wrong:** The README still says tour instructions use “the browser's built-in voice”, although the neural Microsoft voice is primary since Step 41. AGENTS.md still calls the per-room reference photo “optional later” (examples are implemented), describes a SQLite known-issues memory that does not exist in the code (runs are JSON folders; nothing carries accepted issues into the next turnover), and says all API calls go through `pot/meta_client.py`, while `scripts/smoke_test.py` calls the OpenAI SDK directly with its own retry loop.
- **Fix:** Update both documents on Tuesday: neural voice first with phone fallback, examples as built, memory as JSON run history with cross-run known issues listed as not-yet-built, and either route the smoke test through MetaClient or mark it as a standalone diagnostic.
- **Effort:** S. **Safe before filming:** Yes (documentation only).

### 17. Low — A failed Muse Wonder opinion can still raise the host's red “AI checks are failing” banner
- **Where:** `pot/runs.py:723-730`, `pot/server.py:181-215`, `web/host.html:296`
- **What is wrong:** Step 42 stopped second opinions from marking photos unchecked, but `record_call()` still sets `api_down` for any failed call, including a background Wonder call. A Wonder timeout can show “AI checks are failing” on the host page even though every photo check is healthy; the next cache hit clears it again, so the banner can flicker.
- **Fix:** Ignore `second opinion` purposes when updating `api_down`, the same way `photo_had_api_failure()` already ignores them.
- **Effort:** S. **Safe before filming:** Yes, with one status test.

### 18. Low — Old-photo protection depends entirely on EXIF, which is easy to strip
- **Where:** `pot/runs.py:337-343`, `pot/photos.py:105-112`
- **What is wrong:** A photo is refused as old only when a readable EXIF camera time predates the run. Any stripped or edited image — including every rehearsal photo after `scripts/rehearsal.py` removes metadata — is accepted at face value. The in-page camera's canvas JPEGs have no EXIF either, so the check cannot distinguish a fresh capture from an old file uploaded through the fallback picker.
- **Fix:** Accept the limitation for the deadline (the server still records arrival time); later, prefer in-page captures and treat file-picker uploads without EXIF as “unverified time” on the host page.
- **Effort:** M. **Safe before filming:** No — the filmed flow uses the fallback picker in rehearsals.

### 19. Low — Dead and duplicated code invites wrong edits
- **Where:** `pot/prompts.py:23`, `pot/setup.py:622-623`, `scripts/smoke_test.py:45-119`, `scripts/rehearsal.py:1-13`
- **What is wrong:** `prompts.SETUP_EFFORT` is no longer read anywhere (setup uses `config.SETUP_EFFORT`), the smoke test duplicates MetaClient's retry policy instead of using it, and the rehearsal script still describes a “32-shot” round although the tour has 43 shots. Each is small, but together they make the next editor trust stale facts.
- **Fix:** Delete `prompts.SETUP_EFFORT`, make the smoke test call MetaClient (or label it diagnostic-only), and fix the rehearsal wording.
- **Effort:** S. **Safe before filming:** Yes for wording; delete code only after Monday.

## Do not touch before the deadline

- The proof rule and `pot/proof.py` counting (Finding 3): fix it only when a full live rehearsal can be re-run immediately afterwards.
- The checking pipeline's parallelism and live timeout/retry budgets (Finding 11): any change here can trade today's known timing for unknown stalls on camera.
- Access-code splitting and test-skip gating (Findings 4–5): the filmed links, QR codes, and Skip-area flow all depend on the current behaviour.
- Tour planning and fix-up (Finding 6): rental-1's saved 43-shot tour is rehearsed; do not regenerate or re-plan it before filming.
- Sign-off guarding (Finding 9) and old-photo handling (Finding 18): both sit in the filmed host/cleaner flow; brief the host instead (“approve only when the banner says ready to review”).
- The wide-camera and voice code from Step 43: it is untested by automated browser tests (Finding 15), so freeze it and rely on the manual iPhone check, not on last-minute refactors.
