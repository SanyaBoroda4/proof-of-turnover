"""Parallel calls through one MetaClient keep the cache valid (no network)."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from pot.meta_client import MetaClient

CONTENT = [{"type": "input_text", "text": "say hi"}]


class _ThreadSafeResponses:
    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
        return SimpleNamespace(output_text="hi", usage={"input_tokens": 1})


def _client(tmp_path):
    fake = SimpleNamespace(responses=_ThreadSafeResponses())
    return MetaClient(cache_dir=tmp_path, api_key="k", openai_client=fake,
                      sleep=lambda seconds: None)


def _check_cache_valid(cache_dir):
    files = list(cache_dir.glob("*.json"))
    assert files
    assert not list(cache_dir.glob("*.tmp"))
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert set(record) == {"purpose", "model", "request", "answer"}
    return files


def test_parallel_identical_calls_share_one_valid_cache_file(tmp_path):
    client = _client(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.respond("greet", CONTENT), range(8)))
    assert results == ["hi"] * 8
    assert len(client.trace) == 8
    assert len(_check_cache_valid(tmp_path)) == 1


def test_parallel_distinct_calls_all_stay_valid(tmp_path):
    client = _client(tmp_path)

    def call(i):
        return client.respond(f"greet-{i}", CONTENT + [{"type": "input_text", "text": str(i)}])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))
    assert results == ["hi"] * 8
    assert len(client.trace) == 8
    assert len(_check_cache_valid(tmp_path)) == 8
