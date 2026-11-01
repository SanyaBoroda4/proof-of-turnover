"""Cleaner voice notes: record, transcribe, translate for the host.

The phone posts browser audio (webm/opus from Chrome, mp4/aac from
Safari); the server converts it to 16 kHz mono WAV with the bundled
ffmpeg, transcribes it with muse-voice-transcribe-1.0 (language bias
= the run's language), and translates it to English with one Muse
Spark call. Audio stays in the run folder; temp files are deleted.
"""

import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

from pot import prompts
from pot.meta_client import MetaClientError

NOTE_CATEGORIES = ("damage", "missing", "supplies", "other")

#: Largest accepted upload (a 60 s note is well under 1 MB).
MAX_NOTE_BYTES = 10 * 1024 * 1024


class NotesError(Exception):
    """A voice note was rejected; the message is safe for a 400."""


def note_schema():
    return {
        "type": "object",
        "properties": {
            "translation": {"type": "string"},
            "title": {"type": "string"},
            "category": {"type": "string", "enum": list(NOTE_CATEGORIES)},
        },
        "required": ["translation", "title", "category"],
        "additionalProperties": False,
    }


def convert_to_wav(src_path, dst_path):
    """Convert browser audio to 16 kHz mono WAV with the bundled ffmpeg."""
    try:
        proc = subprocess.run(
            [get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(src_path),
             "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(dst_path)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NotesError(f"Could not convert the audio ({exc})") from exc
    if proc.returncode != 0 or not Path(dst_path).is_file():
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1][:200] if detail else "ffmpeg failed"
        raise NotesError(f"Could not convert the audio ({tail})")
    return Path(dst_path)


def transcript_text(result):
    """The spoken text from a transcribe answer ("" when nothing heard)."""
    if not isinstance(result, dict):
        return ""
    text = (result.get("transcript") or "").strip()
    if text:
        return text
    turns = result.get("turns") or []
    bits = [
        turn.get("text", "").strip() for turn in turns
        if isinstance(turn, dict) and (turn.get("text") or "").strip()
    ]
    return " ".join(bits)


def transcribe_note(client, wav_path, language, note_id):
    """Transcribe one note; returns "" when no speech was heard."""
    result = client.transcribe(
        f"transcribe voice note {note_id}", wav_path, [language]
    )
    return transcript_text(result)


def translate_note(client, text, language, room_name, note_id, effort=None):
    """One Muse Spark call: English translation, title, category.

    Returns (record_fields, notes); a failed call falls back to the
    original text so the host still sees the note.
    """
    effort = prompts.RECHECK_EFFORT if effort is None else effort
    try:
        answer = client.respond(
            f"translate voice note {note_id}",
            [{"type": "input_text", "text": text}],
            instructions=prompts.note_instructions(text, language, room_name),
            schema=note_schema(),
            effort=effort,
        )
    except MetaClientError as exc:
        return _translation_fallback(text), [f"Voice note {note_id}: {exc}"]
    if not isinstance(answer, dict):
        return _translation_fallback(text), [
            f"Voice note {note_id}: bad translation answer, showing original"
        ]
    translation = answer.get("translation") or ""
    title = answer.get("title") or ""
    category = answer.get("category") or ""
    if not translation.strip() or not title.strip():
        return _translation_fallback(text), [
            f"Voice note {note_id}: empty translation, showing original"
        ]
    if category not in NOTE_CATEGORIES:
        category = "other"
    return {"english": translation, "title": title, "category": category}, []


def _translation_fallback(text):
    words = text.split()
    return {
        "english": text,
        "title": " ".join(words[:8]) or "Voice note",
        "category": "other",
    }


def _remove(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def add_voice_note(client, state, audio_bytes, filename, room_id, effort=None,
                   item_id=None):
    """Store one voice note; returns {"status": ...}.

    "stored" carries the note record; "no_speech" (empty transcript)
    stores nothing. Temp files are always deleted; the WAV is kept in
    the run folder only when a note is stored.
    """
    room = state.rooms.get(room_id)
    if room is None:
        raise NotesError(f"Unknown room {room_id!r}")
    if not audio_bytes:
        raise NotesError("Empty audio upload")
    if len(audio_bytes) > MAX_NOTE_BYTES:
        raise NotesError("Audio too large (notes stop at 60 s)")
    notes_dir = state.run_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    tag = uuid.uuid4().hex[:12]
    suffix = Path(filename or "note.webm").suffix.lower() or ".webm"
    tmp_upload = notes_dir / f"tmp-{tag}.upload{suffix}"
    tmp_wav = notes_dir / f"tmp-{tag}.wav"
    tmp_upload.write_bytes(audio_bytes)
    keep_wav = False
    try:
        convert_to_wav(tmp_upload, tmp_wav)
        text = transcribe_note(client, str(tmp_wav), state.language, tag)
        keep_wav = bool(text)
    finally:
        _remove(tmp_upload)
        if not keep_wav:
            _remove(tmp_wav)
    if not text:
        return {"status": "no_speech", "detail": "no speech heard"}
    # The id is claimed only for a stored note, so failed notes leave
    # no gaps in the N01, N02, ... numbering.
    note_id = state.next_note_id()
    wav_name = f"{note_id}.wav"
    tmp_wav.rename(notes_dir / wav_name)
    fields, notes = translate_note(
        client, text, state.language, room["name"], note_id, effort
    )
    record = {
        "id": note_id,
        "time": datetime.now().isoformat(timespec="seconds"),
        "room_id": room_id,
        "room_name": room["name"],
        "language": state.language,
        "text": text,
        "audio": wav_name,
        "item_id": item_id,
        **fields,
    }
    state.store_voice_note(record, notes)
    return {"status": "stored", "note": record}
