"""Shared client for the Meta Model API.

Every feature of the app calls the API through MetaClient, which provides:

- one retry policy (up to 6 retries, exponential backoff capped at 20 s)
  on 404, 408, 429, 5xx, connection errors, timeouts and empty answers;
- recording of every successful call under data/model_cache/, keyed by a
  SHA-256 hash of the (redacted) request, with cache lookup before calling;
- replay mode (POT_REPLAY=1): no network, no API key, answers come only
  from the cache;
- a per-call trace in client.trace.
"""

import base64
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextvars import ContextVar, copy_context
from pathlib import Path

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

from pot import config

MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 20.0
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_CALLS = 6

#: Where the current call belongs: {"run_dir": str, "photo_id": str|None}.
#: Workers set this so the per-run call log can route each Meta call.
call_context = ContextVar("pot_call_context", default=None)


def submit_with_context(pool, func, *args, **kwargs):
    """pool.submit that carries the caller's contextvars into the worker.

    Thread pools start workers with a blank context; without this the
    per-run call log cannot route calls made in nested pools.
    """
    context = copy_context()
    return pool.submit(lambda: context.run(func, *args, **kwargs))


def max_calls_limit():
    """How many Meta calls may be in flight at once (POT_MAX_CALLS)."""
    try:
        return max(1, int(os.environ.get("POT_MAX_CALLS", str(DEFAULT_MAX_CALLS))))
    except ValueError:
        return DEFAULT_MAX_CALLS


class MetaClientError(Exception):
    """A Meta API call failed; the message names the purpose that failed."""


class ReplayCacheMissError(MetaClientError):
    """Replay mode has no recorded answer for this request."""


class _RetryableError(Exception):
    """Internal soft failure (empty answer, invalid JSON) worth retrying."""


def backoff_delay(retry_index):
    """Exponential backoff for the 0-based retry index, capped at 20 s."""
    return min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**retry_index))


def _redact(obj):
    """Replace big inline data (bytes, base64 data URLs) with its hash."""
    if isinstance(obj, bytes):
        return {
            "__redacted_sha256__": hashlib.sha256(obj).hexdigest(),
            "size": len(obj),
        }
    if isinstance(obj, str):
        if obj.startswith("data:") and ";base64," in obj:
            return {
                "__redacted_sha256__": hashlib.sha256(obj.encode("utf-8")).hexdigest(),
                "size": len(obj),
            }
        return obj
    if isinstance(obj, dict):
        return {key: _redact(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_redact(value) for value in obj]
    return obj


def _cache_key(redacted_request):
    canonical = json.dumps(redacted_request, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _status_of(exc):
    """HTTP status of an error, from OpenAI SDK, httpx, or test fakes."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is_retryable(exc):
    if isinstance(exc, _RetryableError):
        return True
    if isinstance(exc, (APIConnectionError, APITimeoutError, httpx.TransportError)):
        return True
    status = _status_of(exc)
    if status is None:
        return False
    if status == 400:
        return False
    return status in (404, 408, 429) or 500 <= status <= 599


def _usage_of(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    dump = getattr(usage, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    result = {
        key: getattr(usage, key)
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance(getattr(usage, key, None), int)
    }
    return result or None


def _schema_name(purpose):
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", purpose).strip("_") or "response"
    return f"{slug}_output"[:64]


def _read_bytes_or_missing(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


class MetaClient:
    """Meta Model API client with retries, recording/replay and tracing."""

    def __init__(
        self,
        *,
        api_key=None,
        base_url=None,
        reasoning_model=None,
        sam_model=None,
        asr_model=None,
        cache_dir=None,
        replay=None,
        openai_client=None,
        http_client=None,
        sleep=None,
        timeout=DEFAULT_TIMEOUT,
        max_calls=None,
    ):
        self._api_key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY", "")
        self.base_url = base_url or config.BASE_URL
        self.reasoning_model = reasoning_model or os.environ.get("REASONING_MODEL", config.REASONING_MODEL)
        self.sam_model = sam_model or os.environ.get("SAM_MODEL", config.SAM_MODEL)
        self.asr_model = asr_model or os.environ.get("ASR_MODEL", config.ASR_MODEL)
        if replay is None:
            self.replay = os.environ.get("POT_REPLAY") == "1"
        else:
            self.replay = bool(replay)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else config.MODEL_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._openai_client = openai_client
        self._http_client = http_client
        self._sleep = sleep
        self._timeout = timeout
        self.trace = []
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(
            max(1, max_calls if max_calls is not None else max_calls_limit())
        )
        self._hooks = []

    def add_hook(self, func):
        """Call func(entry) after every traced call (per-run logs)."""
        with self._lock:
            self._hooks.append(func)

    def _wait(self, seconds):
        if self._sleep is not None:
            self._sleep(seconds)
        else:
            time.sleep(seconds)

    def _ensure_openai(self, purpose):
        if self._openai_client is None:
            with self._lock:
                if self._openai_client is None:
                    if not self._api_key:
                        raise MetaClientError(
                            f"{purpose}: MODEL_API_KEY is not set "
                            "(copy .env.example to .env and add your key)"
                        )
                    # SDK-level retries off: this client owns the retry policy.
                    self._openai_client = OpenAI(
                        base_url=self.base_url,
                        api_key=self._api_key,
                        max_retries=0,
                        timeout=self._timeout,
                    )
        return self._openai_client

    def _ensure_http(self, purpose):
        if self._http_client is None:
            with self._lock:
                if self._http_client is None:
                    if not self._api_key:
                        raise MetaClientError(
                            f"{purpose}: MODEL_API_KEY is not set "
                            "(copy .env.example to .env and add your key)"
                        )
                    self._http_client = httpx.Client(timeout=self._timeout)
        return self._http_client

    def _append_trace(self, purpose, model, started, attempts, cache, ok, usage, error=None):
        context = call_context.get() or {}
        entry = {
            "purpose": purpose,
            "model": model,
            "seconds": time.monotonic() - started,
            "attempts": attempts,
            "cache": cache,
            "ok": ok,
            "usage": usage,
            "photo_id": context.get("photo_id"),
        }
        if error is not None:
            entry["error"] = error
        with self._lock:
            self.trace.append(entry)
            hooks = list(self._hooks)
        for func in hooks:
            try:
                func(dict(entry))
            except Exception as exc:  # noqa: BLE001 - logging never breaks the call
                print(f"MetaClient hook failed: {exc}", file=sys.stderr)

    def _write_cache_atomically(self, path, record):
        """Write a cache file via temp file + os.replace, never partial.

        Writes are serialized: on Windows two threads replacing the same
        file at once can fail, and serializing keeps that race away.
        """
        with self._lock:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(record, handle, indent=2, sort_keys=True)
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

    def _read_cache(self, path):
        """Read a cache file, or None when missing.

        Serialized with writes: on Windows a file open for reading cannot
        be replaced, so an unlocked read can break another thread's write.
        """
        with self._lock:
            if not path.exists():
                return None
            return path.read_text(encoding="utf-8")

    def _execute(self, *, purpose, model, request, call, rebuild):
        started = time.monotonic()
        redacted = _redact(request)
        key = _cache_key(redacted)
        path = self.cache_dir / f"{key}.json"
        cached = self._read_cache(path)
        if cached is not None:
            record = json.loads(cached)
            result = rebuild(copy.deepcopy(record["answer"]))
            self._append_trace(
                purpose, model, started, 0, "hit", True, record["answer"].get("usage")
            )
            return result
        if self.replay:
            error = (
                f"{purpose}: not recorded: no cached answer for this request "
                f"(POT_REPLAY=1, key {key[:16]}...)"
            )
            self._append_trace(purpose, model, started, 0, "miss", False, None, error)
            raise ReplayCacheMissError(error)
        # One overall limit on live calls: cache hits and replay misses
        # above never wait, but the network section holds a slot, so three
        # photos in flight still send at most POT_MAX_CALLS at once.
        with self._slots:
            return self._execute_live(purpose, model, redacted, path, call, started)

    def _execute_live(self, purpose, model, redacted, path, call, started):
        attempts = 0
        last_error = None
        for attempt in range(1 + MAX_RETRIES):
            attempts += 1
            try:
                result, answer = call()
            except MetaClientError as exc:
                self._append_trace(
                    purpose, model, started, attempts, "miss", False, None, str(exc)
                )
                raise
            except Exception as exc:  # noqa: BLE001 - classified below
                if not _is_retryable(exc):
                    status = _status_of(exc)
                    if status is not None:
                        message = f"{purpose}: request failed with HTTP {status} (not retried): {exc}"
                    else:
                        message = f"{purpose}: request failed (not retried): {exc}"
                    self._append_trace(purpose, model, started, attempts, "miss", False, None, message)
                    raise MetaClientError(message) from exc
                last_error = exc
                if attempt < MAX_RETRIES:
                    self._wait(backoff_delay(attempt))
                continue
            record = {"purpose": purpose, "model": model, "request": redacted, "answer": answer}
            self._write_cache_atomically(path, record)
            self._append_trace(
                purpose, model, started, attempts, "miss", True, answer.get("usage")
            )
            return result
        message = f"{purpose}: request failed after {attempts} attempts: {last_error}"
        self._append_trace(purpose, model, started, attempts, "miss", False, None, message)
        raise MetaClientError(message) from last_error

    def respond(
        self,
        purpose,
        content,
        instructions=None,
        schema=None,
        max_output_tokens=16000,
        effort="medium",
    ):
        """Ask Muse Spark via the Responses API.

        content is a list of input_text / input_image / input_video parts.
        With schema, requests structured JSON output and returns the parsed
        JSON; otherwise returns the answer text. Reasoning shares the output
        budget, so an empty answer doubles the budget and retries once.
        """
        budget = {"value": max_output_tokens, "doubled": False}

        def call():
            client = self._ensure_openai(purpose)
            kwargs = {
                "model": self.reasoning_model,
                "input": [{"role": "user", "content": list(content)}],
                "max_output_tokens": budget["value"],
            }
            if instructions is not None:
                kwargs["instructions"] = instructions
            if effort is not None:
                kwargs["reasoning"] = {"effort": effort}
            if schema is not None:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": _schema_name(purpose),
                        "schema": schema,
                    }
                }
            response = client.responses.create(**kwargs)
            text = getattr(response, "output_text", "") or ""
            if not text.strip():
                if not budget["doubled"]:
                    budget["doubled"] = True
                    budget["value"] *= 2
                raise _RetryableError(f"{purpose}: empty answer from {self.reasoning_model}")
            parsed = None
            if schema is not None:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise _RetryableError(f"{purpose}: invalid JSON answer: {exc}") from exc
            usage = _usage_of(response)
            answer = {
                "output_text": text,
                "parsed": parsed,
                "has_schema": schema is not None,
                "usage": usage,
            }
            return (parsed if schema is not None else text), answer

        def rebuild(answer):
            return answer["parsed"] if answer["has_schema"] else answer["output_text"]

        request = {
            "method": "respond",
            "model": self.reasoning_model,
            "instructions": instructions,
            "content": list(content),
            "schema": schema,
            "max_output_tokens": max_output_tokens,
            "effort": effort,
        }
        return self._execute(
            purpose=purpose, model=self.reasoning_model, request=request, call=call, rebuild=rebuild
        )

    def segment_image(self, purpose, jpeg_bytes, prompt):
        """Segment one image with sam-3.1 via the Responses API.

        Returns the raw output text (parse it with pot.sam.detect).
        """
        data_url = "data:image/jpeg;base64," + base64.b64encode(bytes(jpeg_bytes)).decode("ascii")

        def call():
            client = self._ensure_openai(purpose)
            response = client.responses.create(
                model=self.sam_model,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    }
                ],
                metadata={"include_confidence": "true", "mask_encoding": "one_bit"},
            )
            text = getattr(response, "output_text", "") or ""
            # An empty SAM answer means "nothing found", not a failure:
            # return it as-is and never retry it.
            usage = _usage_of(response)
            return text, {"output_text": text, "usage": usage}

        request = {
            "method": "segment_image",
            "model": self.sam_model,
            "prompt": prompt,
            "image": data_url,
        }
        return self._execute(
            purpose=purpose, model=self.sam_model, request=request, call=call,
            rebuild=lambda answer: answer["output_text"],
        )

    def transcribe(self, purpose, wav_path, languages):
        """Transcribe a WAV file via POST /v1/asr/transcribe.

        Returns the response JSON (with "transcript" and "turns").
        """
        path = Path(wav_path)
        audio = _read_bytes_or_missing(path)
        payload = {
            "mode": "ENDPOINTING",
            "model": self.asr_model,
            "audioEncoding": "WAV",
            "languageBias": list(languages),
        }
        if audio is None:
            audio_ref = {"filename": path.name, "missing": True}
        else:
            audio_ref = {
                "filename": path.name,
                "size": len(audio),
                "sha256": hashlib.sha256(audio).hexdigest(),
            }

        def call():
            http = self._ensure_http(purpose)
            blob = _read_bytes_or_missing(path)
            if blob is None:
                raise MetaClientError(f"{purpose}: cannot read WAV file {path}")
            response = http.post(
                f"{self.base_url}/asr/transcribe",
                headers={"Authorization": f"Bearer {self._api_key}"},
                data={"request": json.dumps(payload)},
                files={"audio": (path.name, blob, "audio/wav")},
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("transcript") and not result.get("turns"):
                raise _RetryableError(f"{purpose}: empty transcript from {self.asr_model}")
            return result, {"transcript_json": result, "usage": None}

        request = {
            "method": "transcribe",
            "model": self.asr_model,
            "payload": payload,
            "audio": audio_ref,
        }
        return self._execute(
            purpose=purpose, model=self.asr_model, request=request, call=call,
            rebuild=lambda answer: answer["transcript_json"],
        )

    def upload_file(self, purpose, path):
        """Upload a file with purpose="user_data"; returns the file id."""
        target = Path(path)
        blob = _read_bytes_or_missing(target)
        if blob is None:
            file_ref = {"filename": target.name, "missing": True}
        else:
            file_ref = {
                "filename": target.name,
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }

        def call():
            client = self._ensure_openai(purpose)
            try:
                handle = open(target, "rb")  # noqa: PTH123, SIM115 - closed below, reopened per attempt
            except OSError as exc:
                raise MetaClientError(f"{purpose}: cannot read file {target}: {exc}") from exc
            with handle:
                created = client.files.create(file=handle, purpose="user_data")
            file_id = getattr(created, "id", "")
            if not file_id:
                raise _RetryableError(f"{purpose}: empty file id from files.create")
            return file_id, {"file_id": file_id, "usage": None}

        request = {"method": "upload_file", "filename": target.name, "file": file_ref}
        return self._execute(
            purpose=purpose, model=None, request=request, call=call,
            rebuild=lambda answer: answer["file_id"],
        )
