# Overnight changes (branch `overnight`; merged into main in Step 49)

Base: `a1e2b3b` (Step 47a on main). No live API calls were made in
Part 2; every item has fake-client tests and the full suite ran after
each item (final count below). At the time of writing nothing here was on `main` yet (Step 49
below merged it), and `properties/rental-1`, `data/`, and the page
design are untouched.

## Speed analysis (item 0, report only)

Read-only analysis of the main folder's
`data/runs/run-20261101-*/timeline.jsonl` + `calls.jsonl` (live runs;
two runs are cache-fast recorded replays and are excluded):

| Run | Photos | Photo received → finished (median / p90 / max) | Last upload → all checked |
|---|---:|---:|---:|
| run-20261101-061750 | 11 | 38.7 s / 174 s / 316 s | 17.3 s |
| run-20261101-153858 | 38 | 16.7 s / 34.8 s / 75.9 s | 23.9 s |
| run-20261101-195116 | 43 | 28.2 s / 43.5 s / 98.7 s | cards were already ready per photo |
| run-20261101-223104 | 13 | 23.0 s / 92 s / 125 s | 92.4 s |

Where the time goes:

- A photo is “finished” when its slowest background call ends. The
  outliers are not the first look (~5–22 s) but retries that burn the
  full 25 s live budget twice: `pick problem boxes …` failed at
  48.9 s and 98.8 s, one second look took 69.5 s. Median live checks
  are ~17–39 s per photo while the cleaner walks.
- From the last photo to the cards, the wait is simply the last
  photos' remaining checks (up to ~90 s when a pick-box call retries
  to its timeout) plus one request-text call per failed item
  (~2–10 s each, written per photo, not batched at the end).
- Many calls were wasted on items the first look never cited for that
  photo (fixed by item 2 below).

Ideas, in order: (1) start second look/SAM only for cited items —
done in item 2; (2) give the pick-boxes call a shorter live budget or
skip it when the second look names one object and SAM already boxed
it (not done; prompt/proof-adjacent, needs a rehearsal); (3) batch
request-text writing at round end instead of per photo (not done;
changes when cards appear during the round).

## Commits

- `812e1d8` Overnight 1: SAM count must reach the minimum for proof — countable items with a sam_prompt now need SAM itself to find enough (review #3). Why: a “proven” could rest on the second look's count while SAM found too few. Risk: medium (proof outcomes change). Safe before filming? **No** — needs a live rehearsal first. Tests: updated proof tests (SAM 1 of 4 + second look 4 → needs proof) and the full-tour fake SAM.
- `cae106f` Overnight 2: verify only items the first look cited — **REVERTED by `d26615b` (Step 48a)** — second look and SAM start after the first look cites the photo (review #11). Why: fewer wasted calls, less 429/timeout pressure; independence unchanged (the second look never sees the first answer). Risk: medium (hot checking path; call order changes). Safe before filming? **No** — time it in a rehearsal first. Tests: new call-count test (5-item shot, 2 cited → 2 second looks, 2 SAM calls); Step 41 retry test now flakes only the first call, matching the new order.
- `f0aeb30` Overnight 3: separate host and cleaner access codes — `POT_HOST_CODE` (random when unset, printed in the host link) gates sign-off, setup, run admin, call log and note audio; the cleaner code covers the tour/photo/note/voice/status routes and is what the host page's QR carries (review #4). Risk: medium-high (every existing link changes meaning; the host code also works on cleaner routes so the host's own phone flow keeps working). Safe before filming? **No**. Tests: split-access tests (cleaner code gets 401 on host routes, cleaner-code endpoint only for the host). README updated in this commit.
- `9c016b8` Overnight 4: server enforces test-only skips — runs created from `?test=1`/`?demo=1` are marked test; skip-shot, skip-room and end-round return 400 for other runs (review #5). Risk: medium (test-mode filming flow must pass `test` at run creation, which the cleaner page now does). Safe before filming? **No** — Alex's filming uses Skip area in test mode; merge only with a phone check. Tests: non-test runs rejected on all three routes; existing skip tests create test runs.
- `e26d404` Overnight 5: approve only when ready, or forced after confirm — the sign-off route rejects approve unless the run is `awaiting_signoff` (400 naming open/checking counts); the host button shows that confirm and sends `force` (review #9). Risk: medium (host flow). Safe before filming? **No**. Tests: approve rejected while open, forced approve works; existing approve-early tests now pass `force: true`.
- `1a9673c` Overnight 6: cap photo uploads at 25 MB and bound image pixels — run and setup photo uploads stream/read with a 25 MB cap (413) and Pillow is capped at 60 MP (review #10). Risk: low (limits are far above phone photos). Safe before filming? **Yes**. Tests: oversized uploads return 413 on both routes.
- `09c463e` Overnight 7: surface tour fix-up notes in setup save and review — save/retry results carry `tour_notes`, shown on the review and done screens (review #6, display half). Risk: low. Safe before filming? **Yes**. Tests: save result carries the notes list; retry result shape updated.
- `1c99da8` Overnight 8: remove dead setup-effort constant, stale wording — deleted the unread `prompts.SETUP_EFFORT`, the unused `shutil` import, the rehearsal's stale “32-shot” wording, and labelled the smoke test a standalone diagnostic (review #19). Risk: very low. Safe before filming? **Yes**.
- This report is committed separately on top.

## Left undone (deliberately)

- Review #6's other half: fix-up still force-attaches omitted items
  to a room's last shot; notes are now visible, but re-planning or
  splitting instead is a tour change for after the deadline.
- The no-key replay bundle and cache sanitising (Tuesday's plan in
  `reports/replay-plan.md`), README/replay verification on a clean
  clone, and any Mac/Linux run-through.
- A committed browser/DOM test for camera, wide-lens, host and setup
  pages (review #15) — still manual-check territory.
- Cross-run known issues (accepted damage staying accepted): not
  built, as AGENTS.md now states.
- Per-room-type setup-draft defaults: the bench recommendation stays
  Spark minimal everywhere.
- Proof prompts, the pick-boxes live budget, and request-text
  batching: untouched, per the freeze advice in
  `reports/review-wonder.md`.


## Step 48 follow-ups (daytime, before merging)

- `d26615b` Step 48a: revert cited-only checks. Reason, from the
  Nov 1 live runs: starting second looks/SAM only after the first
  look makes each photo's check about 6-14 s slower (median), while
  the 10-call limit was almost never reached in walking runs (under
  1 s of queueing). Speed matters more than the saved calls, so the
  parallel start from main is back. Safe before filming? The branch
  is not merged before filming; on the branch it restores main's
  exact checking order.
- `16acea4` Step 48b: role-aware setup links, docs for two access
  codes. New `GET /api/role` answers host/cleaner from the presented
  code; the cleaner page shows the “Set up a new house” tile only
  for the host role (hidden when the role call fails); the host page
  links to `/setup` with the host code; `.env.example` documents
  `POT_HOST_CODE`; the README now describes the two codes, the
  server-enforced test/demo-only skips, setup from the host page,
  and the current test count. Tests: `/api/role` for both codes,
  cleaner code 401 vs host code 200 on `/setup`.
- `988a16e` Step 48c: close temp file on 413, Russian tour-notes
  heading. The photo-upload finally block closes the temp file
  before unlinking, so a 25 MB-cap rejection leaves no partial temp
  file on Windows (new test); Russian setup pages get their
  “??????? ? ????-????” heading instead of “undefined”.

## Step 49: merged into main, Overnight 2 re-applied

The overnight branch was fast-forwarded into `main` on Nov 2, and
the Step 48a revert was itself reverted (`32b25eb`), so the
cited-only checks from Overnight 2 (`cae106f`) are live on main
after all, at Alex's request: all results are revealed after the
last photo, so per-photo speed matters less than fewer calls, and
the verdicts are unchanged. The Step 48a slowdown note above stays
as the recorded trade-off.

Final state on main after the merge: full suite **320 passed**.
