"""Live tour web server: JSON API for the cleaner and host pages.

Run: python -m pot.server [--host 127.0.0.1] [--port 8000]

Every page and API route needs the access code (POT_ACCESS_CODE, or a
random one made at startup): opening a link with ?code=... once sets a
cookie. Localhost is not exempt: tunnel traffic also arrives locally.
"""

import argparse
import io
import json
import os
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
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from pot import config, prompts
from pot.checklist import PropertyError, build_checklist, load_property
from pot.meta_client import MetaClient, call_context
from pot.photos import new_run_dir
from pot.recheck import run_photo_checks
from pot.requests import write_requests
from pot.runs import OldPhotoError, RunState
from pot.tour import fixup_tour, generate_tour, load_tour

COOKIE_NAME = "pot_code"


def access_code():
    """The access code: POT_ACCESS_CODE, or a random one at startup."""
    return os.environ.get("POT_ACCESS_CODE", "") or secrets.token_urlsafe(9)


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


class TourRequest(BaseModel):
    property: str


class SignoffBody(BaseModel):
    decision: str
    notes: str = ""
    item_ids: list = []


class ServerState:
    """One server's runs, client and background workers."""

    def __init__(self, client, code):
        self.client = client
        self.code = code
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
            "time": datetime.now().isoformat(timespec="seconds"),
            "model": entry.get("model"),
            "purpose": entry.get("purpose"),
            "photo_id": entry.get("photo_id"),
            "seconds": round(entry.get("seconds", 0.0), 1),
            "attempts": entry.get("attempts", 0),
            "source": source,
        }
        with self.calls_lock:
            with open(Path(run_dir) / "calls.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        with self.registry_lock:
            state = self.runs.get(Path(run_dir).name)
        if state is not None:
            state.record_call(record)


def _check_worker(server, state, photo_id):
    """Background check for one uploaded photo (parallel tour mode)."""
    token = call_context.set({"run_dir": str(state.run_dir), "photo_id": photo_id})
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
        fresh = run_photo_checks(
            server.client, state, photo_id, items,
            parallel=True, effort=prompts.RECHECK_EFFORT,
        )
        fresh["elapsed_seconds"] = round(time.monotonic() - started, 1)
        merged = state.merge_result(photo_id, fresh)
        if merged["failed"]:
            requests, notes = write_requests(
                server.client, state.property, state.checklist, state.proofs,
                effort=prompts.RECHECK_EFFORT, item_ids=set(merged["failed"]),
            )
            state.store_requests(requests, notes)
    except Exception as exc:  # noqa: BLE001 - a photo check never kills the server
        state.mark_failed(photo_id, f"Photo {photo_id}: check failed: {exc}")
    finally:
        call_context.reset(token)


def create_app(client=None):
    """Build the FastAPI app (pass a fake client in tests)."""
    server = ServerState(client or MetaClient(), access_code())
    app = FastAPI(title="Proof of Turnover")
    app.state.server = server

    def need_state(run_id):
        state = server.get_state(run_id)
        if state is None:
            raise HTTPException(404, f"Unknown run '{run_id}'")
        return state

    @app.middleware("http")
    async def _auth(request, call_next):
        if request.cookies.get(COOKIE_NAME) == server.code:
            return await call_next(request)
        if request.query_params.get("code") == server.code:
            response = await call_next(request)
            response.set_cookie(COOKIE_NAME, server.code, httponly=True, samesite="lax")
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
        state = RunState.create(
            new_run_dir(), body.property, tour={"shots": shots}, demo=body.demo
        )
        server.remember(state)
        return {
            "run_id": state.run_dir.name,
            "started_at": state.started_at.isoformat(timespec="seconds"),
            "shots": len(shots),
            "tour_notes": notes,
        }

    @app.get("/api/runs/{run_id}/tour")
    def _get_tour(run_id: str):
        state = need_state(run_id)
        status = state.status()
        return {
            "shots": state.tour["shots"],
            "taken": state.taken_shots(),
            "current_shot": status["current_shot"],
        }

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
    def _get_evidence(run_id: str, item_id: str):
        blob = need_state(run_id).read_evidence(item_id)
        if blob is None:
            raise HTTPException(404, f"No evidence for '{item_id}'")
        return Response(blob, media_type="image/jpeg")

    @app.post("/api/runs/{run_id}/signoff")
    def _signoff(run_id: str, body: SignoffBody):
        state = need_state(run_id)
        if body.decision == "approve":
            signoff = state.approve()
        elif body.decision == "sendback":
            if not body.item_ids:
                raise HTTPException(400, "Tick at least one item to send back")
            signoff = state.send_back(body.item_ids, body.notes)
            token = call_context.set({"run_dir": str(state.run_dir), "photo_id": None})
            try:
                requests, notes = write_requests(
                    server.client, state.property, state.checklist,
                    state.proofs, effort=prompts.RECHECK_EFFORT,
                    item_ids=set(signoff["items"]),
                )
            finally:
                call_context.reset(token)
            state.store_requests(requests, notes)
        else:
            raise HTTPException(400, f"Unknown decision {body.decision!r}")
        return {"signoff": signoff, "agent_state": state.agent_state()}

    @app.get("/api/qr")
    def _qr(text: str = Query(max_length=2000)):
        image = qrcode.make(text)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return Response(buffer.getvalue(), media_type="image/png")

    @app.get("/api/server-ip")
    def _server_ip():
        return {"ip": local_ip()}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pot.server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    app = create_app()
    code = app.state.server.code
    print(f"Host:    http://127.0.0.1:{args.port}/host?code={code}")
    print(f"Cleaner: http://{local_ip()}:{args.port}/clean?code={code}")
    print("Open the cleaner link on the phone (same Wi-Fi as this PC).")
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
