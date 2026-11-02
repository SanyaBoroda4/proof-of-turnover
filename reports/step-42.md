# Step 42: background budget for Muse Wonder, voice fallback pause

## What changed

- Muse Wonder second opinions no longer run with `live_checks` in their call context. They keep the run directory and photo id for the call log, but now use the normal Meta client timeout and retry policy instead of the 25-second live-check budget. That gives the high-effort background review enough time to finish.
- Failed calls whose purpose starts with `second opinion` are ignored by `photo_had_api_failure()`, alongside the already-ignored box-pick failures. A failed background opinion can therefore never mark an already-judged photo as unchecked.
- When the neural voice fails in the cleaner page — the `/api/voice` fetch fails, times out, returns a non-200 answer, audio playback is rejected, or a prefetch fails — the page now pauses neural-voice attempts for 2 minutes and uses the phone voice immediately during that pause. It does not call `/api/voice` again for uncached text until the pause expires; a later success clears the pause. Audio already in the local cache can still play without a network call.

## Tests

- Added a server test proving a second-opinion call sees `run_dir` and `photo_id` in its call context but no `live_checks` budget flag.
- Extended the unchecked-photo test so a failed `second opinion ...` call does not count as a photo-check API failure, while a failed second look still does.
- Added a Node behaviour test for the cleaner page speech block: after a failed neural fetch, the next sentence uses the phone voice with no second `/api/voice` call; after 2 minutes the neural voice is tried again; and a rejected `play()` also starts the 2-minute phone-voice pause.
- Full suite: **302 passed**.
