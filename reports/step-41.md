# Step 41: no false unchecked, upload retry, natural voice, Muse Wonder second opinion

## What changed

### Checks and uploads
- Failed `pick problem boxes` / `pick problem area` calls no longer make a photo count as unchecked. Those calls already fall back to keeping all candidate boxes, so the photo was still judged.
- When a real API failure is detected after a photo check, the server now waits 3 seconds and runs the photo checks once more automatically (timeline event `checks_retry`). The photo is marked unchecked only if the second attempt also has an API failure.
- Cleaner photo uploads and card retake uploads retry automatically up to two more times, after 2 seconds and 5 seconds. Each attempt keeps the existing 30-second timeout. A server refusal for an old photo (`foto antigua`) is not retried. The photo stays in the sending state until the final attempt fails.

### Natural voice
- Added `pot/voice.py` using Microsoft neural voices through `edge-tts==7.3.1`: British English (`en-GB-SoniaNeural`), Mexican Spanish (`es-MX-DaliaNeural`), and Russian (`ru-RU-SvetlanaNeural`). No Microsoft key is required.
- Synthesis runs in a worker thread with its own event loop and an 8-second timeout. Results are cached as MP3 files under `data/voice_cache/`; `POT_VOICE=device` disables the neural voice.
- Added `POST /api/voice` behind the normal access code. It accepts text up to 500 characters and returns `audio/mpeg`, or 503 when neural speech is off or fails.
- The cleaner page tries the neural voice first, caches audio by spoken text, prefetches the next two round instructions and the next two card texts, unlocks audio on the first tap for iOS, and falls back to the phone voice for any text that cannot be fetched or played.

### Muse Wonder second opinion
- Added `WONDER_MODEL` and `WONDER_EFFORT` settings, defaulting to `muse-wonder-1.0` and `high`.
- `MetaClient.respond()` now accepts an optional model override while keeping the existing default model behavior unchanged.
- Resolving a card as “It’s fine”, or as “Can’t fix” for broken / already like that / won’t clean / other reasons, starts a background Muse Wonder review. “Can’t fix” for supplies does not start one.
- The review receives the card photo, item requirement, first- and second-look reasons, cleaner answer, and any English voice-note text. It stores a pending result immediately, then a done or failed result, without blocking the cleaner or changing proof status.
- The host decision cards show a “Second opinion · Muse Wonder” box for pending and completed opinions; failed opinions are hidden.

## Tests

- Full suite: **300 passed**.
- New coverage includes ignored pick failures, one automatic check retry after an API failure, voice synthesis caching and off behavior, `/api/voice` validation and MP3 responses, the Muse Wonder startup check, second-opinion eligibility, the `muse-wonder-1.0` model used for the call, and its result appearing in run status.

## Server start check

The server was started briefly on this PC. Its startup lines were:

```text
Meta API: OK
Muse Wonder: OK
Voice: Microsoft neural voices (edge-tts)
```

A live `POST /api/voice` request also returned HTTP 200 with `audio/mpeg` (14,688 bytes), and the MP3 was written to the local voice cache. Nothing under `data/` is included in the commit.
