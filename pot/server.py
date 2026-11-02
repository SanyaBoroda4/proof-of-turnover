"""Live tour web server: JSON API for the cleaner and host pages.

Run: python -m pot.server [--host 127.0.0.1] [--port 8000]

Every page and API route needs the access code (POT_ACCESS_CODE, or a
random one made at startup): opening a link with ?code=... once sets a
cookie. Localhost is not exempt: tunnel traffic also arrives locally.
"""

import argparse
import base64
import io
import json
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import qrcode
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel

from pot import config, examples as examples_mod, prompts, voice as voice_mod
from pot.checklist import PropertyError, build_checklist, load_property
from pot.meta_client import MetaClient, MetaClientError, call_context
from pot import setup as setup_mod
from pot.notes import MAX_NOTE_BYTES, NotesError, add_voice_note
from pot.photos import new_run_dir
from pot.proof import PROVEN
from pot.recheck import run_photo_checks
from pot.requests import write_requests
from pot.runs import OldPhotoError, RunState
from pot.tour import fixup_tour, generate_tour, load_tour

COOKIE_NAME = "pot_code"
ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
RUN_NAME_PATTERN = re.compile(r"^run-(\d{8})-(\d{6})(?:_(\d+))?$")
LONG_CACHE = {"Cache-Control": "public, max-age=31536000"}


def _checked_id(value, what):
    """Ids for URLs: letters, digits, dot, dash, underscore only.

    On Windows a backslash in an id is a folder separator, so anything
    else is rejected before it ever reaches a path.
    """
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise HTTPException(400, f"Invalid {what} {value!r}")
    return value


def latest_run_id():
    """Newest run folder by timestamp, without loading any run."""
    best, best_key = None, None
    if config.RUNS_DIR.is_dir():
        for path in config.RUNS_DIR.iterdir():
            match = RUN_NAME_PATTERN.match(path.name)
            if match is None or not (path / "first_look.json").is_file():
                continue
            key = (match.group(1) + match.group(2), int(match.group(3) or 0))
            if best_key is None or key > best_key:
                best, best_key = path.name, key
    return best


def startup_lines(port, code, host_code=None):
    """What main() prints: host/cleaner links plus the tunnel recipe."""
    return [
        f"Host:    http://127.0.0.1:{port}/host?code={host_code or code}",
        f"Cleaner: http://{local_ip()}:{port}/clean?code={code} "
        f"(start with --host 0.0.0.0, same Wi-Fi as the phone)",
        f"Or keep 127.0.0.1 and run: cloudflared tunnel --url http://localhost:{port}",
        f"   then open https://<tunnel-url>/clean?code={code} on the phone.",
    ]


def access_code():
    """The cleaner access code: POT_ACCESS_CODE, or random at startup."""
    return os.environ.get("POT_ACCESS_CODE", "") or secrets.token_urlsafe(9)


def host_access_code():
    """The host access code: POT_HOST_CODE, or random at startup."""
    return os.environ.get("POT_HOST_CODE", "") or secrets.token_urlsafe(9)


def api_startup_line(client=None, data=None, error=None):
    """One line about the Meta API key: OK, or rejected/unreachable.

    Never includes the key itself. Pass a fetched models list (or its
    error) to avoid calling list_models() again.
    """
    if error is None and data is None:
        try:
            data = client.list_models()
        except Exception as exc:  # noqa: BLE001 - startup must report
            error = exc
    if error is not None:
        exc = error
        status = getattr(exc, "status", None)
        if status in (401, 403):
            return (
                f"Meta API: key rejected ({status}) — "
                "put a valid MODEL_API_KEY in .env"
            )
        return f"Meta API: check failed ({exc})"
    return "Meta API: OK"


def wonder_startup_line(client=None, data=None, error=None):
    """(line, available): is WONDER_MODEL offered for this key?"""
    if error is not None:
        return "Muse Wonder: check failed (second opinions off)", False
    if data is None:
        try:
            data = client.list_models()
        except Exception:  # noqa: BLE001 - one line, never fatal
            return "Muse Wonder: check failed (second opinions off)", False
    ids = [entry.get("id") for entry in (data or {}).get("data", [])
           if isinstance(entry, dict)]
    if config.WONDER_MODEL in ids:
        return "Muse Wonder: OK", True
    return "Muse Wonder: not available for this key (second opinions off)", False


def startup_check_lines(client):
    """(meta line, wonder line, wonder available) from one models fetch."""
    try:
        data = client.list_models()
        error = None
    except Exception as exc:  # noqa: BLE001 - startup must report
        data = None
        error = exc
    wonder_line, wonder_ok = wonder_startup_line(data=data, error=error)
    return api_startup_line(data=data, error=error), wonder_line, wonder_ok


def voice_start_line():
    """One startup line about which voice reads instructions aloud."""
    if voice_mod.neural_off():
        return "Voice: phone voices (neural voice off)"
    return "Voice: Microsoft neural voices (edge-tts)"


WONDER_OPINIONS = ("agree_with_cleaner", "still_a_problem", "unclear")

WONDER_SCHEMA = {
    "type": "object",
    "properties": {
        "opinion": {"type": "string", "enum": list(WONDER_OPINIONS)},
        "reason": {"type": "string"},
    },
    "required": ["opinion", "reason"],
    "additionalProperties": False,
}

WONDER_REASONS = ("broken", "before", "wont_clean", "other")


def _wonder_eligible(action, reason):
    """Second opinion: cleaner says fine, or can't fix for a real reason."""
    if action == "its_fine":
        return True
    return action == "cant_fix" and reason in WONDER_REASONS


def _wonder_snapshot(state, item_id, body):
    with state.lock:
        item = state.items_by_id[item_id]
        proof = state.proofs[item_id]
        claim = state.claims.get(item_id)
        second = state.seconds.get(item_id)
        photo = next(
            (p for p in state.photos if p.id == proof.photo_id), None)
        note = next(
            (n for n in state.voice_notes if n["id"] == body.note_id), None)
        return {
            "photo_id": proof.photo_id,
            "label": item.label,
            "requirement": item.requirement,
            "first_reason": (claim.seen if claim is not None else "")
            or proof.reason,
            "second_reason": second.reason if second is not None else "",
            "action": body.action,
            "reason": body.reason,
            "note_english": (note or {}).get("english", ""),
            "overview_path": str(photo.overview_path) if photo is not None else "",
        }


def _maybe_start_wonder(server, state, item_id, body):
    """Fire-and-forget second opinion; never blocks the cleaner."""
    if not server.wonder or not _wonder_eligible(body.action, body.reason):
        return
    snapshot = _wonder_snapshot(state, item_id, body)
    if not snapshot["photo_id"] or not snapshot["overview_path"]:
        return
    state.mark_wonder_pending(item_id)
    server.executor.submit(_wonder_job, server, state, item_id, snapshot)


def _wonder_job(server, state, item_id, snapshot):
    token = call_context.set({
        "run_dir": str(state.run_dir),
        "photo_id": snapshot["photo_id"],
    })
    try:
        raw = Path(snapshot["overview_path"]).read_bytes()
        text = (
            f"Item: {snapshot['label']}\n"
            f"Requirement: {snapshot['requirement']}\n"
            f"First look: {snapshot['first_reason']}\n"
            f"Second look: {snapshot['second_reason']}\n"
            f"Cleaner answered: {snapshot['action']}"
            + (f" ({snapshot['reason']})" if snapshot["reason"] else "")
            + "\n"
        )
        if snapshot["note_english"]:
            text += f"Cleaner's voice note: {snapshot['note_english']}\n"
        answer = server.client.respond(
            f"second opinion {item_id} {snapshot['photo_id']}",
            [
                {"type": "input_text", "text": text},
                {"type": "input_image",
                 "image_url": "data:image/jpeg;base64,"
                 + base64.b64encode(raw).decode("ascii")},
            ],
            instructions=(
                "You are the senior inspector for vacation-rental "
                "turnovers. A fast checker flagged this item and the "
                "cleaner disagrees or can't fix it. Look at the photo "
                "and tell the host what you see and what to do, in one "
                "or two short sentences."
            ),
            schema=WONDER_SCHEMA,
            model=config.WONDER_MODEL,
            effort=config.WONDER_EFFORT,
        )
        opinion = answer.get("opinion") if isinstance(answer, dict) else None
        if opinion not in WONDER_OPINIONS:
            raise ValueError(f"bad second opinion {answer!r}")
        state.store_wonder(item_id, {
            "status": "done",
            "opinion": opinion,
            "reason": str(answer.get("reason", "")),
            "model": config.WONDER_MODEL,
        })
    except Exception:  # noqa: BLE001 - an opinion never breaks a run
        state.store_wonder(item_id, {"status": "failed"})
    finally:
        call_context.reset(token)


def local_ip():
    """This PC's LAN address (for the cleaner link); 127.0.0.1 as fallback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))  # TEST-NET-1: connect() sends nothing
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class RunCreate(BaseModel):
    property: str
    demo: bool = False
    language: str | None = None


class TourRequest(BaseModel):
    property: str


class SignoffBody(BaseModel):
    decision: str
    notes: str = ""
    item_ids: list = []


class ResolveBody(BaseModel):
    action: str
    reason: str | None = None
    note_id: str | None = None


class SkipShotBody(BaseModel):
    shot_id: str


class SkipRoomBody(BaseModel):
    room_id: str


class VoiceBody(BaseModel):
    text: str
    lang: str


class VoicePrefetchBody(BaseModel):
    lang: str
    texts: list


RESOLVE_ACTIONS = ("cant_fix", "its_fine")
RESOLVE_REASONS = ("broken", "before", "supplies", "wont_clean", "other")


class SetupCreate(BaseModel):
    name: str
    floors: int = 1
    bedrooms: int = 0
    full_baths: int = 0
    half_baths: int = 0
    kitchen: bool = False
    living_room: bool = False
    dining_room: bool = False
    laundry: bool = False
    back_porch: bool = False
    front_porch: bool = False
    language: str = "es"


class RoomUpdate(BaseModel):
    name: str | None = None
    floor: int | None = None
    rules: str | None = None
    beds: list | None = None
    towels: int | None = None
    items: list | None = None
    skipped: bool | None = None


class RoomAdd(BaseModel):
    type: str
    name: str
    floor: int = 1


class ServerState:
    """One server's runs, client and background workers."""

    def __init__(self, client, code, host_code=None):
        self.client = client
        self.code = code
        self.host_code = host_code or host_access_code()
        self.wonder = True
        self.runs = {}
        self.registry_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=8)
        self.calls_lock = threading.Lock()
        if hasattr(client, "add_hook"):
            client.add_hook(self._call_hook)

    def get_state(self, run_id):
        """A run's state, loaded from disk after a restart (None if unknown)."""
        with self.registry_lock:
            state = self.runs.get(run_id)
        if state is not None:
            return state
        run_dir = config.RUNS_DIR / run_id
        if not (run_dir / "first_look.json").is_file():
            return None
        state = RunState.load(run_dir)
        with self.registry_lock:
            return self.runs.setdefault(run_id, state)

    def remember(self, state):
        with self.registry_lock:
            self.runs[state.run_dir.name] = state

    def _call_hook(self, entry):
        """Append one Meta call to its run's calls.jsonl (+ memory)."""
        context = call_context.get() or {}
        run_dir = context.get("run_dir")
        if not run_dir:
            return
        if entry.get("cache") == "hit":
            source = "recorded"
        elif entry.get("ok"):
            source = "live"
        else:
            source = "failed"
        record = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "model": entry.get("model"),
            "purpose": entry.get("purpose"),
            "photo_id": entry.get("photo_id"),
            "seconds": round(entry.get("seconds", 0.0), 2),
            "attempts": entry.get("attempts", 0),
            "source": source,
        }
        usage = _usage_summary(entry.get("usage"))
        if usage is not None:
            record["usage"] = usage
        if entry.get("status") is not None:
            record["status"] = entry["status"]
        with self.calls_lock:
            with open(Path(run_dir) / "calls.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        with self.registry_lock:
            state = self.runs.get(Path(run_dir).name)
        if state is not None:
            state.record_call(record)


def _usage_summary(usage):
    """Token usage as {input, cached, reasoning, output}, or None."""
    if not isinstance(usage, dict):
        return None
    details_in = usage.get("input_tokens_details") or {}
    if not isinstance(details_in, dict):
        details_in = {}
    details_out = usage.get("output_tokens_details") or {}
    if not isinstance(details_out, dict):
        details_out = {}
    summary = {}
    for key, value in [
        ("input", usage.get("input_tokens")),
        ("cached", details_in.get("cached_tokens")),
        ("reasoning", details_out.get("reasoning_tokens")),
        ("output", usage.get("output_tokens")),
    ]:
        if isinstance(value, int):
            summary[key] = value
    return summary or None


def _check_worker(server, state, photo_id):
    """Background check for one uploaded photo (parallel tour mode)."""
    token = call_context.set({
        "run_dir": str(state.run_dir), "photo_id": photo_id, "live_checks": True,
    })
    started = time.monotonic()
    try:
        with state.lock:
            shot_id = state.photo_shot.get(photo_id, "")
            wanted = set()
            for shot in state.tour["shots"]:
                if shot["shot_id"] == shot_id:
                    wanted = set(shot.get("item_ids", []))
            items = [
                state.items_by_id[item_id] for item_id in wanted
                if item_id in state.items_by_id
                and state.items_by_id[item_id].key != "coverage"
                and state.proofs[item_id].status != "proven"
            ]
        state.timeline("checks_started", photo_id=photo_id)
        calls_before = state.call_count()
        fresh = run_photo_checks(
            server.client, state, photo_id, items,
            parallel=True, effort=prompts.RECHECK_EFFORT,
        )
        state.timeline("checks_done", photo_id=photo_id)
        if state.photo_had_api_failure(photo_id, calls_before):
            state.timeline("checks_retry", photo_id=photo_id)
            time.sleep(3)
            retry_before = state.call_count()
            fresh = run_photo_checks(
                server.client, state, photo_id, items,
                parallel=True, effort=prompts.RECHECK_EFFORT,
            )
            if state.photo_had_api_failure(photo_id, retry_before):
                state.mark_unchecked(
                    photo_id,
                    f"Photo {photo_id}: AI check failed, saved for retry",
                )
                return
        fresh["elapsed_seconds"] = round(time.monotonic() - started, 1)
        merged = state.merge_result(photo_id, fresh)
        count = 0
        if merged["failed"]:
            requests, notes = write_requests(
                server.client, state.property, state.checklist, state.proofs,
                effort=prompts.RECHECK_EFFORT, item_ids=set(merged["failed"]),
                language=state.language,
            )
            state.store_requests(requests, notes)
            count = len(requests)
        state.timeline("requests_done", photo_id=photo_id, items=count)
        state.finish_photo(photo_id)
    except Exception as exc:  # noqa: BLE001 - a photo check never kills the server
        state.mark_failed(photo_id, f"Photo {photo_id}: check failed: {exc}")
    finally:
        call_context.reset(token)


def create_app(client=None):
    """Build the FastAPI app (pass a fake client in tests)."""
    server = ServerState(client or MetaClient(), access_code(), host_access_code())
    app = FastAPI(title="Proof of Turnover")
    app.state.server = server

    def need_state(run_id):
        _checked_id(run_id, "run")
        state = server.get_state(run_id)
        if state is None:
            raise HTTPException(404, f"Unknown run '{run_id}'")
        return state

    def _host_only(request):
        """Routes only the host code may use (cleaner code is rejected)."""
        path = request.url.path
        if path in ("/host", "/setup", "/api/qr", "/api/server-ip",
                    "/api/cleaner-code", "/api/tour", "/api/runs/latest"):
            return True
        if path.startswith("/api/setups"):
            return True
        if path == "/api/runs" and request.method == "GET":
            return True
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 4 and parts[:2] == ["api", "runs"]:
            if parts[3] in ("signoff", "calls"):
                return True
            if len(parts) == 6 and parts[3] == "notes" and parts[5] == "audio":
                return True
        return False

    @app.middleware("http")
    async def _auth(request, call_next):
        presented = (request.query_params.get("code")
                     or request.cookies.get(COOKIE_NAME))
        allowed = ((server.host_code,) if _host_only(request)
                   else (server.code, server.host_code))
        if presented and presented in allowed:
            response = await call_next(request)
            if request.query_params.get("code"):
                response.set_cookie(COOKIE_NAME, presented,
                                    httponly=True, samesite="lax")
            return response
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "access code required (open the link with ?code=...)"},
                status_code=401,
            )
        return HTMLResponse(
            "<h1>Access code required</h1>"
            "<p>Open the link with ?code=... to continue.</p>",
            status_code=401,
        )

    @app.post("/api/runs")
    def _create_run(body: RunCreate):
        try:
            property = load_property(body.property)
        except PropertyError as exc:
            raise HTTPException(400, str(exc))
        try:
            saved = load_tour(body.property)
        except PropertyError as exc:
            raise HTTPException(400, str(exc))
        checklist = build_checklist(property)
        shots, notes = fixup_tour(property, checklist, saved)
        if not shots:
            raise HTTPException(400, f"Property '{body.property}': tour has no usable shots")
        language = body.language or property.get("cleaner_language", "es")
        if language not in config.CLEANER_LANGUAGE_CODES:
            raise HTTPException(400, f"Unsupported language {language!r}")
        state = RunState.create(
            new_run_dir(), body.property, tour={"shots": shots}, demo=body.demo,
            language=language,
        )
        server.remember(state)
        return {
            "run_id": state.run_dir.name,
            "started_at": state.started_at.isoformat(timespec="seconds"),
            "shots": len(shots),
            "tour_notes": notes,
            "language": language,
        }

    @app.get("/api/runs/{run_id}/tour")
    def _get_tour(run_id: str):
        state = need_state(run_id)
        status = state.status()
        shots = []
        for shot in state.tour["shots"]:
            entry = dict(shot)
            if examples_mod.has_example(
                state.property["id"], shot["shot_id"]
            ):
                entry["example_url"] = (
                    f"/api/properties/{state.property['id']}"
                    f"/examples/{shot['shot_id']}"
                )
            shots.append(entry)
        return {
            "shots": shots,
            "taken": state.taken_shots(),
            "current_shot": status["current_shot"],
        }

    @app.post("/api/voice")
    def _voice(body: VoiceBody):
        text = (body.text or "").strip()
        if not text or len(text) > 500:
            raise HTTPException(400, "text must be 1-500 characters")
        if body.lang not in config.CLEANER_LANGUAGE_CODES:
            raise HTTPException(400, f"Unsupported language {body.lang!r}")
        try:
            data = voice_mod.synthesize(text, body.lang)
        except Exception as exc:  # noqa: BLE001 - phone voice is the fallback
            raise HTTPException(503, f"voice unavailable: {exc}")
        return Response(data, media_type="audio/mpeg", headers=dict(LONG_CACHE))

    @app.post("/api/voice/prefetch")
    def _voice_prefetch(body: VoicePrefetchBody):
        if body.lang not in config.CLEANER_LANGUAGE_CODES:
            raise HTTPException(400, f"Unsupported language {body.lang!r}")
        if len(body.texts) > 80:
            raise HTTPException(400, "at most 80 texts")
        texts = []
        for text in body.texts:
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                raise HTTPException(400, "each text must be 1-500 characters")
            texts.append(text)
        try:
            queued = voice_mod.prefetch(texts, body.lang)
        except Exception as exc:  # noqa: BLE001 - phone voice is the fallback
            raise HTTPException(503, f"voice unavailable: {exc}")
        return {"queued": queued}

    @app.get("/api/properties/{property_id}/examples/{shot_id}")
    def _example_file(property_id: str, shot_id: str):
        _checked_id(property_id, "property")
        _checked_id(shot_id, "shot")
        blob = examples_mod.read_example(property_id, shot_id)
        if blob is None:
            raise HTTPException(404, f"No example for shot '{shot_id}'")
        return Response(blob, media_type="image/jpeg", headers=dict(LONG_CACHE))

    @app.post("/api/tour")
    def _generate_tour(body: TourRequest):
        try:
            property = load_property(body.property)
        except PropertyError as exc:
            raise HTTPException(400, str(exc))
        try:
            shots, notes = generate_tour(
                server.client, property, build_checklist(property)
            )
        except PropertyError as exc:
            raise HTTPException(400, str(exc))
        return {"shots": shots, "notes": notes}

    @app.post("/api/runs/{run_id}/photos")
    def _upload_photo(run_id: str, shot_id: str = Form(...), image: UploadFile = File(...)):
        state = need_state(run_id)
        _checked_id(shot_id, "shot")
        suffix = Path(image.filename or "upload.jpg").suffix.lower() or ".jpg"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            shutil.copyfileobj(image.file, tmp)
            tmp.close()
            photo, _seq = state.claim_photo(tmp.name, shot_id)
        except OldPhotoError as exc:
            raise HTTPException(400, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        server.executor.submit(_check_worker, server, state, photo.id)
        return {"photo_id": photo.id, "status": "checking"}

    @app.get("/api/runs/{run_id}/status")
    def _get_status(run_id: str):
        return need_state(run_id).status()

    @app.get("/api/runs/{run_id}/calls")
    def _get_calls(run_id: str):
        return {"calls": need_state(run_id).call_log()}

    @app.get("/api/runs/{run_id}/evidence/{item_id}")
    def _get_evidence(run_id: str, item_id: str, card: bool = False):
        _checked_id(item_id, "item")
        blob = need_state(run_id).read_evidence(item_id, card=card)
        if blob is None:
            raise HTTPException(404, f"No evidence for '{item_id}'")
        # Pages add ?v=<photo id>: cache long, retakes change the URL.
        return Response(blob, media_type="image/jpeg", headers=dict(LONG_CACHE))

    @app.get("/api/runs/{run_id}/photos/{photo_id}/overview")
    def _get_overview(run_id: str, photo_id: str):
        _checked_id(photo_id, "photo")
        blob = need_state(run_id).read_overview(photo_id)
        if blob is None:
            raise HTTPException(404, f"No photo '{photo_id}'")
        return Response(blob, media_type="image/jpeg", headers=dict(LONG_CACHE))

    @app.post("/api/runs/{run_id}/notes")
    def _add_note(run_id: str, room_id: str = Form(...), audio: UploadFile = File(...),
                  item_id: str | None = Form(default=None)):
        state = need_state(run_id)
        _checked_id(room_id, "room")
        if item_id is not None:
            _checked_id(item_id, "item")
            if item_id not in state.items_by_id:
                raise HTTPException(400, f"Unknown item '{item_id}'")
        blob = audio.file.read(MAX_NOTE_BYTES + 1)
        token = call_context.set({"run_dir": str(state.run_dir), "photo_id": None})
        try:
            try:
                return add_voice_note(
                    server.client, state, blob, audio.filename or "note",
                    room_id, effort=prompts.RECHECK_EFFORT, item_id=item_id,
                )
            except NotesError as exc:
                raise HTTPException(400, str(exc))
            except MetaClientError as exc:
                raise HTTPException(502, str(exc))
        finally:
            call_context.reset(token)

    @app.get("/api/runs/{run_id}/notes/{note_id}/audio")
    def _get_note_audio(run_id: str, note_id: str):
        _checked_id(note_id, "note")
        blob = need_state(run_id).read_note_audio(note_id)
        if blob is None:
            raise HTTPException(404, f"No audio for '{note_id}'")
        return Response(blob, media_type="audio/wav", headers=dict(LONG_CACHE))

    @app.post("/api/runs/{run_id}/signoff")
    def _signoff(run_id: str, body: SignoffBody):
        state = need_state(run_id)
        if body.decision == "approve":
            signoff = state.approve()
            for shot in state.tour["shots"]:
                if examples_mod.has_example(
                        state.property["id"], shot["shot_id"]) and not (
                        examples_mod.is_temporary(
                            state.property["id"], shot["shot_id"])):
                    continue
                photo = examples_mod.proving_photo_for_shot(state, shot)
                if photo is not None:
                    examples_mod.save_example(
                        state.property["id"], shot["shot_id"],
                        photo.overview_path,
                    )
                    examples_mod.set_temporary(
                        state.property["id"], shot["shot_id"], False)
        elif body.decision == "sendback":
            if not body.item_ids:
                raise HTTPException(400, "Tick at least one item to send back")
            signoff = state.send_back(body.item_ids, body.notes)
            token = call_context.set({"run_dir": str(state.run_dir), "photo_id": None})
            try:
                requests, notes = write_requests(
                    server.client, state.property, state.checklist,
                    state.proofs, effort=prompts.RECHECK_EFFORT,
                    item_ids=set(signoff["items"]), language=state.language,
                )
            finally:
                call_context.reset(token)
            state.store_requests(requests, notes)
        else:
            raise HTTPException(400, f"Unknown decision {body.decision!r}")
        return {"signoff": signoff, "agent_state": state.agent_state()}

    @app.post("/api/runs/{run_id}/items/{item_id}/resolve")
    def _resolve_item(run_id: str, item_id: str, body: ResolveBody):
        state = need_state(run_id)
        _checked_id(item_id, "item")
        item = state.items_by_id.get(item_id)
        if item is None:
            raise HTTPException(404, f"Unknown item '{item_id}'")
        if not item.critical:
            raise HTTPException(400, f"Item '{item_id}' is not critical")
        if state.proofs[item_id].status == PROVEN:
            raise HTTPException(400, f"Item '{item_id}' is already proven")
        if body.action not in RESOLVE_ACTIONS:
            raise HTTPException(400, f"Unknown action {body.action!r}")
        if body.reason is not None and body.reason not in RESOLVE_REASONS:
            raise HTTPException(400, f"Unknown reason {body.reason!r}")
        if body.note_id is not None:
            _checked_id(body.note_id, "note")
            if all(note["id"] != body.note_id for note in state.voice_notes):
                raise HTTPException(400, f"Unknown note '{body.note_id}'")
        resolution = state.resolve_item(
            item_id, body.action, body.reason, body.note_id
        )
        _maybe_start_wonder(server, state, item_id, body)
        return {"resolution": resolution, "agent_state": state.agent_state()}

    @app.post("/api/runs/{run_id}/recheck-unchecked")
    def _recheck_unchecked(run_id: str):
        state = need_state(run_id)
        todo = state.claim_rechecks()
        for photo_id in todo:
            server.executor.submit(_check_worker, server, state, photo_id)
        with state.lock:
            checking = sorted(state.checking)
        return {"rechecking": todo, "checking": checking}

    @app.post("/api/runs/{run_id}/end-round")
    def _end_round(run_id: str):
        state = need_state(run_id)
        skipped = state.end_round()
        return {"round_ended": True, "skipped": skipped}

    @app.post("/api/runs/{run_id}/skip-shot")
    def _skip_shot(run_id: str, body: SkipShotBody):
        state = need_state(run_id)
        _checked_id(body.shot_id, "shot")
        try:
            skipped = state.skip_shot(body.shot_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"skipped": body.shot_id, "skipped_shots": skipped}

    @app.post("/api/runs/{run_id}/skip-room")
    def _skip_room(run_id: str, body: SkipRoomBody):
        state = need_state(run_id)
        _checked_id(body.room_id, "room")
        try:
            skipped = state.skip_room(body.room_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"skipped": skipped}

    @app.post("/api/runs/{run_id}/finish")
    def _finish_run(run_id: str):
        state = need_state(run_id)
        with state.lock:
            checking = sorted(state.checking)
            unchecked = sorted(state.unchecked)
            skipped_only = state._skipped_only_locked()
            open_items = [
                item.id for item in state.checklist
                if item.critical
                and state.proofs[item.id].status != PROVEN
                and item.id not in state.resolutions
                and item.id not in skipped_only
            ]
        if checking:
            raise HTTPException(
                400, f"{len(checking)} photos still checking: "
                + ", ".join(checking))
        if unchecked:
            raise HTTPException(
                400, f"{len(unchecked)} photos could not be checked, "
                "try again: " + ", ".join(unchecked))
        if open_items:
            raise HTTPException(
                400, f"{len(open_items)} items still open: "
                + ", ".join(open_items))
        finished_at = state.finish()
        return {"finished_at": finished_at, "agent_state": state.agent_state()}

    @app.get("/api/qr")
    def _qr(text: str = Query(max_length=2000)):
        image = qrcode.make(text)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png",
                        headers=dict(LONG_CACHE))

    @app.get("/api/cleaner-code")
    def _cleaner_code():
        return {"code": server.code}

    @app.get("/api/server-ip")
    def _server_ip():
        return {"ip": local_ip()}

    @app.get("/api/languages")
    def _languages():
        return {"languages": [
            {"code": code, "name": name} for code, name in config.CLEANER_LANGUAGES
        ]}

    @app.get("/api/properties")
    def _properties():
        found = []
        base = config.PROPERTIES_DIR
        if base.is_dir():
            for path in sorted(base.iterdir()):
                definition = path / "property.json"
                if not definition.is_file():
                    continue
                try:
                    data = json.loads(definition.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                found.append({"id": data.get("id", path.name),
                              "name": data.get("name", path.name)})
        return {"properties": found}

    @app.get("/api/runs/latest")
    def _latest_run():
        return {"run_id": latest_run_id()}

    @app.get("/api/runs")
    def _list_runs():
        found = []
        if config.RUNS_DIR.is_dir():
            for path in sorted(config.RUNS_DIR.iterdir(), reverse=True):
                if not (path / "first_look.json").is_file():
                    continue
                try:
                    state = server.get_state(path.name)
                    status = state.status()
                except (OSError, ValueError):
                    continue
                found.append({"run_id": path.name,
                              "property": status["property"],
                              "started_at": status["started_at"],
                              "agent_state": status["agent_state"]})
        return {"runs": found}

    @app.get("/api/demo-photos")
    def _demo_photos():
        photos = []
        directory = config.DEMO_DIR / "photos"
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg"):
                    photos.append({"name": path.name,
                                   "url": f"/api/demo-photos/{path.name}"})
        return {"photos": photos}

    @app.get("/api/demo-photos/{name}")
    def _demo_photo(name: str):
        if "/" in name or "\\" in name or ".." in name:
            raise HTTPException(404, "No such demo photo")
        path = config.DEMO_DIR / "photos" / name
        if not path.is_file():
            raise HTTPException(404, "No such demo photo")
        return FileResponse(path, media_type="image/jpeg")

    def need_setup(setup_id):
        if not setup_mod.SETUP_ID_PATTERN.match(setup_id or ""):
            raise HTTPException(400, f"Bad setup id {setup_id!r}")
        try:
            return setup_mod.load_setup(setup_id)
        except setup_mod.SetupNotFound as exc:
            raise HTTPException(404, str(exc))

    def _setup_room(setup_id, room_id):
        need_setup(setup_id)
        _checked_id(room_id, "room")

    @app.post("/api/setups")
    def _create_setup(body: SetupCreate):
        try:
            created = setup_mod.create_setup(
                body.name, body.floors, body.bedrooms, body.full_baths,
                body.half_baths, body.kitchen, body.living_room,
                body.dining_room, body.laundry, body.back_porch,
                body.front_porch, body.language,
            )
        except setup_mod.SetupError as exc:
            raise HTTPException(400, str(exc))
        return {"setup_id": created["id"], "rooms": created["rooms"]}

    @app.get("/api/setups/{setup_id}")
    def _get_setup(setup_id: str):
        return need_setup(setup_id)

    @app.put("/api/setups/{setup_id}/rooms/{room_id}")
    def _update_setup_room(setup_id: str, room_id: str, body: RoomUpdate):
        _setup_room(setup_id, room_id)
        edits = {
            key: value for key, value in {
                "name": body.name, "floor": body.floor, "rules": body.rules,
                "beds": body.beds, "towels": body.towels, "items": body.items,
                "skipped": body.skipped,
            }.items() if value is not None
        }
        try:
            return setup_mod.update_room(setup_id, room_id, edits)
        except setup_mod.SetupNotFound as exc:
            raise HTTPException(404, str(exc))
        except setup_mod.SetupError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/setups/{setup_id}/rooms")
    def _add_setup_room(setup_id: str, body: RoomAdd):
        need_setup(setup_id)
        try:
            return setup_mod.add_room(setup_id, body.type, body.name, body.floor)
        except setup_mod.SetupError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/setups/{setup_id}/rooms/{room_id}/photos")
    def _setup_photo(setup_id: str, room_id: str, image: UploadFile = File(...)):
        _setup_room(setup_id, room_id)
        try:
            return setup_mod.add_room_photo(
                setup_id, room_id, image.filename or "photo.jpg", image.file.read()
            )
        except setup_mod.SetupNotFound as exc:
            raise HTTPException(404, str(exc))
        except setup_mod.SetupError as exc:
            raise HTTPException(400, str(exc))

    @app.delete("/api/setups/{setup_id}/rooms/{room_id}/photos/{n}")
    def _delete_setup_photo(setup_id: str, room_id: str, n: int):
        _setup_room(setup_id, room_id)
        try:
            return setup_mod.delete_room_photo(setup_id, room_id, n)
        except setup_mod.SetupNotFound as exc:
            raise HTTPException(404, str(exc))
        except setup_mod.SetupError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/setups/{setup_id}/rooms/{room_id}/photos/{n}")
    def _setup_photo_file(setup_id: str, room_id: str, n: int):
        _setup_room(setup_id, room_id)
        try:
            blob = setup_mod.read_room_photo(setup_id, room_id, n)
        except setup_mod.SetupNotFound as exc:
            raise HTTPException(404, str(exc))
        if blob is None:
            raise HTTPException(404, f"No photo {n} in room '{room_id}'")
        return Response(blob, media_type="image/jpeg", headers=dict(LONG_CACHE))

    @app.post("/api/setups/{setup_id}/rooms/{room_id}/voice")
    def _setup_voice(setup_id: str, room_id: str, audio: UploadFile = File(...)):
        _setup_room(setup_id, room_id)
        blob = audio.file.read(MAX_NOTE_BYTES + 1)
        token = call_context.set(
            {"run_dir": str(config.SETUPS_DIR / setup_id), "photo_id": None})
        try:
            try:
                return setup_mod.room_voice_note(
                    server.client, setup_id, room_id, blob, audio.filename or "rule",
                )
            except setup_mod.SetupNotFound as exc:
                raise HTTPException(404, str(exc))
            except setup_mod.SetupError as exc:
                raise HTTPException(400, str(exc))
            except MetaClientError as exc:
                raise HTTPException(502, str(exc))
        finally:
            call_context.reset(token)

    @app.post("/api/setups/{setup_id}/rooms/{room_id}/draft")
    def _setup_draft(setup_id: str, room_id: str):
        _setup_room(setup_id, room_id)
        token = call_context.set(
            {"run_dir": str(config.SETUPS_DIR / setup_id), "photo_id": None})
        try:
            try:
                return setup_mod.draft_room(server.client, setup_id, room_id)
            except setup_mod.SetupNotFound as exc:
                raise HTTPException(404, str(exc))
            except setup_mod.SetupError as exc:
                raise HTTPException(400, str(exc))
            except MetaClientError as exc:
                raise HTTPException(502, str(exc))
        finally:
            call_context.reset(token)

    @app.post("/api/setups/{setup_id}/save")
    def _save_setup(setup_id: str):
        need_setup(setup_id)
        token = call_context.set(
            {"run_dir": str(config.SETUPS_DIR / setup_id), "photo_id": None})
        try:
            try:
                return setup_mod.save_setup(server.client, setup_id)
            except setup_mod.SetupBusy as exc:
                raise HTTPException(409, str(exc))
            except setup_mod.SetupError as exc:
                raise HTTPException(400, str(exc))
            except PropertyError as exc:
                raise HTTPException(500, str(exc))
        finally:
            call_context.reset(token)

    @app.post("/api/setups/{setup_id}/tour")
    def _retry_setup_tour(setup_id: str):
        need_setup(setup_id)
        token = call_context.set(
            {"run_dir": str(config.SETUPS_DIR / setup_id), "photo_id": None})
        try:
            try:
                return setup_mod.retry_tour(server.client, setup_id)
            except setup_mod.SetupBusy as exc:
                raise HTTPException(409, str(exc))
            except setup_mod.SetupError as exc:
                raise HTTPException(400, str(exc))
            except PropertyError as exc:
                raise HTTPException(500, str(exc))
        finally:
            call_context.reset(token)

    @app.get("/")
    def _index(request: Request):
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/host{query}", status_code=302)

    @app.get("/clean")
    def _clean_page():
        return _page("clean.html")

    @app.get("/host")
    def _host_page():
        return _page("host.html")

    @app.get("/setup")
    def _setup_page():
        return _page("setup.html")

    return app


def _page(name):
    path = config.PROJECT_ROOT / "web" / name
    if not path.is_file():
        raise HTTPException(404, f"No {name} yet")
    return FileResponse(path, media_type="text/html")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pot.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    app = create_app()
    code = app.state.server.code
    if not config.REPLAY:
        print("Checking Meta API…", flush=True)
        meta_line, wonder_line, wonder_ok = startup_check_lines(
            app.state.server.client)
        print(meta_line, flush=True)
        print(wonder_line, flush=True)
        app.state.server.wonder = wonder_ok
        print(voice_start_line(), flush=True)
    for line in startup_lines(args.port, code, app.state.server.host_code):
        print(line, flush=True)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
