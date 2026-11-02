# Replay plan: the no-key judge path (Tuesday)

Goal: a judge clones the repo, starts the server with **no API key**,
and can still walk a complete demo turnover of `properties/demo-house`
(11 shots) on the phone or laptop, because every model answer was
recorded beforehand. Plan only — nothing here is built yet.

## 1. Demo photos

- Source: clean, guest-ready photos of a synthetic/demo house only
  (never rental-1, never a person or reflection). One photo per demo
  tour shot: master-bedroom-1, guest-bedroom-1, master-bathroom-1..3,
  hall-bathroom-1..3, kitchen-1, living-room-1, patio-1. Add one photo
  that deliberately fails one critical item (for example towels
  missing in one bathroom) so the replay shows a problem card, a
  retake, and a fix — a replay where everything passes proves nothing.
- Build the bundle with the existing tool:
  `python scripts/make_demo.py <photo folder> --out data/demo-build`
  (check-size, metadata-free JPEGs 01.jpg, 02.jpg, ... + manifest).
  The demo page's photo picker serves exactly these files, and demo
  uploads are kept byte-for-byte, so the recorded requests match.
- Ship `data/demo-build/` content in the submission bundle (it is
  gitignored today, so the bundle step must copy it explicitly).

## 2. Record the model cache

- With a real key, run the server **without** `POT_REPLAY`, with
  `POT_MODEL_CACHE_DIR` pointed at an empty staging folder, and drive
  one full scripted demo run (the demo photo picker, the same clicks a
  judge will make): all 11 shots, the planned problem card, one retake,
  one “It's fine” (so the Muse Wonder second-opinion call is recorded
  too), one voice note if the demo includes one, and Approve.
- Every successful call lands in the staging cache as one JSON per
  request hash (`pot/meta_client.py` records automatically; nothing
  new to write). Copy the staging folder to the bundle's
  `data/model_cache/` (or point `POT_MODEL_CACHE_DIR` at it in the
  instructions).
- Record on the final code: any change to prompts, schemas, photo
  sizes, or demo photos changes request hashes and silently turns the
  replay into cache misses. Freeze code first, record second.

## 3. Strip private text from the cache (review finding 13)

Cache JSON stores request **text** verbatim: room descriptions, host
rules, checklist requirements, voice-note transcripts (only image
bytes are hashed). Before shipping:

- Build the demo from `properties/demo-house` only, whose descriptions
  and rules are synthetic (“king bed, oak headboard…”), so the cache
  contains no real-house text by construction.
- Grep the staged cache for anything real anyway: the rental's name,
  street/city words, “Airbnb”, cleaner names, and any transcript text;
  delete or re-record any file that matches.
- Check the demo run folder the same way before it is copied anywhere;
  the submission needs the cache + demo photos, **not** a real run
  folder with real photos, audio, or notes.

## 4. Judge run configuration

Document these exact settings in the README's replay section:

```bash
POT_REPLAY=1            # no network calls, no key needed
POT_VOICE=device        # no Microsoft TTS either; the phone voice reads
POT_ACCESS_CODE=demo    # or leave unset for a random printed code
python -m pot.server
```

- In replay the server already skips the startup key/Wonder checks;
  verify it also prints no scary “key rejected” line.
- A cache miss in replay must look like the designed “couldn't be
  checked” state, not a crash — test one deliberately wrong photo to
  see that path.
- Example photos for the demo house: approve the recorded run once
  during recording (learning writes `data/examples/demo-house/`), or
  save the 11 shot examples with `pot.examples.save_example`; copy
  that folder into the bundle so the tour cards show “how it should
  look” offline too.

## 5. Clean-clone offline test (the acceptance gate)

On a second machine or a fresh clone, with Wi-Fi off (or the Meta and
Microsoft hosts blocked):

1. Follow the README replay instructions word for word (venv, install,
   env settings above, start server).
2. Open `/clean?demo=1`, start the demo house, send all 11 demo
   photos, wait for checks, open the cards.
3. Confirm: the planted problem card appears with red boxes; the
   retake fixes it; resolving one card shows the recorded Muse Wonder
   second opinion on `/host`; Approve works; the final screen shows
   “Ready for guests” / “Sent to host” correctly.
4. Confirm the server log shows no live API attempt (all cache hits)
   and no photo is left “couldn't be checked” except the deliberate
   wrong-photo check from step 4 of the configuration section.
5. Only then replace the README placeholder with the real replay
   instructions and remove the `<!-- REPLAY: finished Tuesday -->`
   comment.
