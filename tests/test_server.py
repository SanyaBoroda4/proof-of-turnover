"""API tests for pot.server with a fake client (no network)."""

import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from PIL.Image import Exif

from pot.server import create_app
from tests.fakes import FakeSpark, photo_ids_in


def _jpeg(color="blue", taken=None):
    image = Image.new("RGB", (64, 48), color)
    if taken is not None:
        exif = Exif()
        exif.get_ifd(0x8769)[36867] = taken
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", exif=exif)
    else:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG")
    return buffer.getvalue()


def _done(call):
    pid = photo_ids_in(call["content"])[0]
    enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
    return {
        "claims": [
            {"item_id": item_id, "status": "done", "photo_id": pid,
             "seen": "looks good", "count_seen": 1}
            for item_id in enum
        ],
        "other_issues": [],
    }


def _problem(call):
    pid = photo_ids_in(call["content"])[0]
    enum = call["schema"]["properties"]["claims"]["items"]["properties"]["item_id"]["enum"]
    return {
        "claims": [
            {"item_id": item_id, "status": "problem", "photo_id": pid,
             "seen": "a mess", "count_seen": None}
            for item_id in enum
        ],
        "other_issues": [],
    }


def _not_done(call):
    return {"this_room": "yes", "verdict": "not_done", "count_seen": None,
            "reason": "a mess", "problem_object": "dirty pile"}


def _client(inspector=_done, **kwargs):
    test = TestClient(create_app(FakeSpark(inspector=inspector, **kwargs)))
    code = test.app.state.server.code
    assert code
    return test, code


def _create_run(test, code, property="demo-house", demo=False, language=None):
    body = {"property": property, "demo": demo}
    if language is not None:
        body["language"] = language
    response = test.post(f"/api/runs?code={code}", json=body)
    assert response.status_code == 200, response.text
    return response.json()["run_id"]


def _upload(test, code, run_id, shot_id, payload=None):
    return test.post(
        f"/api/runs/{run_id}/photos?code={code}",
        data={"shot_id": shot_id},
        files={"image": ("pic.jpg", payload or _jpeg(), "image/jpeg")},
    )


def _wait_idle(test, code, run_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = test.get(f"/api/runs/{run_id}/status?code={code}")
        assert response.status_code == 200
        status = response.json()
        if not status["checking"]:
            return status
        time.sleep(0.05)
    raise AssertionError("photo check did not finish in time")


def test_access_code_required_on_every_route():
    test, code = _client()
    assert test.post("/api/runs", json={"property": "demo-house"}).status_code == 401
    assert test.get("/api/server-ip").status_code == 401
    assert test.get("/api/qr?text=x").status_code == 401
    assert test.get("/api/runs/nope/status").status_code == 401
    assert test.post("/api/tour", json={"property": "demo-house"}).status_code == 401
    # ?code= once works and sets a cookie for later calls.
    response = test.get(f"/api/server-ip?code={code}")
    assert response.status_code == 200
    assert response.json()["ip"]
    assert test.get("/api/server-ip").status_code == 200
    fresh = TestClient(test.app)
    assert fresh.get("/api/server-ip?code=wrong").status_code == 401
    assert fresh.get("/api/server-ip").status_code == 401


def test_access_code_from_environment(monkeypatch):
    monkeypatch.setenv("POT_ACCESS_CODE", "house-42")
    app = create_app(FakeSpark())
    assert app.state.server.code == "house-42"


def test_create_run_unknown_property_rejected():
    test, code = _client()
    response = test.post(f"/api/runs?code={code}", json={"property": "nope"})
    assert response.status_code == 400


def test_upload_flow_proves_and_tracks_progress(tmp_path):
    test, code = _client()
    run_id = _create_run(test, code)
    response = _upload(test, code, run_id, "master-bedroom-1")
    assert response.status_code == 200
    assert response.json() == {"photo_id": "P01", "status": "checking"}
    status = _wait_idle(test, code, run_id)
    assert status["agent_state"] == "needs_input"
    assert status["progress"]["shots_taken"] == 1
    assert status["progress"]["shots_total"] == 11
    assert status["current_shot"]["shot_id"] == "guest-bedroom-1"
    assert status["photo_seconds"]["P01"] >= 0
    bed = next(r for r in status["rooms"] if r["id"] == "master-bedroom")
    proven = {i["id"]: i["status"] for i in bed["items"]}
    assert proven["master-bedroom.beds_made"] == "proven"
    assert proven["master-bedroom.coverage"] == "proven"
    bath = next(r for r in status["rooms"] if r["id"] == "guest-bedroom")
    assert all(i["status"] == "not yet" for i in bath["items"]
               if i["id"] != "guest-bedroom.coverage")
    assert all(i["request"] is None for i in bath["items"])
    calls = test.get(f"/api/runs/{run_id}/calls?code={code}").json()["calls"]
    assert calls
    assert all(c["photo_id"] == "P01" for c in calls)
    assert {c["model"] for c in calls} == {"test-spark", "test-sam"}


def test_per_shot_requests_only_for_failed_shot_items():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    bed = next(r for r in status["rooms"] if r["id"] == "master-bedroom")
    bed_requests = {i["id"] for i in bed["items"] if i["request"] is not None}
    assert bed_requests == {"master-bedroom.beds_made", "master-bedroom.no_belongings",
                            "master-bedroom.floor_clear"}
    bath = next(r for r in status["rooms"] if r["id"] == "guest-bedroom")
    assert all(i["request"] is None for i in bath["items"])
    assert all(i["status"] == "not yet" for i in bath["items"]
               if i["id"] != "guest-bedroom.coverage")


def test_old_photo_refused():
    test, code = _client()
    run_id = _create_run(test, code)
    old = (datetime.now() - timedelta(days=1)).strftime("%Y:%m:%d %H:%M:%S")
    response = _upload(test, code, run_id, "master-bedroom-1", _jpeg(taken=old))
    assert response.status_code == 400
    assert "foto antigua" in response.json()["detail"]


def test_unknown_shot_run_and_evidence():
    test, code = _client()
    run_id = _create_run(test, code)
    assert _upload(test, code, run_id, "nope").status_code == 400
    assert test.get(f"/api/runs/nope/status?code={code}").status_code == 404
    assert test.get(f"/api/runs/{run_id}/evidence/nope?code={code}").status_code == 404


def test_evidence_served_for_proven_item():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    response = test.get(
        f"/api/runs/{run_id}/evidence/master-bedroom.beds_made?code={code}"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_signoff_approve_marks_done():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    response = test.post(f"/api/runs/{run_id}/signoff?code={code}",
                         json={"decision": "approve"})
    assert response.status_code == 200
    assert response.json()["agent_state"] == "done"
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["agent_state"] == "done"
    assert status["signoff"]["decision"] == "approve"


def test_signoff_sendback_reopens_items_with_spanish_request():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    response = test.post(
        f"/api/runs/{run_id}/signoff?code={code}",
        json={"decision": "sendback", "notes": "Haz la cama otra vez",
              "item_ids": ["master-bedroom.beds_made"]},
    )
    assert response.status_code == 200
    assert response.json()["agent_state"] == "needs_input"
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    bed = next(r for r in status["rooms"] if r["id"] == "master-bedroom")
    item = next(i for i in bed["items"] if i["id"] == "master-bedroom.beds_made")
    assert item["status"] == "needs photo"
    assert item["reason"] == "Haz la cama otra vez"
    assert item["request"]["cleaner"]
    assert test.post(f"/api/runs/{run_id}/signoff?code={code}",
                     json={"decision": "sendback", "item_ids": []}).status_code == 400
    assert test.post(f"/api/runs/{run_id}/signoff?code={code}",
                     json={"decision": "maybe"}).status_code == 400


def test_qr_returns_png():
    test, code = _client()
    response = test.get(f"/api/qr?code={code}&text=http://example/clean?code={code}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_pages_need_code_and_serve_html():
    test, code = _client()
    assert test.get("/clean").status_code == 401
    assert test.get("/host").status_code == 401
    clean = test.get(f"/clean?code={code}")
    assert clean.status_code == 200
    assert "Turno de limpieza" in clean.text
    host = test.get(f"/host?code={code}")
    assert host.status_code == 200
    assert "Turnover" in host.text
    index = test.get(f"/?code={code}", follow_redirects=False)
    assert index.status_code == 302
    assert index.headers["location"].startswith("/host?")


def test_properties_and_runs_lists():
    test, code = _client()
    props = test.get(f"/api/properties?code={code}").json()["properties"]
    ids = {p["id"] for p in props}
    assert {"demo-house", "rental-1"} <= ids
    assert test.get(f"/api/runs?code={code}").json() == {"runs": []}
    run_id = _create_run(test, code)
    runs = test.get(f"/api/runs?code={code}").json()["runs"]
    assert [r["run_id"] for r in runs] == [run_id]
    assert runs[0]["property"] == "demo-house"
    assert runs[0]["agent_state"] == "needs_input"


def test_demo_upload_keeps_bytes(tmp_path):
    test, code = _client()
    run_id = _create_run(test, code, demo=True)
    payload = _jpeg()
    response = _upload(test, code, run_id, "master-bedroom-1", payload)
    assert response.status_code == 200
    check = tmp_path / "data" / "runs" / run_id / "photos" / "P01_check.jpg"
    assert check.read_bytes() == payload
    live_id = _create_run(test, code)
    _upload(test, code, live_id, "master-bedroom-1", payload)
    resaved = tmp_path / "data" / "runs" / live_id / "photos" / "P01_check.jpg"
    assert resaved.read_bytes() != payload


def test_demo_photos_endpoint(tmp_path, monkeypatch):
    from pot import config

    bundle = tmp_path / "bundle" / "photos"
    bundle.mkdir(parents=True)
    Image.new("RGB", (32, 24), "blue").save(bundle / "01.jpg", "JPEG")
    (bundle / "notes.txt").write_text("ignored")
    monkeypatch.setattr(config, "DEMO_DIR", tmp_path / "bundle")
    test, code = _client()
    photos = test.get(f"/api/demo-photos?code={code}").json()["photos"]
    assert [p["name"] for p in photos] == ["01.jpg"]
    blob = test.get(f"/api/demo-photos/01.jpg?code={code}")
    assert blob.status_code == 200
    assert blob.content[:2] == b"\xff\xd8"
    assert test.get(f"/api/demo-photos/nope.jpg?code={code}").status_code == 404
    assert test.get(f"/api/demo-photos/..%2Fnope?code={code}").status_code in (404, 400)


def test_demo_photos_empty_without_bundle(tmp_path, monkeypatch):
    from pot import config

    monkeypatch.setattr(config, "DEMO_DIR", tmp_path / "missing")
    test, code = _client()
    assert test.get(f"/api/demo-photos?code={code}").json() == {"photos": []}


def test_latest_run_without_loading(tmp_path):
    from pot.server import latest_run_id

    assert latest_run_id() is None
    runs = tmp_path / "data" / "runs"
    for name in ("run-20261031-101241", "run-20261031-101242", "run-20261031-101241_2"):
        folder = runs / name
        folder.mkdir(parents=True)
        (folder / "first_look.json").write_text("{}", encoding="utf-8")
    (runs / "scratch").mkdir()
    # Lexicographic max would be the _2 suffixed older run; time wins.
    assert latest_run_id() == "run-20261031-101242"
    test, code = _client()
    assert test.get(f"/api/runs/latest?code={code}").json() == {
        "run_id": "run-20261031-101242"}


def test_overview_endpoint_and_cache_headers():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    response = test.get(f"/api/runs/{run_id}/photos/P01/overview?code={code}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=31536000"
    assert response.content[:2] == b"\xff\xd8"
    assert test.get(f"/api/runs/{run_id}/photos/P99/overview?code={code}").status_code == 404
    evidence = test.get(
        f"/api/runs/{run_id}/evidence/master-bedroom.beds_made?code={code}&v=P01")
    assert evidence.status_code == 200
    assert evidence.headers["cache-control"] == "public, max-age=31536000"


def test_backslash_ids_rejected():
    test, code = _client()
    assert test.get(f"/api/runs/a%5Cb/status?code={code}").status_code == 400
    run_id = _create_run(test, code)
    response = test.post(
        f"/api/runs/{run_id}/photos?code={code}",
        data={"shot_id": "..\\x"},
        files={"image": ("pic.jpg", _jpeg(), "image/jpeg")},
    )
    assert response.status_code == 400
    assert test.get(f"/api/runs/{run_id}/evidence/a%5Cb?code={code}").status_code == 400
    assert test.get(
        f"/api/runs/{run_id}/photos/P%5C1/overview?code={code}").status_code == 400


def test_startup_lines_mention_tunnel():
    from pot.server import startup_lines

    lines = startup_lines(8000, "c0de")
    assert any("cloudflared tunnel --url http://localhost:8000" in line for line in lines)
    assert any("--host 0.0.0.0" in line for line in lines)
    assert any("c0de" in line for line in lines)


def test_status_news_covers_problem_request_and_signoff():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    kinds = [e["kind"] for e in status["news"]]
    assert kinds[0] == "request"
    assert "status" in kinds
    assert status["news"][0]["photo_id"] == "P01"
    test.post(f"/api/runs/{run_id}/signoff?code={code}", json={"decision": "approve"})
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["news"][0]["kind"] == "signoff"


def test_generate_tour_endpoint(tmp_path, monkeypatch):
    import shutil

    props = tmp_path / "properties" / "demo-house"
    props.mkdir(parents=True)
    shutil.copy(Path("properties/demo-house/property.json"), props / "property.json")
    monkeypatch.setattr("pot.config.PROPERTIES_DIR", tmp_path / "properties")

    def tour(call):
        return {"shots": [{
            "shot_id": "bed-1", "room_id": "master-bedroom",
            "item_ids": ["master-bedroom.beds_made"],
            "instruction_es": "Foto", "instruction_en": "Photo",
        }]}
    test = TestClient(create_app(FakeSpark(tour=tour)))
    code = test.app.state.server.code
    response = test.post(f"/api/tour?code={code}", json={"property": "demo-house"})
    assert response.status_code == 200
    shots = response.json()["shots"]
    assert shots[0]["shot_id"] == "bed-1"
    assert any("-extra-" in s["shot_id"] for s in shots[1:])
    saved = json.loads((props / "tour.json").read_text(encoding="utf-8"))
    assert saved["shots"] == shots


def test_create_run_language_accepted_and_in_status():
    for lang in ("es", "en", "ru"):
        test, code = _client()
        response = test.post(
            f"/api/runs?code={code}",
            json={"property": "demo-house", "language": lang},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["language"] == lang
        status = test.get(f"/api/runs/{body['run_id']}/status?code={code}")
        assert status.status_code == 200
        assert status.json()["language"] == lang


def test_create_run_rejects_unsupported_language():
    test, code = _client()
    response = test.post(
        f"/api/runs?code={code}",
        json={"property": "demo-house", "language": "fr"},
    )
    assert response.status_code == 400


def test_create_run_defaults_to_property_language():
    test, code = _client()
    run_id = _create_run(test, code)
    status = test.get(f"/api/runs/{run_id}/status?code={code}")
    assert status.json()["language"] == "es"  # demo-house cleaner_language


def test_languages_endpoint_lists_native_names():
    test, code = _client()
    response = test.get(f"/api/languages?code={code}")
    assert response.status_code == 200
    assert response.json()["languages"] == [
        {"code": "es", "name": "Español"},
        {"code": "en", "name": "English"},
        {"code": "ru", "name": "Русский"},
    ]


def _note_upload(test, code, run_id, room_id, payload, name="note.webm"):
    return test.post(
        f"/api/runs/{run_id}/notes?code={code}",
        data={"room_id": room_id},
        files={"audio": (name, payload, "audio/webm")},
    )


def test_notes_endpoint_stores_and_serves_audio(tmp_path):
    from tests.fakes import tone_clip

    test, code = _client()
    run_id = _create_run(test, code, language="ru")
    blob = tone_clip(tmp_path / "note.webm").read_bytes()
    response = _note_upload(test, code, run_id, "master-bedroom", blob)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "stored"
    assert body["note"]["id"] == "N01"
    assert body["note"]["language"] == "ru"
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert [n["id"] for n in status["voice_notes"]] == ["N01"]
    assert status["news"][0]["kind"] == "note"
    audio = test.get(f"/api/runs/{run_id}/notes/N01/audio?code={code}")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    assert test.get(f"/api/runs/{run_id}/notes/N99/audio?code={code}").status_code == 404
    purposes = [c["purpose"] for c in
                test.get(f"/api/runs/{run_id}/calls?code={code}").json()["calls"]]
    assert any(p.startswith("transcribe voice note") for p in purposes)
    assert any(p.startswith("translate voice note") for p in purposes)


def test_notes_endpoint_no_speech_and_bad_room(tmp_path):
    from tests.fakes import FakeSpark, tone_clip

    test, code = _client()
    run_id = _create_run(test, code)
    blob = tone_clip(tmp_path / "note.webm").read_bytes()
    assert _note_upload(test, code, run_id, "attic", blob).status_code == 400
    quiet = TestClient(create_app(FakeSpark(
        transcript=lambda call: {"transcript": "", "turns": []})))
    hush = quiet.app.state.server.code
    run_id = _create_run(quiet, hush)
    response = _note_upload(quiet, hush, run_id, "master-bedroom", blob)
    assert response.status_code == 200
    assert response.json() == {"status": "no_speech", "detail": "no speech heard"}
    status = quiet.get(f"/api/runs/{run_id}/status?code={hush}").json()
    assert status["voice_notes"] == []
