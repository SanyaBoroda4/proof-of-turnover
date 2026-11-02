# Proof of Turnover

A verifiable AI agent that proves a vacation-rental cleaning is done, photo by photo.

<!-- VIDEO: link added Tuesday -->

## How it works

1. **The host sets up the house.** On `/setup`, the host answers a few questions about the house, adds 1-3 photos per room, and types or speaks the rules for each room. Muse Spark drafts the checklist from the photos and rules; the host edits it and saves. Saving writes the house plan and plans a guided photo tour: one photo per shot, room by room, each room starting with a wide overview shot.
2. **The cleaner walks the tour.** On `/clean`, in her language (Spanish, English, or Russian), each shot starts as an instruction card with an example photo of how it should look. “Take photo” opens the live in-page camera; one tap takes and sends the photo, and the next card appears. Instructions are read aloud, and the cleaner can leave voice notes.
3. **Every photo is checked in the background.** While the cleaner keeps walking, each photo is matched to its shot and checked (see the proof rule below). She never waits for the AI.
4. **Problem cards, one by one.** After the last photo, anything not proven becomes a card: “Fixed — take new photo”, “Can't fix” with a reason, “It's fine”, or “Later”. A retake re-checks only that item.
5. **The host decides.** On `/host`: “Needs your decision” (things the cleaner reported or disputes, with a Muse Wonder second opinion), “Fixed by cleaner” before/after photos, voice notes translated to English, the model-call log, and Approve or Send back.

## Why it is a verifiable agent

An item is **proven** only when three independent checks agree on one specific photo:

| Check | What happens |
|---|---|
| First look | Muse Spark looks at the room's photos, says whether the item is done, and cites the photo id. |
| Second look | A separate Muse Spark call sees only that one photo — never the first answer — and must agree. |
| SAM 3.1 | SAM must find the object in that photo; for countable items (towels, pillows, pods) it must find enough of them. |

If the checks disagree or no photo shows the item, it is “needs proof” and the cleaner gets one short re-photo request in her language. If a photo clearly shows it is not done, it is a “problem”. Missing evidence is never treated as done.

Every judgment leaves a trail:

- evidence photos with red boxes around what is wrong (muted-blue boxes for what is proven),
- a per-run call log (`calls.jsonl`) and timeline (`timeline.jsonl`) under `data/runs/<run-id>/`,
- record and replay of every model call: each successful call is cached under `data/model_cache/` keyed by a hash of the request, and with `POT_REPLAY=1` the app answers from that cache with no network and no key.

## Meta models

| Model | Used for |
|---|---|
| `muse-spark-1.3-contributor` | First look, second look, re-photo request text, problem-box picking, tour planning, setup checklist drafts |
| `muse-wonder-1.0` | Second opinion on cards the cleaner disputes or can't fix |
| `sam-3.1` | Object boxes and counts in photos |
| `muse-voice-transcribe-1.0` | Cleaner voice notes and spoken setup rules |

One non-Meta piece: reading text aloud uses Microsoft's neural voices through the `edge-tts` package (no key), with the phone's own voice as fallback.

## Try it without a key

The no-key replay of a demo house is being recorded; instructions land here before submission.

<!-- REPLAY: finished Tuesday -->

## Run it with your own key

You need Python 3.13 and a Meta Model API key.

### Windows (PowerShell)

```powershell
git clone <this-repo-url>
cd proof-of-turnover
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # then set MODEL_API_KEY in .env
.venv\Scripts\python -m pot.server
```

### macOS / Linux (bash)

```bash
git clone <this-repo-url>
cd proof-of-turnover
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # then set MODEL_API_KEY in .env
.venv/bin/python -m pot.server
```

The server prints “Meta API: OK” (or why the key was rejected), a host link, and a cleaner link, each with the access code (`POT_ACCESS_CODE` in `.env`, or a random one made at startup). Opening a link with `?code=...` once sets a cookie.

For the phone, keep the server on `127.0.0.1` and run:

```bash
cloudflared tunnel --url http://localhost:8000
```

Open `https://<tunnel-url>/host?code=<access-code>` on the laptop, then scan the QR code on the host page with the phone. The phone needs the HTTPS tunnel for the in-page camera, the microphone, and voice playback; a plain-LAN `http://` link only gives the phone's file picker, with no in-page camera or voice notes.

## Set up your own house

Open `/setup` (linked from the cleaner's start screen, “Set up a new house”). Answer the house questions, then for each room: add photos of the clean room, type or speak your rules, let the app draft the checklist (“Make checklist”), edit it if needed, and move on. “Save house” writes the house and plans its tour; “Start turnover” opens the cleaner page with the new house.

## Under the hood

- **Server:** one FastAPI app in `pot/server.py`; it serves three single-file pages from `web/` (`clean.html` for the cleaner, `setup.html` for house setup, `host.html` for the host) and a JSON API behind one access code.
- **`pot/` modules:**
  - `checklist.py` — house-plan loading and checklist generation
  - `config.py` — shared configuration (models, folders, languages)
  - `examples.py` — per-shot “how it should look” example photos
  - `inspector.py` — first look: one Muse Spark claim per item
  - `matcher.py` — match photos to rooms (batch path)
  - `meta_client.py` — the one Meta API client: retries, call recording, replay
  - `notes.py` — cleaner voice notes: transcribe and translate
  - `photos.py` — photo intake, EXIF time, check/overview copies
  - `prompts.py` — every instruction text sent to the models
  - `proof.py` — the proof rule, host verdict, evidence images
  - `prove.py` — re-run only the proof stage on an existing run
  - `recheck.py` — live per-photo checking (first look, second look, SAM)
  - `requests.py` — one short re-photo request per unproven item
  - `rooms.py` — checklist templates per room type
  - `runs.py` — live run state: proofs, cards, resolutions, status
  - `sam.py` — parse sam-3.1 output into boxes
  - `setup.py` — house setup: photos + rules → checklist and tour
  - `tour.py` — guided shot plans, one photo per shot
  - `turnover.py` — batch turnover CLI for a folder of photos
  - `verifier.py` — second look, SAM checks, problem-box picking
  - `voice.py` — Microsoft neural text-to-speech, cached on disk
- **Storage:** everything is local files. Each turnover is a folder under `data/runs/<run-id>/` with JSON state (`first_look.json`, `proof.json`, `requests.json`, `signoff.json`), `calls.jsonl`, `timeline.jsonl`, the photos, evidence images, and voice-note audio. Houses live in `properties/<id>/`; setups in `data/setups/`; model recordings in `data/model_cache/`.

## Privacy and safety

- The app runs locally and only ever sends the host's own house photos and voice notes to the models. In contributor mode, content sent to the Meta models may be used by Meta.
- Every page and API route sits behind one access code. Known limits today: that one code is shared by the host and cleaner pages (the cleaner link is a QR code), and the “Skip area” / “Finish photos now” links that exist for tests and demos are shown only with `?test=1` or `?demo=1` in the link.
- Test data and samples in this repository never show people, door codes, Wi-Fi passwords, or personal documents.

## Tests

```bash
python -m pytest -q
```

The suite (313 tests) uses a fake Meta client; live-API smoke and rehearsal scripts live in `scripts/`.

## Built with

Built with Muse Code on Muse Spark and Muse Wonder. License: MIT.
