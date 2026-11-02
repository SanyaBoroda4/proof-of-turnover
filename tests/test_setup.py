"""Setup backend tests: pot.setup.py + /api/setups endpoints (fake client)."""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pot import setup as setup_mod
from pot.server import create_app
from tests.fakes import FakeSpark, tone_clip


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr("pot.config.SETUPS_DIR", tmp_path / "setups")
    monkeypatch.setattr("pot.config.PROPERTIES_DIR", tmp_path / "properties")
    return tmp_path


@pytest.fixture
def client():
    test = TestClient(create_app(FakeSpark()))
    return test, test.app.state.server.code


def _jpeg(color="green"):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, "JPEG")
    return buffer.getvalue()


ANSWERS = {
    "name": "Setup test",
    "floors": 2,
    "bedrooms": 2,
    "full_baths": 1,
    "half_baths": 1,
    "kitchen": True,
    "living_room": True,
    "dining_room": False,
    "laundry": True,
    "back_porch": True,
    "front_porch": False,
    "language": "en",
}


def _create(test, code, answers=None):
    response = test.post(
        f"/api/setups?code={code}", json=answers or dict(ANSWERS))
    assert response.status_code == 200, response.text
    return response.json()


def test_room_list_from_answers(dirs, client):
    test, code = client
    created = _create(test, code)
    rooms = created["rooms"]
    assert [r["id"] for r in rooms] == [
        "kitchen", "living-room", "half-bathroom", "laundry", "back-porch",
        "bedroom-1", "bedroom-2", "bathroom",
    ]
    by_id = {r["id"]: r for r in rooms}
    assert by_id["kitchen"]["type"] == "kitchen"
    assert by_id["half-bathroom"]["type"] == "half_bathroom"
    assert by_id["bedroom-1"]["name"] == "Bedroom 1"
    assert by_id["bathroom"]["name"] == "Bathroom"  # single: no number
    assert by_id["bedroom-1"]["floor"] == 2
    assert by_id["bathroom"]["floor"] == 2
    assert by_id["kitchen"]["floor"] == 1
    assert all(r["status"] == "new" and r["draft"] is None for r in rooms)
    assert created["setup_id"].startswith("setup-")
    get = test.get(f"/api/setups/{created['setup_id']}?code={code}").json()
    assert get["name"] == "Setup test" and len(get["rooms"]) == 8


def test_single_floors_and_empty_name_rejected(dirs, client):
    test, code = client
    one = dict(ANSWERS, floors=1, bedrooms=1)
    rooms = _create(test, code, one)["rooms"]
    assert [r["id"] for r in rooms if r["type"] == "bedroom"] == ["bedroom"]
    assert all(r["floor"] == 1 for r in rooms)
    bad = dict(ANSWERS, name="  ")
    assert test.post("/api/setups?code=" + code, json=bad).status_code == 400
    bad = dict(ANSWERS, floors=9)
    assert test.post("/api/setups?code=" + code, json=bad).status_code == 400
    bad = dict(ANSWERS, language="fr")
    assert test.post("/api/setups?code=" + code, json=bad).status_code == 400
    none = dict(ANSWERS, bedrooms=0, full_baths=0, half_baths=0,
                kitchen=False, living_room=False, laundry=False, back_porch=False)
    response = test.post("/api/setups?code=" + code, json=none)
    assert response.status_code == 400


def test_add_and_skip_room(dirs, client):
    test, code = client
    sid = _create(test, code)["setup_id"]
    added = test.post(
        f"/api/setups/{sid}/rooms?code={code}",
        json={"type": "office", "name": "Study", "floor": 2},
    )
    assert added.status_code == 200
    assert added.json()["id"] == "study"
    again = test.post(
        f"/api/setups/{sid}/rooms?code={code}",
        json={"type": "office", "name": "Study", "floor": 1},
    )
    assert again.json()["id"] == "study-2"
    bad = test.post(
        f"/api/setups/{sid}/rooms?code={code}",
        json={"type": "pool", "name": "Pool", "floor": 1},
    )
    assert bad.status_code == 400
    skipped = test.put(
        f"/api/setups/{sid}/rooms/study?code={code}", json={"skipped": True})
    assert skipped.json()["status"] == "skipped"
    back = test.put(
        f"/api/setups/{sid}/rooms/study?code={code}",
        json={"skipped": False, "name": "Study room"})
    assert back.json()["status"] == "new"
    assert back.json()["name"] == "Study room"


def test_access_code_and_ids_on_every_setup_route(dirs, client):
    test, code = client
    sid = _create(test, code)["setup_id"]
    bare = TestClient(create_app(FakeSpark()))  # fresh jar: no auth cookie
    no_code = [
        ("POST", "/api/setups"),
        ("GET", f"/api/setups/{sid}"),
        ("PUT", f"/api/setups/{sid}/rooms/kitchen"),
        ("POST", f"/api/setups/{sid}/rooms"),
        ("POST", f"/api/setups/{sid}/rooms/kitchen/photos"),
        ("DELETE", f"/api/setups/{sid}/rooms/kitchen/photos/1"),
        ("GET", f"/api/setups/{sid}/rooms/kitchen/photos/1"),
        ("POST", f"/api/setups/{sid}/rooms/kitchen/voice"),
        ("POST", f"/api/setups/{sid}/rooms/kitchen/draft"),
        ("POST", f"/api/setups/{sid}/save"),
        ("POST", f"/api/setups/{sid}/tour"),
        ("GET", "/setup"),
    ]
    for method, path in no_code:
        response = bare.request(method, path)
        assert response.status_code == 401, (method, path)
    assert test.get("/setup?code=" + code).status_code == 200
    assert test.get("/api/setups/nope?code=" + code).status_code == 400
    assert test.get(
        f"/api/setups/setup-20200101-000000?code={code}").status_code == 404
    assert test.put(
        f"/api/setups/{sid}/rooms/nope?code={code}",
        json={"name": "x"}).status_code == 404
    assert test.put(
        f"/api/setups/{sid}/rooms/..%5Cwin?code={code}",
        json={"name": "x"}).status_code in (400, 404)


def _draft_answer(**overrides):
    answer = {
        "description": "white kitchen with a gold paper towel holder",
        "beds": [],
        "towels": 0,
        "items": [
            {"key": "fridge_empty", "label": "Fridge empty",
             "requirement": "Fridge doors open for the photo: shelves empty.",
             "sam_prompt": None, "min_count": None,
             "problem_area": "refrigerator", "critical": True, "source": "rule"},
        ],
        "unclear_rules": [],
    }
    answer.update(overrides)
    return answer


def _photo(test, code, sid, room, color="green"):
    return test.post(
        f"/api/setups/{sid}/rooms/{room}/photos?code={code}",
        files={"image": ("room.jpg", _jpeg(color), "image/jpeg")},
    )


def test_photos_capped_and_non_images_refused(dirs, client):
    test, code = client
    sid = _create(test, code)["setup_id"]
    for _ in range(3):
        assert _photo(test, code, sid, "kitchen").status_code == 200
    assert _photo(test, code, sid, "kitchen").status_code == 400
    bad = test.post(
        f"/api/setups/{sid}/rooms/laundry/photos?code={code}",
        files={"image": ("notes.txt", b"not an image", "text/plain")},
    )
    assert bad.status_code == 400
    shot = test.get(f"/api/setups/{sid}/rooms/kitchen/photos/1?code={code}")
    assert shot.status_code == 200
    assert shot.headers["content-type"] == "image/jpeg"
    assert shot.content[:2] == b"\xff\xd8"
    assert test.get(
        f"/api/setups/{sid}/rooms/kitchen/photos/9?code={code}").status_code == 404
    dropped = test.delete(f"/api/setups/{sid}/rooms/kitchen/photos/2?code={code}")
    assert dropped.json()["photos"] == [1, 3]
    fourth = _photo(test, code, sid, "kitchen")
    assert fourth.status_code == 200
    assert fourth.json() == {"n": 4}  # numbers never reused after delete


def test_draft_needs_a_photo_and_stores_fixup(dirs, client):
    test = TestClient(create_app(FakeSpark(drafter=lambda call: _draft_answer())))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert test.post(
        f"/api/setups/{sid}/rooms/kitchen/draft?code={code}").status_code == 400
    assert _photo(test, code, sid, "kitchen").status_code == 200
    draft = test.post(f"/api/setups/{sid}/rooms/kitchen/draft?code={code}")
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["items"][0]["key"] == "fridge_empty"
    assert body["items"][0]["problem_area"] == "refrigerator"
    room = test.get(f"/api/setups/{sid}?code={code}").json()["rooms"][0]
    assert room["id"] == "kitchen" and room["status"] == "ready"


def test_draft_uses_configured_model_and_effort(dirs, monkeypatch):
    from pot import config

    monkeypatch.setattr(config, "SETUP_MODEL", "muse-wonder-1.0")
    monkeypatch.setattr(config, "SETUP_EFFORT", "minimal")
    test = TestClient(create_app(FakeSpark(drafter=lambda call: _draft_answer())))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert _photo(test, code, sid, "kitchen").status_code == 200
    draft = test.post(f"/api/setups/{sid}/rooms/kitchen/draft?code={code}")
    assert draft.status_code == 200, draft.text
    call = [c for c in test.app.state.server.client.calls
            if c["purpose"].startswith("setup draft")][-1]
    assert call["model"] == "muse-wonder-1.0"
    assert call["effort"] == "minimal"


def test_fixup_keys_texts_and_caps():
    room = {"id": "kitchen", "type": "kitchen", "name": "Kitchen"}
    items = [
        {"key": "Bad Key!", "label": "L" * 80, "requirement": "R" * 400,
         "sam_prompt": "a quite long noun phrase here", "min_count": 3,
         "problem_area": "the whole big refrigerator door area",
         "critical": True, "source": "rule"},
        {"key": "bad key!", "label": "Second", "requirement": "Do it.",
         "sam_prompt": "paper towel roll", "min_count": 99,
         "problem_area": "shelf", "critical": False, "source": "rule"},
        {"key": "coverage", "label": "Third", "requirement": "Do that.",
         "sam_prompt": None, "min_count": None, "problem_area": None,
         "critical": True, "source": "rule"},
    ]
    photos = [
        {"key": f"photo_{n}", "label": f"P{n}", "requirement": "Set out.",
         "sam_prompt": None, "min_count": None, "problem_area": None,
         "critical": True, "source": "photo"}
        for n in range(5)
    ]
    draft = setup_mod.fixup_draft(
        _draft_answer(items=items + photos, description=""), room)
    assert draft["description"] == "Kitchen"  # empty description falls back
    keys = [item["key"] for item in draft["items"]]
    assert keys[:3] == ["bad_key", "bad_key_2", "third"]  # coverage renamed via label
    assert len(draft["items"]) == 3 + 3  # all rules kept, photos capped at 3
    first = draft["items"][0]
    assert len(first["label"]) == 60 and len(first["requirement"]) == 300
    assert first["sam_prompt"] is None and first["min_count"] is None
    assert first["problem_area"] is None
    second = draft["items"][1]
    assert second["sam_prompt"] == "paper towel roll"
    assert second["min_count"] is None  # 99 out of range
    assert second["problem_area"] == "shelf"
    many = setup_mod.fixup_draft(
        _draft_answer(items=[
            {"key": f"rule_{n}", "label": f"R{n}", "requirement": "Do it.",
             "sam_prompt": None, "min_count": None, "problem_area": None,
             "critical": True, "source": "rule"}
            for n in range(15)
        ]), room)
    assert len(many["items"]) == 12
    worst = setup_mod.fixup_draft(
        _draft_answer(items=[
            {"key": "coverage", "label": "Coverage", "requirement": "All.",
             "sam_prompt": None, "min_count": None, "problem_area": None,
             "critical": True, "source": "rule"},
        ]), room)
    assert worst["items"][0]["key"] not in ("coverage", "")
    assert "coverage" not in [item["key"] for item in draft["items"]]


def test_fixup_beds_and_towels_fallbacks():
    bed = {"id": "bedroom-1", "type": "bedroom", "name": "Bedroom 1"}
    assert setup_mod.fixup_draft(_draft_answer(), bed)["beds"] == ["bed"]
    assert setup_mod.fixup_draft(
        _draft_answer(beds=["king bed", "", "twin bed"]), bed)["beds"] == [
        "king bed", "twin bed"]
    bath = {"id": "bathroom", "type": "bathroom", "name": "Bathroom"}
    assert setup_mod.fixup_draft(_draft_answer(), bath)["towels"] == 2
    assert setup_mod.fixup_draft(_draft_answer(towels=4), bath)["towels"] == 4
    kitchen = {"id": "kitchen", "type": "kitchen", "name": "Kitchen"}
    fixed = setup_mod.fixup_draft(_draft_answer(beds=["bed"], towels=4), kitchen)
    assert fixed["beds"] == [] and fixed["towels"] == 0


def test_voice_no_speech_english_and_spanish(dirs, tmp_path, client):
    test, code = client
    sid = _create(test, code)["setup_id"]
    blob = tone_clip(tmp_path / "rule.webm").read_bytes()

    quiet = TestClient(create_app(FakeSpark(
        transcript=lambda call: {"transcript": "", "turns": []})))
    hush = quiet.app.state.server.code
    qid = _create(quiet, hush)["setup_id"]
    silent = quiet.post(
        f"/api/setups/{qid}/rooms/kitchen/voice?code={hush}",
        files={"audio": ("rule.webm", blob, "audio/webm")},
    )
    assert silent.json() == {"status": "no_speech"}
    assert len(quiet.app.state.server.client.transcribe_calls) == 1

    heard = test.post(
        f"/api/setups/{sid}/rooms/kitchen/voice?code={code}",
        files={"audio": ("rule.webm", blob, "audio/webm")},
    )
    assert heard.json() == {"status": "ok", "transcript": "se acabó el café",
                            "english": "se acabó el café"}  # en: no extra call
    fake = test.app.state.server.client
    assert fake.transcribe_calls[-1]["languages"] == ["en"]
    assert fake.calls == []

    spanish = TestClient(create_app(FakeSpark()))
    scode = spanish.app.state.server.code
    esid = _create(spanish, scode, dict(ANSWERS, language="es"))["setup_id"]
    translated = spanish.post(
        f"/api/setups/{esid}/rooms/kitchen/voice?code={scode}",
        files={"audio": ("rule.webm", blob, "audio/webm")},
    )
    assert translated.json()["english"] == "EN: se acabó el café"
    sfake = spanish.app.state.server.client
    assert len(sfake.calls) == 1
    assert sfake.calls[0]["purpose"].startswith("setup rule ")
    assert sfake.transcribe_calls[-1]["languages"] == ["es"]


def _drafting_client():
    def draft_each(call):
        return _draft_answer(items=[
            {"key": "custom_check", "label": "Custom check",
             "requirement": "Host rule shown done in the photo.",
             "sam_prompt": None, "min_count": None, "problem_area": None,
             "critical": True, "source": "rule"},
        ])

    return FakeSpark(drafter=draft_each, tour=lambda call: {"shots": []})


def _ready_setup(test, code, skip=()):
    sid = _create(test, code)["setup_id"]
    setup = test.get(f"/api/setups/{sid}?code={code}").json()
    for room in setup["rooms"]:
        if room["id"] in skip:
            test.put(f"/api/setups/{sid}/rooms/{room['id']}?code={code}",
                     json={"skipped": True})
            continue
        assert _photo(test, code, sid, room["id"]).status_code == 200
        draft = test.post(f"/api/setups/{sid}/rooms/{room['id']}/draft?code={code}")
        assert draft.status_code == 200, (room["id"], draft.text)
    return sid


def test_save_writes_property_and_tour(dirs):
    from pot.checklist import build_checklist, load_property

    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    sid = _ready_setup(test, code, skip=("laundry",))
    saved = test.post(f"/api/setups/{sid}/save?code={code}")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["property_id"] == "setup-test"
    assert body["rooms"] == 7 and body["items"] > 7 and body["shots"] > 0
    prop_path = dirs / "properties" / "setup-test" / "property.json"
    assert prop_path.is_file()
    assert (dirs / "properties" / "setup-test" / "tour.json").is_file()
    property = load_property("setup-test")
    checklist = build_checklist(property)
    assert len(checklist) == body["items"]
    assert "laundry" not in {room["id"] for room in property["rooms"]}
    names = [p["name"] for p in
             test.get("/api/properties?code=" + code).json()["properties"]]
    assert "Setup test" in names
    calls_path = dirs / "setups" / sid / "calls.jsonl"
    assert calls_path.is_file()
    assert "setup draft" in calls_path.read_text(encoding="utf-8")


def test_save_writes_room_photo_as_shot_examples(dirs):
    from pot import config

    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    setup_id = _ready_setup(test, code)
    saved = test.post(f"/api/setups/{setup_id}/save?code={code}")
    assert saved.status_code == 200, saved.text
    property_id = saved.json()["property_id"]
    shots = json.loads(
        (config.PROPERTIES_DIR / property_id / "tour.json")
        .read_text(encoding="utf-8")
    )["shots"]
    assert shots
    for shot in shots:
        path = (config.EXAMPLES_DIR / property_id
                / f"{shot['shot_id']}.jpg")
        assert path.is_file(), shot["shot_id"]


def test_save_needs_ready_rooms_and_unique_id(dirs):
    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    response = test.post(f"/api/setups/{sid}/save?code={code}")
    assert response.status_code == 400
    assert "Kitchen" in response.json()["detail"]
    sid2 = _ready_setup(test, code)
    test.post(f"/api/setups/{sid2}/save?code={code}")
    sid3 = _ready_setup(test, code)
    again = test.post(f"/api/setups/{sid3}/save?code={code}").json()
    assert again["property_id"] == "setup-test-2"


def test_standard_key_replaces_standard_item(dirs):
    from pot.checklist import build_checklist, load_property

    def draft_toilet(call):
        return _draft_answer(items=[
            {"key": "toilet", "label": "Toilet lid open",
             "requirement": "Toilet lid open for the photo, bowl clean.",
             "sam_prompt": "toilet", "min_count": 1, "problem_area": None,
             "critical": True, "source": "rule"},
        ])

    test = TestClient(create_app(
        FakeSpark(drafter=draft_toilet, tour=lambda call: {"shots": []})))
    code = test.app.state.server.code
    sid = _create(test, code, dict(
        ANSWERS, name="Toilet house", bedrooms=0, full_baths=0, half_baths=1,
        kitchen=False, living_room=False, laundry=False, back_porch=False,
    ))["setup_id"]
    assert _photo(test, code, sid, "half-bathroom").status_code == 200
    assert test.post(
        f"/api/setups/{sid}/rooms/half-bathroom/draft?code={code}").status_code == 200
    assert test.post(f"/api/setups/{sid}/save?code={code}").status_code == 200
    items = {item.id: item for item in
             build_checklist(load_property("toilet-house"))}
    assert items["half-bathroom.toilet"].requirement == \
        "Toilet lid open for the photo, bowl clean."


def test_tour_failure_keeps_property_and_retries(dirs):
    from pot.meta_client import MetaClientError

    def boom(call):
        raise MetaClientError("tour: boom")

    test = TestClient(create_app(FakeSpark(
        drafter=lambda call: _draft_answer(), tour=boom)))
    code = test.app.state.server.code
    sid = _ready_setup(test, code)
    saved = test.post(f"/api/setups/{sid}/save?code={code}").json()
    assert saved["property_id"] == "setup-test"
    assert "tour_error" in saved
    assert (dirs / "properties" / "setup-test" / "property.json").is_file()
    assert not (dirs / "properties" / "setup-test" / "tour.json").exists()
    test.app.state.server.client._tour = lambda call: {"shots": []}
    retried = test.post(f"/api/setups/{sid}/tour?code={code}").json()
    assert retried == {"property_id": "setup-test", "shots": retried["shots"]}
    assert retried["shots"] > 0
    assert (dirs / "properties" / "setup-test" / "tour.json").is_file()


def _kitchen(test, code, sid):
    setup = test.get(f"/api/setups/{sid}?code={code}").json()
    rooms = {room["id"]: room for room in setup["rooms"]}
    return rooms["kitchen"]


def test_rules_change_marks_stale_and_blocks_save(dirs):
    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    sid = _ready_setup(test, code)
    assert _kitchen(test, code, sid)["stale"] is False
    put = test.put(
        f"/api/setups/{sid}/rooms/kitchen?code={code}", json={"rules": "New rule."}
    )
    assert put.status_code == 200
    assert _kitchen(test, code, sid)["stale"] is True
    refused = test.post(f"/api/setups/{sid}/save?code={code}")
    assert refused.status_code == 400
    name = _kitchen(test, code, sid)["name"]
    assert name in refused.json()["detail"]
    drafted = test.post(f"/api/setups/{sid}/rooms/kitchen/draft?code={code}")
    assert drafted.status_code == 200
    assert _kitchen(test, code, sid)["stale"] is False
    saved = test.post(f"/api/setups/{sid}/save?code={code}")
    assert saved.status_code == 200


def test_same_rules_and_photo_tweaks_set_stale(dirs):
    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert _photo(test, code, sid, "kitchen").status_code == 200
    test.put(f"/api/setups/{sid}/rooms/kitchen?code={code}", json={"rules": "Same."})
    assert (
        test.post(f"/api/setups/{sid}/rooms/kitchen/draft?code={code}").status_code
        == 200
    )
    # Saving the same rules again is not a change.
    test.put(f"/api/setups/{sid}/rooms/kitchen?code={code}", json={"rules": "Same."})
    assert _kitchen(test, code, sid)["stale"] is False
    assert (
        test.delete(f"/api/setups/{sid}/rooms/kitchen/photos/1?code={code}").status_code
        == 200
    )
    assert _kitchen(test, code, sid)["stale"] is True


def test_save_twice_reuses_property_id(dirs):
    test = TestClient(create_app(_drafting_client()))
    code = test.app.state.server.code
    sid = _ready_setup(test, code)
    first = test.post(f"/api/setups/{sid}/save?code={code}").json()
    second = test.post(f"/api/setups/{sid}/save?code={code}").json()
    assert second["property_id"] == first["property_id"]
    assert [p.name for p in (dirs / "properties").iterdir()] == [
        first["property_id"]]


def test_second_save_while_saving_returns_409(dirs):
    import threading

    entered = threading.Event()
    release = threading.Event()

    def slow_tour(call):
        entered.set()
        assert release.wait(30)
        return {"shots": []}

    test = TestClient(create_app(FakeSpark(
        drafter=lambda call: _draft_answer(), tour=slow_tour)))
    code = test.app.state.server.code
    sid = _ready_setup(test, code)
    outcome = {}

    def save_in_background():
        outcome["status"] = test.post(f"/api/setups/{sid}/save?code={code}").status_code

    worker = threading.Thread(target=save_in_background)
    worker.start()
    assert entered.wait(30)
    busy = test.post(f"/api/setups/{sid}/tour?code={code}")
    assert busy.status_code == 409
    release.set()
    worker.join(30)
    assert outcome["status"] == 200


def _blocking_draft_client(entered, release):
    def slow_draft(call):
        entered.set()
        assert release.wait(30)
        return _draft_answer()

    return create_app(FakeSpark(drafter=slow_draft))


def test_get_answers_while_draft_is_blocked(dirs):
    import threading

    entered = threading.Event()
    release = threading.Event()
    test = TestClient(_blocking_draft_client(entered, release))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert _photo(test, code, sid, "kitchen").status_code == 200
    outcome = {}

    def draft_in_background():
        outcome["status"] = test.post(
            f"/api/setups/{sid}/rooms/kitchen/draft?code={code}"
        ).status_code

    worker = threading.Thread(target=draft_in_background)
    worker.start()
    assert entered.wait(30)
    # The drafter holds no lock: this GET answers while it is blocked.
    assert test.get(f"/api/setups/{sid}?code={code}").status_code == 200
    release.set()
    worker.join(30)
    assert outcome["status"] == 200


def test_mid_draft_room_delete_drops_result(dirs):
    import threading

    entered = threading.Event()
    release = threading.Event()
    test = TestClient(_blocking_draft_client(entered, release))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert _photo(test, code, sid, "kitchen").status_code == 200
    outcome = {}

    def draft_in_background():
        outcome["status"] = test.post(
            f"/api/setups/{sid}/rooms/kitchen/draft?code={code}"
        ).status_code

    worker = threading.Thread(target=draft_in_background)
    worker.start()
    assert entered.wait(30)
    path = dirs / "setups" / sid / "setup.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rooms"] = [room for room in data["rooms"] if room["id"] != "kitchen"]
    path.write_text(json.dumps(data), encoding="utf-8")
    release.set()
    worker.join(30)
    assert outcome["status"] == 200
    setup = test.get(f"/api/setups/{sid}?code={code}").json()
    assert "kitchen" not in {room["id"] for room in setup["rooms"]}


def test_mid_draft_rule_change_marks_stale(dirs):
    import threading

    entered = threading.Event()
    release = threading.Event()
    test = TestClient(_blocking_draft_client(entered, release))
    code = test.app.state.server.code
    sid = _create(test, code)["setup_id"]
    assert _photo(test, code, sid, "kitchen").status_code == 200
    outcome = {}

    def draft_in_background():
        outcome["status"] = test.post(
            f"/api/setups/{sid}/rooms/kitchen/draft?code={code}"
        ).status_code

    worker = threading.Thread(target=draft_in_background)
    worker.start()
    assert entered.wait(30)
    put = test.put(
        f"/api/setups/{sid}/rooms/kitchen?code={code}", json={"rules": "Changed."}
    )
    assert put.status_code == 200
    release.set()
    worker.join(30)
    assert outcome["status"] == 200
    room = _kitchen(test, code, sid)
    assert room["status"] == "ready"
    assert room["stale"] is True


def test_fixup_drops_photo_items_with_standard_keys():
    room = {"id": "kitchen", "type": "kitchen", "name": "Kitchen"}
    draft = _draft_answer(items=[
        {"key": "counters", "label": "Counters",
         "requirement": "Repeat of the standard check.",
         "sam_prompt": None, "min_count": None, "problem_area": None,
         "critical": True, "source": "photo"},
        {"key": "counters", "label": "Counters",
         "requirement": "The host's own wording replaces it.",
         "sam_prompt": None, "min_count": None, "problem_area": None,
         "critical": True, "source": "rule"},
    ])
    fixed = setup_mod.fixup_draft(draft, room)
    assert [item["source"] for item in fixed["items"]] == ["rule"]
    assert fixed["items"][0]["requirement"] == "The host's own wording replaces it."


def test_setup_draft_prompt_states_counting_and_photo_rules():
    from pot import prompts

    text = prompts.setup_draft_instructions(
        "Half bathroom", "half_bathroom", "spare roll",
        [{"key": "toilet_paper", "label": "Toilet paper",
          "requirement": "Toilet paper on the holder."}],
    )
    assert "including the ones already in place" in text
    assert "min_count 2" in text
    assert "Never cups, used toiletries" in text
