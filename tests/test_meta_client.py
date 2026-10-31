"""Offline tests for pot.meta_client using a fake API (no real calls)."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, BadRequestError

from pot.meta_client import MetaClient, MetaClientError, ReplayCacheMissError

CONTENT = [{"type": "input_text", "text": "say hi"}]


def _http_error(status, cls=APIStatusError):
    request = httpx.Request("POST", "https://api.meta.ai/v1/responses")
    response = httpx.Response(status, request=request, json={"error": {"message": "boom"}})
    return cls("boom", response=response, body={"message": "boom"})


class _FakeResponse:
    def __init__(self, output_text="", usage=None):
        self.output_text = output_text
        self.usage = usage if usage is not None else {
            "input_tokens": 3,
            "output_tokens": 5,
            "total_tokens": 8,
        }


class _ScriptedAPI:
    """Replays scripted outcomes (exceptions or values); repeats the last."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _next(self):
        if len(self.script) > 1:
            return self.script.pop(0)
        return self.script[0]


class _FakeResponsesAPI(_ScriptedAPI):
    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeFilesAPI(_ScriptedAPI):
    def __init__(self, script):
        super().__init__(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeHTTP(_ScriptedAPI):
    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _FakeHTTPResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://api.meta.ai/v1/asr/transcribe")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=self.request, response=self)

    def json(self):
        return self._payload


def _openai_fake(script, file_id="file-123"):
    return SimpleNamespace(
        responses=_FakeResponsesAPI(script),
        files=_FakeFilesAPI([SimpleNamespace(id=file_id)]),
    )


def _client(tmp_path, script, **kwargs):
    kwargs.setdefault("api_key", "test-key")
    kwargs.setdefault("sleep", lambda seconds: None)
    return MetaClient(
        cache_dir=tmp_path / "cache", openai_client=_openai_fake(script), **kwargs
    )


def _cache_texts(tmp_path):
    return [
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "cache").glob("*.json")
    ]


def test_retry_then_success(tmp_path):
    fake = _openai_fake([_http_error(404), _http_error(429), _FakeResponse("hi")])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    assert client.respond("greet", CONTENT) == "hi"
    assert len(fake.responses.calls) == 3
    entry = client.trace[-1]
    assert entry["attempts"] == 3
    assert entry["ok"] is True
    assert entry["cache"] == "miss"
    assert entry["purpose"] == "greet"
    assert entry["model"] == "muse-spark-1.3-contributor"
    assert entry["usage"] == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}


def test_connection_error_retried(tmp_path):
    client = _client(tmp_path, [httpx.ConnectError("down"), _FakeResponse("ok")])
    assert client.respond("greet", CONTENT) == "ok"
    assert client.trace[-1]["attempts"] == 2


def test_backoff_capped_at_20s_and_six_retries(tmp_path):
    delays = []
    client = _client(tmp_path, [_http_error(500)], sleep=delays.append)
    with pytest.raises(MetaClientError, match="probe-xyz"):
        client.respond("probe-xyz", CONTENT)
    assert delays == [1, 2, 4, 8, 16, 20]
    assert client.trace[-1]["attempts"] == 7
    assert client.trace[-1]["ok"] is False


def test_400_not_retried_and_names_purpose(tmp_path):
    fake = _openai_fake([_http_error(400, BadRequestError)])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    with pytest.raises(MetaClientError, match="bad-prompt"):
        client.respond("bad-prompt", CONTENT)
    assert len(fake.responses.calls) == 1


def test_empty_answer_doubles_budget_once(tmp_path):
    fake = _openai_fake([_FakeResponse(""), _FakeResponse("  "), _FakeResponse("done")])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    assert client.respond("greet", CONTENT) == "done"
    budgets = [call["max_output_tokens"] for call in fake.responses.calls]
    assert budgets == [16000, 32000, 32000]


def test_schema_returns_parsed_json(tmp_path):
    fake = _openai_fake([_FakeResponse('{"ready": true}')])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    schema = {"type": "object", "properties": {"ready": {"type": "boolean"}}}
    assert client.respond("check room", CONTENT, schema=schema) == {"ready": True}
    assert fake.responses.calls[0]["text"] == {
        "format": {"type": "json_schema", "name": "check_room_output", "schema": schema}
    }


def test_schema_name_capped_at_64_chars(tmp_path):
    fake = _openai_fake([_FakeResponse('{"a": 1}')])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    client.respond("first look kitchen (P01,P02,P03,P04,P05,P06,P07,P08,P09,P10)", CONTENT,
                   schema={"type": "object"})
    name = fake.responses.calls[0]["text"]["format"]["name"]
    assert len(name) <= 64


def test_second_identical_call_comes_from_cache(tmp_path):
    client = _client(tmp_path, [_FakeResponse("hi")])
    assert client.respond("greet", CONTENT) == "hi"
    assert client.respond("greet", CONTENT) == "hi"
    assert len(client._openai_client.responses.calls) == 1
    assert client.trace[0]["cache"] == "miss"
    assert client.trace[1]["cache"] == "hit"
    assert client.trace[1]["attempts"] == 0
    assert client.trace[1]["usage"] == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}


def test_replay_works_with_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    cache_dir = tmp_path / "cache"
    seed = MetaClient(cache_dir=cache_dir, api_key="k",
                      openai_client=_openai_fake([_FakeResponse("hi")]),
                      sleep=lambda seconds: None)
    assert seed.respond("greet", CONTENT) == "hi"
    replay = MetaClient(cache_dir=cache_dir, replay=True)  # no key, no network client
    assert replay.respond("greet", CONTENT) == "hi"
    assert replay.trace[-1]["cache"] == "hit"


def test_replay_flag_read_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("POT_REPLAY", "1")
    assert MetaClient(cache_dir=tmp_path).replay is True


def test_replay_of_unrecorded_call_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    client = MetaClient(cache_dir=tmp_path, replay=True)
    with pytest.raises(ReplayCacheMissError, match="not recorded"):
        client.respond("never-seen", CONTENT)


def test_cache_files_contain_no_raw_base64(tmp_path):
    jpeg = b"\xff\xd8fake-jpeg-bytes\xff\xd9"
    image_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    fake = _openai_fake([_FakeResponse("seg-output"), _FakeResponse("vision-ok")])
    client = MetaClient(cache_dir=tmp_path / "cache", api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    client.segment_image("find towels", jpeg, "towel")
    client.respond("look", [{"type": "input_image", "image_url": image_url}])
    texts = _cache_texts(tmp_path)
    assert len(texts) == 2
    blob = base64.b64encode(jpeg).decode("ascii")
    for text in texts:
        assert blob not in text
        assert "data:image" not in text
        assert "__redacted_sha256__" in text


def test_segment_image_request_shape(tmp_path):
    fake = _openai_fake([_FakeResponse("seg-output")])
    client = MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    assert client.segment_image("find towels", b"jpeg-bytes", "towel") == "seg-output"
    call = fake.responses.calls[0]
    assert call["model"] == "sam-3.1"
    assert call["metadata"] == {"include_confidence": "true", "mask_encoding": "one_bit"}
    parts = call["input"][0]["content"]
    assert parts[0] == {"type": "input_text", "text": "towel"}
    assert parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/jpeg;base64,")


def test_transcribe_request_shape(tmp_path):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF-fake-wav")
    payload = {"transcript": "hola", "turns": [{"text": "hola"}]}
    http = _FakeHTTP([_FakeHTTPResponse(payload)])
    client = MetaClient(cache_dir=tmp_path / "cache", api_key="k", http_client=http,
                        sleep=lambda seconds: None)
    assert client.transcribe("transcribe walkthrough", wav, ["es"]) == payload
    post = http.calls[0]
    assert post["url"] == "https://api.meta.ai/v1/asr/transcribe"
    assert post["headers"] == {"Authorization": "Bearer k"}
    assert json.loads(post["data"]["request"]) == {
        "mode": "ENDPOINTING",
        "model": "muse-voice-transcribe-1.0",
        "audioEncoding": "WAV",
        "languageBias": ["es"],
    }
    assert post["files"] == {"audio": ("clip.wav", b"RIFF-fake-wav", "audio/wav")}


def test_upload_file_returns_id(tmp_path):
    target = tmp_path / "frame.jpg"
    target.write_bytes(b"fake-frame")
    fake = _openai_fake([], file_id="file-abc")
    client = MetaClient(cache_dir=tmp_path / "cache", api_key="k", openai_client=fake,
                        sleep=lambda seconds: None)
    assert client.upload_file("upload frame", target) == "file-abc"
    assert fake.files.calls[0]["purpose"] == "user_data"
