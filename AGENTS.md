Proof of Turnover checks vacation-rental cleanings. This text describes the whole product; build only what each task asks.

Setup, once per property: the host describes the house as a list of rooms (type, name, floor, number of beds, number of bath towels, etc.). The checklist is generated from the rooms using room templates, plus custom items. Optional later: a reference photo per room showing how it should look.

Each turnover: the cleaner takes her usual photos (10-60) with the app's camera page; the server records when each photo arrived, so old photos cannot be passed off as new. She can add voice notes, usually in Spanish (e.g. 'we're out of coffee pods', 'the bedroom lamp is broken'). The app matches every photo to a room, lists rooms with no photos, and checks every checklist item.

Proof rule: an item is 'proven' only when independent checks agree on one specific photo: (1) Muse Spark looks at the photos of that room, says the item is done and cites the photo id; (2) a second Muse Spark call looks only at that photo, without seeing the first answer, and agrees; (3) SAM 3.1 finds the object in that photo, and for countable items finds enough of them. Otherwise the item is 'needs proof' (no photo shows it, or the checks disagree) or 'problem' (a photo clearly shows it is not done). Missing evidence is never treated as done.

For each item without proof the cleaner gets one short request in her language, e.g. 'Please send a photo of the towels in the master bathroom'; she sends one more photo and only that item is re-checked. Voice notes are transcribed with Muse Voice Transcribe and turned into a restock list, damage reports and comments for the host in English; reported damage is matched to a photo that shows it, or the cleaner is asked for one. The host gets a ready / not-ready verdict for the next guest, with the proof photo for every item.

Memory: a local SQLite database keeps each property's history (turnovers, results, known issues with status open / accepted by host / fixed, host decisions), so the app never reports a known issue as new and never keeps asking about something the host already accepted.

Every AI model called at runtime must be a Meta model (muse-spark-1.3-contributor, sam-3.1, muse-voice-transcribe-1.0); plain code like OpenCV, Pillow or SQLite is fine. Runs locally on Windows. All API calls go through pot/meta_client.py (retries, recording, replay). Test data and samples must never show people, door codes, Wi-Fi passwords or personal documents.
