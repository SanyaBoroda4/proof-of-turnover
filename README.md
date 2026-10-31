# Proof of Turnover

Proof of Turnover checks vacation-rental cleanings from the cleaner's photos and voice notes: every checklist item generated from the house plan is only marked proven when independent Meta-model checks agree on one specific photo, anything unproven becomes a re-photo request in the cleaner's language, and the host gets a ready / not-ready verdict for the next guest.

## Web app

Start the server from the project folder:

```
.venv\Scripts\python -m pot.server
```

It prints a host link and a cleaner link, each with the access code. Every
page and API route needs that code: `POT_ACCESS_CODE` in `.env`, or a random
one made at startup when it is not set. Opening a link with `?code=...`
once sets a cookie.

Open the cleaner's tour on the phone in one of two ways:

- Same Wi-Fi: run with `.venv\Scripts\python -m pot.server --host 0.0.0.0`,
  accept the Windows Firewall prompt (or allow inbound TCP on port 8000),
  and open `http://<pc-ip>:8000/clean?code=...` on the phone.
- Any network: keep the server on `127.0.0.1` and run
  `cloudflared tunnel --url http://localhost:8000`, then open the
  `https://...` link it prints, plus `/clean?code=...`. The tunnel gives
  https, which the microphone in the voice step will need.

The host page (`/host`) shows the verdict, rooms with evidence photos, the
model log, sign-off buttons, and a QR code of the cleaner link.

The tour instructions are read aloud with the browser's built-in voice
(`speechSynthesis`), which is not an AI model call.
