"""Offline tests for pot.notes with a fake client (ffmpeg is real)."""

import json
import wave

import pytest

from pot.checklist import build_checklist
from pot.meta_client import MetaClientError
from pot.notes import (
    MAX_NOTE_BYTES,
    NotesError,
    add_text_note,
    add_voice_note,
    convert_to_wav,
    transcript_text,
    translate_note,
)
from pot.runs import RunState
from tests.fakes import FakeSpark, tiny_property, tone_clip


def _state(directory, language="es"):
    property = tiny_property()
    run_dir = directory / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return RunState(run_dir, property, build_checklist(property),
                    tour={"shots": []}, language=language)


def _wav_info(path):
    with wave.open(str(path), "rb") as handle:
        return handle.getnchannels(), handle.getframerate()


@pytest.mark.parametrize("container", ["webm", "mp4"])
def test_convert_browser_audio_to_16k_mono_wav(tmp_path, container):
    src = tone_clip(tmp_path / f"note.{container}", container)
    dst = tmp_path / "note.wav"
    convert_to_wav(src, dst)
    assert _wav_info(dst) == (1, 16000)


def test_convert_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.webm"
    bad.write_bytes(b"not audio at all")
    with pytest.raises(NotesError, match="Could not convert"):
        convert_to_wav(bad, tmp_path / "bad.wav")


def test_transcript_text_prefers_transcript_then_turns():
    assert transcript_text({"transcript": " hola ", "turns": []}) == "hola"
    assert transcript_text({"transcript": "", "turns": [
        {"text": "piso"}, {"text": ""}, {"text": "limpio"},
    ]}) == "piso limpio"
    assert transcript_text({"transcript": "", "turns": []}) == ""
    assert transcript_text([]) == ""


def test_add_voice_note_stores_translation_and_news(tmp_path):
    state = _state(tmp_path)
    src = tone_clip(tmp_path / "note.webm")
    client = FakeSpark()
    result = add_voice_note(client, state, src.read_bytes(), "note.webm", "bath")
    assert result["status"] == "stored"
    note = result["note"]
    assert note["id"] == "N01"
    assert note["room_id"] == "bath" and note["room_name"] == "Bath"
    assert note["language"] == "es"
    assert note["text"] == "se acabó el café"  # default fake transcript
    assert note["english"] == "EN: se acabó el café"
    assert note["category"] == "supplies"
    assert (state.run_dir / "notes" / "N01.wav").is_file()
    assert [p.name for p in (state.run_dir / "notes").iterdir()] == ["N01.wav"]
    assert client.transcribe_calls[0]["languages"] == ["es"]
    assert state.status()["voice_notes"][0]["id"] == "N01"
    assert state.status()["news"][0]["kind"] == "note"
    saved = json.loads((state.run_dir / "first_look.json").read_text(encoding="utf-8"))
    assert saved["voice_notes"][0]["id"] == "N01"


def test_language_bias_follows_run_language(tmp_path):
    state = _state(tmp_path, language="ru")
    src = tone_clip(tmp_path / "note.mp4", "mp4")
    client = FakeSpark()
    add_voice_note(client, state, src.read_bytes(), "note.mp4", "bed")
    assert client.transcribe_calls[0]["languages"] == ["ru"]
    assert state.voice_notes[0]["language"] == "ru"


def test_empty_transcript_is_no_speech_without_retries(tmp_path):
    state = _state(tmp_path)
    src = tone_clip(tmp_path / "note.webm")
    client = FakeSpark(transcript=lambda call: {"transcript": "", "turns": []})
    result = add_voice_note(client, state, src.read_bytes(), "note.webm", "bed")
    assert result == {"status": "no_speech", "detail": "no speech heard"}
    assert len(client.transcribe_calls) == 1
    assert client.calls == []  # no translation call either
    assert state.voice_notes == []
    assert list((state.run_dir / "notes").iterdir()) == []
    result2 = add_voice_note(
        FakeSpark(), state, src.read_bytes(), "note.webm", "bed")
    assert result2["note"]["id"] == "N01"  # failed notes leave no gap


def test_rejects_unknown_room_empty_and_huge_audio(tmp_path):
    state = _state(tmp_path)
    src = tone_clip(tmp_path / "note.webm")
    blob = src.read_bytes()
    with pytest.raises(NotesError, match="Unknown room"):
        add_voice_note(FakeSpark(), state, blob, "note.webm", "attic")
    with pytest.raises(NotesError, match="Empty audio"):
        add_voice_note(FakeSpark(), state, b"", "note.webm", "bed")
    with pytest.raises(NotesError, match="too large"):
        add_voice_note(FakeSpark(), state, b"x" * (MAX_NOTE_BYTES + 1),
                       "note.webm", "bed")


def test_failed_translation_falls_back_to_original():
    def boom(call):
        raise MetaClientError("translate: boom")

    fields, notes = translate_note(
        FakeSpark(noter=boom), "lámpara rota", "es", "Bed", "N01")
    assert fields == {"english": "lámpara rota", "title": "lámpara rota",
                      "category": "other"}
    assert len(notes) == 1


def test_bad_category_becomes_other():
    def odd(call):
        return {"translation": "ok", "title": "Broken lamp", "category": "weird"}

    fields, notes = translate_note(
        FakeSpark(noter=odd), "lámpara rota", "es", "Bed", "N01")
    assert fields["category"] == "other"
    assert notes == []


def test_status_lists_notes_newest_first_with_audio_url(tmp_path):
    state = _state(tmp_path)
    src = tone_clip(tmp_path / "note.webm")
    blob = src.read_bytes()
    add_voice_note(FakeSpark(), state, blob, "note.webm", "bed")
    add_voice_note(FakeSpark(), state, blob, "note.webm", "bath")
    notes = state.status()["voice_notes"]
    assert [n["id"] for n in notes] == ["N02", "N01"]
    assert notes[0]["audio_url"] == (
        f"/api/runs/{state.run_dir.name}/notes/N02/audio"
    )
    assert notes[0]["english"] and notes[0]["title"]


def test_voice_restock_note_house_room_is_supplies(tmp_path):
    state = _state(tmp_path)
    src = tone_clip(tmp_path / "note.webm")
    client = FakeSpark(noter=lambda call: {
        "translation": "We are out of coffee", "title": "Coffee",
        "category": "damage"})
    result = add_voice_note(
        client, state, src.read_bytes(), "note.webm", "house", kind="restock")
    assert result["status"] == "stored"
    note = result["note"]
    assert note["room_id"] == "house" and note["room_name"] == "Whole house"
    assert note["category"] == "supplies"  # restock is always supplies
    assert state.status()["restock"]["note_ids"] == [note["id"]]


def test_text_restock_note_stored_supplies_no_audio(tmp_path):
    state = _state(tmp_path)
    result = add_text_note(
        FakeSpark(), state, "se acabó el papel", kind="restock")
    assert result["status"] == "stored"
    note = result["note"]
    assert note["category"] == "supplies"
    assert note["english"] == "EN: se acabó el papel"
    assert note["audio"] == ""
    assert note["room_name"] == "Whole house"
    assert state.status()["restock"]["note_ids"] == [note["id"]]
    assert not (state.run_dir / "notes" / f"{note['id']}.wav").exists()


def test_text_note_model_error_keeps_typed_text(tmp_path):
    def boom(call):
        raise MetaClientError("replay miss")

    state = _state(tmp_path)
    result = add_text_note(
        FakeSpark(noter=boom), state, "no quedan cápsulas", kind="restock")
    assert result["status"] == "stored"
    assert result["note"]["english"] == "no quedan cápsulas"
    assert result["note"]["text"] == "no quedan cápsulas"
    assert result["note"]["category"] == "supplies"


def test_restock_state_survives_load(tmp_path):
    from pot.checklist import load_property

    property = load_property("demo-house")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RunState(run_dir, property, build_checklist(property),
                     tour={"shots": []})
    note = add_text_note(
        FakeSpark(), state, "falta café", kind="restock")["note"]
    state.mark_restock_done(True)
    status = state.status()
    assert status["restock"] == {
        "answered": True, "nothing": True, "note_ids": [note["id"]]}
    loaded = RunState.load(state.run_dir)
    assert loaded.status()["restock"] == status["restock"]


def test_load_restores_notes_and_seq_continues(tmp_path):
    from pot.checklist import load_property

    property = load_property("demo-house")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = RunState(run_dir, property, build_checklist(property),
                     tour={"shots": []})
    src = tone_clip(tmp_path / "note.webm")
    add_voice_note(FakeSpark(), state, src.read_bytes(), "note.webm",
                   "master-bedroom")
    loaded = RunState.load(state.run_dir)
    assert [n["id"] for n in loaded.voice_notes] == ["N01"]
    assert loaded.status()["voice_notes"][0]["text"] == "se acabó el café"
    assert loaded.read_note_audio("N01")[:4] == b"RIFF"
    assert loaded.read_note_audio("N99") is None
    add_voice_note(FakeSpark(), loaded, src.read_bytes(), "note.webm",
                   "master-bedroom")
    assert loaded.voice_notes[-1]["id"] == "N02"
