"""API tests for pot.server with a fake client (no network)."""

import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from PIL.Image import Exif

from pot.meta_client import MetaClientError, call_context
from pot.server import api_startup_line, create_app
from tests.fakes import SAM_FIXTURE, FakeSpark, photo_ids_in


class _ApiDownSpark(FakeSpark):
    """Every model call fails like a rejected key, then optionally recovers.

    Each failure fires a failed hook entry (like the real client's trace)
    and raises MetaClientError. Flip `down` to False to answer normally.
    """

    def __init__(self, status=401, **kwargs):
        super().__init__(**kwargs)
        self.fail_status = status
        self.down = True

    def _fail(self, purpose, model):
        self._fire(purpose, model, ok=False, status=self.fail_status)
        raise MetaClientError(
            f"{purpose}: request failed with HTTP {self.fail_status}"
        )

    def respond(self, purpose, content, instructions=None, schema=None,
                max_output_tokens=16000, effort="medium"):
        if not self.down:
            return super().respond(
                purpose, content, instructions, schema,
                max_output_tokens, effort,
            )
        self.calls.append({
            "purpose": purpose, "content": content,
            "instructions": instructions, "schema": schema, "effort": effort,
        })
        self._fail(purpose, "test-spark")

    def segment_image(self, purpose, jpeg_bytes, prompt):
        if not self.down:
            return super().segment_image(purpose, jpeg_bytes, prompt)
        self.sam_calls.append(
            {"purpose": purpose, "jpeg_bytes": jpeg_bytes, "prompt": prompt}
        )
        self._fail(purpose, "test-sam")


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


def _host(test):
    return test.app.state.server.host_code


def _create_run(test, code, property="demo-house", demo=False, language=None,
                test_mode=False):
    body = {"property": property, "demo": demo, "test": test_mode}
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
    assert test.post("/api/runs/nope/items/x/resolve",
                     json={"action": "its_fine"}).status_code == 401
    assert test.post("/api/runs/nope/finish").status_code == 401
    assert test.get("/api/server-ip").status_code == 401
    assert test.get("/api/qr?text=x").status_code == 401
    assert test.get("/api/runs/nope/status").status_code == 401
    assert test.post("/api/tour", json={"property": "demo-house"}).status_code == 401
    # ?code= once works and sets a cookie for later calls.
    response = test.get(f"/api/server-ip?code={_host(test)}")
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
    calls = test.get(f"/api/runs/{run_id}/calls?code={_host(test)}").json()["calls"]
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


def test_evidence_card_param_serves_uncaptioned_copy():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    captioned = test.get(
        f"/api/runs/{run_id}/evidence/master-bedroom.beds_made?code={code}"
    )
    card = test.get(
        f"/api/runs/{run_id}/evidence/master-bedroom.beds_made?code={code}&card=1"
    )
    assert card.status_code == 200
    assert card.headers["content-type"] == "image/jpeg"
    assert card.headers["cache-control"] == "public, max-age=31536000"
    assert card.content != captioned.content
    with Image.open(io.BytesIO(card.content)) as card_img, \
         Image.open(io.BytesIO(captioned.content)) as cap_img:
        assert card_img.size[0] == cap_img.size[0]
        assert card_img.size[1] < cap_img.size[1]


def test_signoff_approve_marks_done():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    response = test.post(f"/api/runs/{run_id}/signoff?code={_host(test)}",
                         json={"decision": "approve", "force": True})
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
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
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
    assert test.post(f"/api/runs/{run_id}/signoff?code={_host(test)}",
                     json={"decision": "sendback", "item_ids": []}).status_code == 400
    assert test.post(f"/api/runs/{run_id}/signoff?code={_host(test)}",
                     json={"decision": "maybe"}).status_code == 400


def test_qr_returns_png():
    test, code = _client()
    response = test.get(f"/api/qr?code={_host(test)}&text=http://example/clean?code={code}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_approve_rejected_until_ready_unless_forced():
    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    assert status["agent_state"] != "awaiting_signoff"
    rejected = test.post(
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
        json={"decision": "approve", "notes": "", "item_ids": []},
    )
    assert rejected.status_code == 400
    assert "items open" in rejected.json()["detail"]
    assert "photos" in rejected.json()["detail"]
    forced = test.post(
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
        json={"decision": "approve", "force": True, "notes": "", "item_ids": []},
    )
    assert forced.status_code == 200, forced.text


def test_cleaner_code_cannot_use_host_routes():
    test, code = _client()
    host = _host(test)
    assert host and host != code
    run_id = _create_run(test, code)
    assert test.get(f"/api/runs/{run_id}/status?code={code}").status_code == 200
    assert test.get(f"/host?code={code}").status_code == 401
    assert test.post(
        f"/api/runs/{run_id}/signoff?code={code}",
        json={"decision": "approve", "force": True, "notes": "", "item_ids": []},
    ).status_code == 401
    assert test.post(
        f"/api/setups?code={code}",
        json={"name": "X", "bedrooms": 1},
    ).status_code == 200
    assert test.get(
        f"/api/runs/{run_id}/calls?code={code}").status_code == 401
    assert test.get(
        f"/api/runs/{run_id}/notes/N01/audio?code={code}").status_code == 401
    assert test.get(f"/api/cleaner-code?code={code}").status_code == 401
    assert test.get(f"/api/cleaner-code?code={host}").json() == {"code": code}
    assert test.get(f"/host?code={host}").status_code == 200
    assert test.get(f"/api/role?code={code}").json() == {"role": "cleaner"}
    assert test.get(f"/api/role?code={host}").json() == {"role": "host"}
    assert test.get(f"/setup?code={code}").status_code == 200
    assert test.get(f"/setup?code={host}").status_code == 200


def test_pages_need_code_and_serve_html():
    test, code = _client()
    assert test.get("/clean").status_code == 401
    assert test.get("/host").status_code == 401
    clean = test.get(f"/clean?code={code}")
    assert clean.status_code == 200
    assert "Turno de limpieza" in clean.text
    host = test.get(f"/host?code={_host(test)}")
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
    assert test.get(f"/api/runs?code={_host(test)}").json() == {"runs": []}
    run_id = _create_run(test, code)
    runs = test.get(f"/api/runs?code={_host(test)}").json()["runs"]
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
    assert test.get(f"/api/runs/latest?code={_host(test)}").json() == {
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
    test.post(f"/api/runs/{run_id}/signoff?code={_host(test)}", json={"decision": "approve", "force": True})
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
    response = test.post(f"/api/tour?code={_host(test)}", json={"property": "demo-house"})
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
    audio = test.get(f"/api/runs/{run_id}/notes/N01/audio?code={_host(test)}")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content[:4] == b"RIFF"
    assert test.get(f"/api/runs/{run_id}/notes/N99/audio?code={_host(test)}").status_code == 404
    purposes = [c["purpose"] for c in
                test.get(f"/api/runs/{run_id}/calls?code={_host(test)}").json()["calls"]]
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


def _problem_run():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    return test, code, run_id


def _status_item(status, item_id):
    for room in status["rooms"]:
        for item in room["items"]:
            if item["id"] == item_id:
                return item
    raise AssertionError(f"no {item_id} in status")


def _resolve(test, code, run_id, item_id, action="cant_fix", reason="supplies",
             note_id=None):
    return test.post(
        f"/api/runs/{run_id}/items/{item_id}/resolve?code={code}",
        json={"action": action, "reason": reason, "note_id": note_id},
    )


def test_resolve_validates_item_action_reason_and_note():
    test, code, run_id = _problem_run()
    _wait_idle(test, code, run_id)
    assert _resolve(test, code, run_id, "nope.nothing").status_code == 404
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    optional = next(
        item["id"] for room in status["rooms"] for item in room["items"]
        if not item["critical"]
    )
    assert _resolve(test, code, run_id, optional).status_code == 400
    assert _resolve(test, code, run_id, "master-bedroom.coverage").status_code == 400
    open_id = status["cards"][0]
    assert _resolve(test, code, run_id, open_id, action="later").status_code == 400
    assert _resolve(test, code, run_id, open_id, reason="tired").status_code == 400
    assert _resolve(test, code, run_id, open_id, note_id="N99").status_code == 400


def test_resolve_stores_and_finish_gates_on_open_items():
    test, code, run_id = _problem_run()
    status = _wait_idle(test, code, run_id)
    assert status["cards"]
    open_id = status["cards"][0]
    response = _resolve(test, code, run_id, open_id)
    assert response.status_code == 200, response.text
    assert response.json()["resolution"]["action"] == "cant_fix"
    assert response.json()["agent_state"] == "needs_input"
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert _status_item(status, open_id)["resolution"]["reason"] == "supplies"
    assert open_id not in status["cards"]
    assert status["news"][0]["kind"] == "resolution"
    refused = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert refused.status_code == 400
    assert "still open" in refused.json()["detail"]
    for room in status["rooms"]:
        for item in room["items"]:
            if (item["critical"] and item["status"] != "proven"
                    and item["resolution"] is None):
                assert _resolve(test, code, run_id, item["id"],
                                action="its_fine", reason=None).status_code == 200
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["agent_state"] == "awaiting_signoff"
    done = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert done.status_code == 200, done.text
    assert done.json()["finished_at"]
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["finished_at"] == done.json()["finished_at"]


def test_finish_refused_while_photo_checking():
    import threading

    entered = threading.Event()
    release = threading.Event()

    def slow(call):
        entered.set()
        assert release.wait(15)
        return _done(call)

    test = TestClient(create_app(FakeSpark(inspector=slow)))
    code = test.app.state.server.code
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    assert entered.wait(15)
    refused = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert refused.status_code == 400
    assert "still checking" in refused.json()["detail"]
    release.set()
    _wait_idle(test, code, run_id)


def test_full_tour_proves_and_finishes():
    # SAM must find 4 towels itself now (the second look cannot lend
    # its count): clone one fixture object so the fake finds 4.
    extra = "," + "3" + SAM_FIXTURE[
        SAM_FIXTURE.index("1<|box") + 1:SAM_FIXTURE.index(",2<|box")]
    extra += ",4" + SAM_FIXTURE[SAM_FIXTURE.index(",2<|box") + 3:].rstrip()
    test, code = _client(sam=lambda call: SAM_FIXTURE.rstrip() + extra)
    run_id = _create_run(test, code)
    tour = test.get(f"/api/runs/{run_id}/tour?code={code}").json()
    assert len(tour["shots"]) == 11
    for shot in tour["shots"]:
        assert _upload(test, code, run_id, shot["shot_id"]).status_code == 200
    status = _wait_idle(test, code, run_id, timeout=60)
    assert status["agent_state"] == "awaiting_signoff", status["open"]
    assert status["cards"] == []
    done = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert done.status_code == 200, done.text


def test_retake_reuses_existing_request_text():
    test, code, run_id = _problem_run()
    status = _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    item = _status_item(status, item_id)
    assert item["request"] and item["request"]["cleaner"]
    fake = test.app.state.server.client

    def request_calls():
        return [
            c for c in fake.calls
            if "requests" in ((c.get("schema") or {}).get("properties") or {})
        ]

    assert len(request_calls()) == 1
    first_text = item["request"]["cleaner"]
    # Retake: the item still fails, but no new requests call is made.
    assert _upload(test, code, run_id, "master-bedroom-1").status_code == 200
    status = _wait_idle(test, code, run_id)
    item = _status_item(status, item_id)
    assert item["status"] != "proven"
    assert item["request"]["cleaner"] == first_text
    assert len(request_calls()) == 1


def test_before_image_served_after_fix():
    test, code, run_id = _problem_run()
    _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    fake = test.app.state.server.client
    fake._inspector = _done
    fake._verifier = lambda call: {
        "this_room": "yes", "verdict": "done", "count_seen": 5,
        "reason": "looks good", "problem_object": "",
    }
    assert _upload(test, code, run_id, "master-bedroom-1").status_code == 200
    status = _wait_idle(test, code, run_id)
    item = _status_item(status, item_id)
    assert item["status"] == "proven"
    assert item["has_before"] is True
    before = test.get(f"/api/runs/{run_id}/evidence/{item_id}.before?code={code}")
    assert before.status_code == 200
    assert before.headers["content-type"] == "image/jpeg"
    after = test.get(f"/api/runs/{run_id}/evidence/{item_id}?code={code}")
    assert after.status_code == 200
    assert after.content != before.content


def test_timeline_and_call_log_written_for_live_photo():
    from pot import config

    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    run_dir = config.RUNS_DIR / run_id
    kinds = [
        json.loads(line)["event"]
        for line in (run_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for event in ("photo_received", "checks_started", "checks_done",
                  "requests_done", "photo_finished"):
        assert event in kinds
    calls = [
        json.loads(line)
        for line in (run_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert calls
    first = calls[0]
    assert "." in first["time"]  # milliseconds
    assert first["seconds"] == round(first["seconds"], 2)
    assert "usage" not in first and "status" not in first  # fake ok call


def test_notes_endpoint_links_item_id(tmp_path):
    from tests.fakes import tone_clip

    test, code = _client()
    run_id = _create_run(test, code)
    blob = tone_clip(tmp_path / "note.webm").read_bytes()
    bad = test.post(
        f"/api/runs/{run_id}/notes?code={code}",
        data={"room_id": "master-bedroom", "item_id": "nope.nothing"},
        files={"audio": ("note.webm", blob, "audio/webm")},
    )
    assert bad.status_code == 400
    good = test.post(
        f"/api/runs/{run_id}/notes?code={code}",
        data={"room_id": "master-bedroom",
              "item_id": "master-bedroom.beds_made"},
        files={"audio": ("note.webm", blob, "audio/webm")},
    )
    assert good.status_code == 200, good.text
    note = good.json()["note"]
    assert note["item_id"] == "master-bedroom.beds_made"
    stored = test.post(
        f"/api/runs/{run_id}/items/master-bedroom.beds_made/resolve?code={code}",
        json={"action": "cant_fix", "reason": "broken", "note_id": note["id"]},
    )
    assert stored.status_code == 200, stored.text
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    item = _status_item(status, "master-bedroom.beds_made")
    assert item["resolution"]["note_id"] == note["id"]


def test_api_failure_leaves_photo_unchecked_without_cards():
    test = TestClient(create_app(_ApiDownSpark()))
    code = test.app.state.server.code
    run_id = _create_run(test, code)
    assert _upload(test, code, run_id, "master-bedroom-1").status_code == 200
    status = _wait_idle(test, code, run_id)
    assert status["checking"] == []
    assert status["unchecked"] == ["P01"]
    assert status["cards"] == []
    assert status["api_status"] == {"ok": False, "message": "key rejected (401)"}
    item = _status_item(status, "master-bedroom.beds_made")
    assert item["status"] == "needs photo"
    assert item["request"] is None
    refused = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert refused.status_code == 400
    assert "could not be checked" in refused.json()["detail"]


class _FlakySpark(FakeSpark):
    """Fails the first N respond calls (fired as failed), then answers."""

    def __init__(self, fails=2, **kwargs):
        super().__init__(**kwargs)
        self._fails = fails

    def respond(self, purpose, content, instructions=None, schema=None,
                max_output_tokens=16000, effort="medium", model=None):
        if self._fails:
            self._fails -= 1
            self.calls.append({
                "purpose": purpose, "content": content,
                "instructions": instructions, "schema": schema,
                "effort": effort, "model": model,
            })
            self._fire(purpose, "test-spark", ok=False, status=503)
            raise MetaClientError(f"{purpose}: request failed with HTTP 503")
        return super().respond(
            purpose, content, instructions, schema,
            max_output_tokens, effort, model,
        )


def test_api_failure_retries_once_automatically():
    fake = _FlakySpark(fails=2, inspector=_done)
    test = TestClient(create_app(fake))
    code = test.app.state.server.code
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id, timeout=30)
    assert status["unchecked"] == []
    assert _status_item(status, "master-bedroom.beds_made")["status"] == "proven"


def _await_wonder(test, code, run_id, item_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
        item = _status_item(status, item_id)
        wonder = (item.get("resolution") or {}).get("wonder") or {}
        if wonder.get("status") in ("done", "failed"):
            return wonder
        time.sleep(0.1)
    return {}


def test_second_opinion_for_its_fine_uses_wonder_model():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    assert _resolve(test, code, run_id, item_id,
                    action="its_fine", reason=None).status_code == 200
    wonder = _await_wonder(test, code, run_id, item_id)
    assert wonder.get("status") == "done"
    assert wonder.get("model") == "muse-wonder-1.0"
    assert wonder.get("opinion") == "still_a_problem"
    assert wonder.get("requirement_met") == "not_met"
    assert wonder.get("evidence") == "The photo shows the item is not done."
    assert wonder.get("reason") == wonder.get("evidence")
    calls = [c for c in test.app.state.server.client.calls
             if (c.get("purpose") or "").startswith("second opinion")]
    assert len(calls) == 1
    assert calls[0]["model"] == "muse-wonder-1.0"
    # Schema order: evidence first, then the decision.
    assert list(calls[0]["schema"]["properties"]) == [
        "evidence", "requirement_met"]
    # Independent: no cleaner answer, no first/second look in the prompt.
    prompt = (calls[0]["instructions"] or "") + " " + " ".join(
        part.get("text", "") for part in calls[0]["content"])
    assert "its_fine" not in prompt and "Cleaner answered" not in prompt
    assert "First look" not in prompt and "Second look:" not in prompt
    assert "Master bedroom" in prompt or "Beds made" in prompt


def test_second_opinion_guard_forces_unclear():
    from pot.server import wonder_guard

    assert wonder_guard("") == "empty evidence"
    assert wonder_guard("word " * 41) == "evidence over 40 words"
    assert wonder_guard("Полки пустые, полотенец нет совсем") is not None
    assert wonder_guard("The shelves are empty and no towels are visible.") is None

    def bad_opinion(call):
        return {"evidence": "word " * 41, "requirement_met": "met"}

    test, code = _client(
        inspector=_problem, verifier=_not_done, opinion=bad_opinion)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    assert _resolve(test, code, run_id, item_id,
                    action="its_fine", reason=None).status_code == 200
    wonder = _await_wonder(test, code, run_id, item_id)
    assert wonder.get("opinion") == "unclear"
    assert wonder.get("guard") == "evidence over 40 words"
    assert "guard" in (wonder.get("notes") or "")


class _ContextSpark(FakeSpark):
    """Captures the call context seen by second-opinion calls."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.contexts = []

    def respond(self, purpose, content, instructions=None, schema=None,
                max_output_tokens=16000, effort="medium", model=None):
        if schema and "requirement_met" in (schema.get("properties") or {}):
            self.contexts.append(dict(call_context.get() or {}))
        return super().respond(
            purpose, content, instructions, schema,
            max_output_tokens, effort, model,
        )


def test_second_opinion_runs_without_live_check_budget():
    fake = _ContextSpark(inspector=_problem, verifier=_not_done)
    test = TestClient(create_app(fake))
    code = test.app.state.server.code
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    assert _resolve(test, code, run_id, item_id,
                    action="its_fine", reason=None).status_code == 200
    wonder = _await_wonder(test, code, run_id, item_id)
    assert wonder.get("status") == "done"
    assert len(fake.contexts) == 1
    context = fake.contexts[0]
    assert "live_checks" not in context
    assert context.get("photo_id")
    assert context.get("run_dir", "").endswith(run_id)


def test_second_opinion_skipped_for_cant_fix_supplies():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    item_id = "master-bedroom.beds_made"
    assert _resolve(test, code, run_id, item_id).status_code == 200
    deadline = time.time() + 1.5
    while time.time() < deadline:
        status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
        item = _status_item(status, item_id)
        assert "wonder" not in (item.get("resolution") or {})
        time.sleep(0.1)
    calls = [c for c in test.app.state.server.client.calls
             if (c.get("purpose") or "").startswith("second opinion")]
    assert calls == []


def test_wonder_startup_line():
    from pot.server import wonder_startup_line

    class _Has:
        def list_models(self):
            return {"data": [{"id": "muse-wonder-1.0"}]}

    class _Missing:
        def list_models(self):
            return {"data": [{"id": "muse-spark-1.3-contributor"}]}

    assert wonder_startup_line(_Has()) == ("Muse Wonder: OK", True)
    line, available = wonder_startup_line(_Missing())
    assert available is False
    assert line == ("Muse Wonder: not available for this key "
                    "(second opinions off)")


def test_startup_checks_fetch_models_once():
    from pot.server import startup_check_lines

    class _Counting:
        def __init__(self):
            self.calls = 0

        def list_models(self):
            self.calls += 1
            return {"data": [{"id": "muse-wonder-1.0"}]}

    client = _Counting()
    meta_line, wonder_line, wonder_ok = startup_check_lines(client)
    assert client.calls == 1
    assert meta_line == "Meta API: OK"
    assert wonder_line == "Muse Wonder: OK"
    assert wonder_ok is True


def test_voice_endpoint_validation(monkeypatch):
    test, code = _client()
    too_long = test.post(f"/api/voice?code={code}",
                         json={"text": "x" * 501, "lang": "en"})
    assert too_long.status_code == 400
    bad_lang = test.post(f"/api/voice?code={code}",
                         json={"text": "Hello", "lang": "fr"})
    assert bad_lang.status_code == 400
    monkeypatch.setenv("POT_VOICE", "device")
    off = test.post(f"/api/voice?code={code}",
                    json={"text": "Hello", "lang": "en"})
    assert off.status_code == 503


def test_voice_endpoint_returns_mp3(monkeypatch):
    monkeypatch.setattr("pot.voice.synthesize", lambda text, lang: b"ID3fake")
    test, code = _client()
    resp = test.post(f"/api/voice?code={code}",
                     json={"text": "Hello", "lang": "en"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"ID3fake"


def test_approve_replaces_temporary_example_and_clears_marker():
    from pot import config, examples as examples_mod

    test, code = _client()
    run_id = _create_run(test, code)
    examples_mod.save_example(
        "demo-house", "master-bedroom-1", _example_jpeg("red"))
    examples_mod.set_temporary("demo-house", "master-bedroom-1", True)
    before = (
        config.EXAMPLES_DIR / "demo-house" / "master-bedroom-1.jpg"
    ).read_bytes()
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    approved = test.post(
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
        json={"decision": "approve", "force": True, "notes": "", "item_ids": []},
    )
    assert approved.status_code == 200, approved.text
    after = (
        config.EXAMPLES_DIR / "demo-house" / "master-bedroom-1.jpg"
    ).read_bytes()
    assert after != before
    assert not examples_mod.is_temporary("demo-house", "master-bedroom-1")


def test_voice_prefetch_endpoint(monkeypatch):
    calls = []

    def fake_prefetch(texts, lang):
        calls.append((list(texts), lang))
        return len(texts)

    monkeypatch.setattr("pot.voice.prefetch", fake_prefetch)
    test, code = _client()
    resp = test.post(f"/api/voice/prefetch?code={code}",
                     json={"lang": "en", "texts": ["One", "Two"]})
    assert resp.status_code == 200
    assert resp.json() == {"queued": 2}
    assert calls == [(["One", "Two"], "en")]
    too_many = test.post(
        f"/api/voice/prefetch?code={code}",
        json={"lang": "en", "texts": ["x"] * 81})
    assert too_many.status_code == 400
    bad_text = test.post(
        f"/api/voice/prefetch?code={code}",
        json={"lang": "en", "texts": ["x" * 501]})
    assert bad_text.status_code == 400
    bad_lang = test.post(
        f"/api/voice/prefetch?code={code}",
        json={"lang": "fr", "texts": ["Bonjour"]})
    assert bad_lang.status_code == 400


def test_recheck_unchecked_retries_and_clears():
    fake = _ApiDownSpark(inspector=_done)
    test = TestClient(create_app(fake))
    code = test.app.state.server.code
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    assert _wait_idle(test, code, run_id)["unchecked"] == ["P01"]
    fake.down = False
    retry = test.post(f"/api/runs/{run_id}/recheck-unchecked?code={code}")
    assert retry.status_code == 200, retry.text
    assert retry.json()["rechecking"] == ["P01"]
    status = _wait_idle(test, code, run_id)
    assert status["unchecked"] == []
    assert status["api_status"] == {"ok": True, "message": ""}
    assert _status_item(status, "master-bedroom.beds_made")["status"] == "proven"
    again = test.post(f"/api/runs/{run_id}/recheck-unchecked?code={code}")
    assert again.json()["rechecking"] == []


def test_run_photo_upload_over_25mb_is_413():
    test, code = _client()
    run_id = _create_run(test, code)
    response = _upload(test, code, run_id, "master-bedroom-1",
                       payload=b"x" * (25 * 1024 * 1024 + 1))
    assert response.status_code == 413
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["progress"]["shots_taken"] == 0


def test_oversized_upload_leaves_no_temp_file():
    import tempfile
    from pathlib import Path

    test, code = _client()
    run_id = _create_run(test, code)
    temp_dir = Path(tempfile.gettempdir())
    before = set(temp_dir.glob("tmp*.jpg"))
    response = _upload(test, code, run_id, "master-bedroom-1",
                       payload=b"x" * (25 * 1024 * 1024 + 1))
    assert response.status_code == 413
    assert set(temp_dir.glob("tmp*.jpg")) <= before


def test_skip_routes_reject_non_test_runs():
    test, code = _client()
    run_id = _create_run(test, code)
    assert test.post(f"/api/runs/{run_id}/end-round?code={code}").status_code == 400
    assert test.post(
        f"/api/runs/{run_id}/skip-shot?code={code}",
        json={"shot_id": "master-bedroom-1"},
    ).status_code == 400
    skipped = test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bedroom"},
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["skipped"] == ["master-bedroom-1"]


def test_unskip_room_restores_shots_on_normal_run():
    test, code = _client()
    run_id = _create_run(test, code)
    assert test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bedroom"},
    ).status_code == 200
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["skipped_shots"] == ["master-bedroom-1"]
    assert status["skipped_areas"] == [{
        "room_id": "master-bedroom", "room_name": "Master bedroom",
        "shots": 1,
        "items": ["Beds made", "No guest belongings left", "Floor clear"],
        "item_ids": [
            "master-bedroom.beds_made",
            "master-bedroom.no_belongings",
            "master-bedroom.floor_clear",
        ],
        "checks": ["Beds made", "No guest belongings left", "Floor clear"],
    }]
    restored = test.post(
        f"/api/runs/{run_id}/unskip-room?code={code}",
        json={"room_id": "master-bedroom"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == ["master-bedroom-1"]
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["skipped_shots"] == []
    assert status["skipped_areas"] == []
    # The tour continues: the restored shot can be photographed.
    assert _upload(test, code, run_id, "master-bedroom-1").status_code == 200


def test_skipped_area_items_stay_needs_proof_not_done():
    test, code = _client()
    run_id = _create_run(test, code)
    skipped = test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bedroom"},
    )
    assert skipped.status_code == 200, skipped.text
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["agent_state"] == "needs_input"
    assert status["not_photographed_count"] == 4
    assert status["not_photographed_areas"] == ["Master bedroom"]
    assert "master-bedroom.beds_made" in status["not_photographed_items"]
    assert status["verdict"]["ready"] is False
    assert "master-bedroom.beds_made" in status["verdict"]["need_photo"]
    # No problem cards for never-photographed items.
    assert "master-bedroom.beds_made" not in status["cards"]
    room = next(r for r in status["rooms"] if r["id"] == "master-bedroom")
    by_id = {item["id"]: item for item in room["items"]}
    assert by_id["master-bedroom.beds_made"]["status"] == "needs photo"
    assert by_id["master-bedroom.beds_made"]["reason"] == (
        "Skipped by cleaner — not photographed"
    )
    assert by_id["master-bedroom.coverage"]["reason"] == (
        "Skipped by cleaner — not photographed"
    )
    assert status["skipped_areas"][0]["items"] == [
        "Beds made", "No guest belongings left", "Floor clear",
    ]
    # Host page shows the Not ready banner text for this case.
    host = test.get(f"/host?code={_host(test)}").text
    assert "critical items were not photographed (skipped:" in host
    # Cleaner reminder lists the checks (clean page renders skipped_areas).
    clean = test.get(f"/clean?code={code}").text
    assert "a.items || a.checks" in clean


def test_end_round_skips_untaken_shots_and_finish_allows_them():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code, test_mode=True)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    taken_cards = [
        "master-bedroom.beds_made",
        "master-bedroom.no_belongings",
        "master-bedroom.floor_clear",
    ]
    assert status["cards"] == taken_cards
    ended = test.post(f"/api/runs/{run_id}/end-round?code={code}")
    assert ended.status_code == 200, ended.text
    assert len(ended.json()["skipped"]) == 10
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["round_ended"] is True
    assert len(status["skipped_shots"]) == 10
    assert status["cards"] == taken_cards
    refused = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert refused.status_code == 400
    for item_id in taken_cards:
        assert _resolve(test, code, run_id, item_id).status_code == 200
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    # Skipped shots are not done: the run stays needs_input, but the
    # cleaner can still send it to the host (approve uses the confirm).
    assert status["agent_state"] == "needs_input"
    assert status["not_photographed_count"] > 0
    done = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert done.status_code == 200, done.text


def test_api_startup_line_messages():
    class _Ok:
        def list_models(self):
            return {"data": []}

    class _Denied:
        def list_models(self):
            error = MetaClientError("list models: request failed with HTTP 401")
            error.status = 401
            raise error

    class _Down:
        def list_models(self):
            raise MetaClientError("list models: request failed: boom")

    class _Broken:
        def list_models(self):
            raise RuntimeError("boom")

    assert api_startup_line(_Ok()) == "Meta API: OK"
    assert api_startup_line(_Denied()) == (
        "Meta API: key rejected (401) — put a valid MODEL_API_KEY in .env"
    )
    assert api_startup_line(_Down()) == "Meta API: check failed (list models: request failed: boom)"
    assert api_startup_line(_Broken()) == "Meta API: check failed (boom)"


def _example_jpeg(color="blue"):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, "JPEG")
    return buffer.getvalue()


def test_tour_includes_example_url_only_for_shots_with_one():
    from pot import examples as examples_mod

    test, code = _client()
    run_id = _create_run(test, code)
    tour = test.get(f"/api/runs/{run_id}/tour?code={code}").json()
    assert all("example_url" not in shot for shot in tour["shots"])
    examples_mod.save_example(
        "demo-house", "master-bedroom-1", _example_jpeg())
    tour = test.get(f"/api/runs/{run_id}/tour?code={code}").json()
    by_id = {shot["shot_id"]: shot for shot in tour["shots"]}
    assert by_id["master-bedroom-1"]["example_url"] == (
        "/api/properties/demo-house/examples/master-bedroom-1"
    )
    assert "example_url" not in by_id["guest-bedroom-1"]
    image = test.get(
        by_id["master-bedroom-1"]["example_url"] + f"?code={code}")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.content[:2] == b"\xff\xd8"
    missing = test.get(
        "/api/properties/demo-house/examples/guest-bedroom-1"
        f"?code={code}")
    assert missing.status_code == 404


def test_approve_learns_missing_examples_from_proving_photos():
    from pot import config, examples as examples_mod

    test, code = _client()
    run_id = _create_run(test, code)
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    approved = test.post(
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
        json={"decision": "approve", "force": True, "notes": "", "item_ids": []},
    )
    assert approved.status_code == 200, approved.text
    learned = config.EXAMPLES_DIR / "demo-house" / "master-bedroom-1.jpg"
    assert learned.is_file()
    assert not examples_mod.has_example("demo-house", "guest-bedroom-1")
    tour = test.get(f"/api/runs/{run_id}/tour?code={code}").json()
    by_id = {shot["shot_id"]: shot for shot in tour["shots"]}
    assert "example_url" in by_id["master-bedroom-1"]


def test_approve_never_overwrites_existing_examples():
    from pot import config, examples as examples_mod

    test, code = _client()
    run_id = _create_run(test, code)
    examples_mod.save_example(
        "demo-house", "master-bedroom-1", _example_jpeg("red"))
    before = (
        config.EXAMPLES_DIR / "demo-house" / "master-bedroom-1.jpg"
    ).read_bytes()
    _upload(test, code, run_id, "master-bedroom-1")
    _wait_idle(test, code, run_id)
    approved = test.post(
        f"/api/runs/{run_id}/signoff?code={_host(test)}",
        json={"decision": "approve", "force": True, "notes": "", "item_ids": []},
    )
    assert approved.status_code == 200, approved.text
    after = (
        config.EXAMPLES_DIR / "demo-house" / "master-bedroom-1.jpg"
    ).read_bytes()
    assert after == before


def test_progress_counts_skipped_shots_as_done():
    test, code = _client()
    run_id = _create_run(test, code, test_mode=True)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    assert status["progress"]["shots_taken"] == 1
    skipped = test.post(
        f"/api/runs/{run_id}/skip-shot?code={code}",
        json={"shot_id": "guest-bedroom-1"},
    )
    assert skipped.status_code == 200, skipped.text
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["progress"]["shots_taken"] == 2
    assert status["progress"]["shots_total"] == 11


def test_skip_shot_skips_cards_and_finish_allows_them():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code, test_mode=True)
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    assert len(status["cards"]) == 3
    assert test.post(
        f"/api/runs/{run_id}/skip-shot?code={code}",
        json={"shot_id": "nope-nothing"},
    ).status_code == 400
    assert test.post(
        f"/api/runs/{run_id}/skip-shot?code={code}",
        json={"shot_id": "master-bedroom-1"},
    ).status_code == 400
    tour = test.get(f"/api/runs/{run_id}/tour?code={code}").json()
    others = [shot["shot_id"] for shot in tour["shots"]
              if shot["shot_id"] != "master-bedroom-1"]
    assert len(others) == 10
    for shot_id in others:
        skipped = test.post(
            f"/api/runs/{run_id}/skip-shot?code={code}",
            json={"shot_id": shot_id},
        )
        assert skipped.status_code == 200, (shot_id, skipped.text)
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert len(status["skipped_shots"]) == 10
    assert len(status["cards"]) == 3
    for item_id in list(status["cards"]):
        assert _resolve(test, code, run_id, item_id).status_code == 200
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert status["agent_state"] == "needs_input"
    done = test.post(f"/api/runs/{run_id}/finish?code={code}")
    assert done.status_code == 200, done.text


def test_skip_room_skips_every_untaken_shot_of_the_room():
    test, code = _client(inspector=_problem, verifier=_not_done)
    run_id = _create_run(test, code, test_mode=True)
    assert test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "nope-nothing"},
    ).status_code == 400
    _upload(test, code, run_id, "master-bedroom-1")
    status = _wait_idle(test, code, run_id)
    assert len(status["cards"]) == 3
    skipped = test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bathroom"},
    )
    assert skipped.status_code == 200, skipped.text
    assert sorted(skipped.json()["skipped"]) == [
        "master-bathroom-1", "master-bathroom-2", "master-bathroom-3"]
    taken = test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bedroom"},
    )
    assert taken.status_code == 200
    assert taken.json()["skipped"] == []
    repeat = test.post(
        f"/api/runs/{run_id}/skip-room?code={code}",
        json={"room_id": "master-bathroom"},
    )
    assert repeat.status_code == 200
    assert repeat.json()["skipped"] == []
    status = test.get(f"/api/runs/{run_id}/status?code={code}").json()
    assert len(status["cards"]) == 3
    for shot_id in ("master-bathroom-1", "master-bathroom-2",
                    "master-bathroom-3"):
        assert shot_id in status["skipped_shots"]
    assert "master-bedroom-1" not in status["skipped_shots"]
