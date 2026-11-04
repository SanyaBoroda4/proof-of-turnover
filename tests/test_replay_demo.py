"""Offline tests for scripts/replay_demo.py (no server, no network)."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = ROOT / "scripts" / "replay_demo.py"
    spec = importlib.util.spec_from_file_location("replay_demo_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_env_is_replay_with_demo_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "must-not-be-used")
    module = _load()
    env = module.build_env(tmp_path / "runs")
    assert env["POT_REPLAY"] == "1"
    assert env["POT_VOICE"] == "device"
    assert env["POT_ACCESS_CODE"] == "demo"
    assert env["POT_HOST_CODE"] == "demohost"
    assert env["POT_RUNS_DIR"] == str(tmp_path / "runs")
    assert env["POT_MODEL_CACHE_DIR"].endswith(
        str(Path("replay") / "model_cache"))
    assert "MODEL_API_KEY" not in env
    assert "data" not in Path(env["POT_RUNS_DIR"]).parts[-2:]


def test_judge_links_carry_demo_codes_and_run():
    module = _load()
    cleaner, host = module.judge_links(8000, "run-1")
    assert cleaner == "http://127.0.0.1:8000/clean?code=demo&run=run-1"
    assert host == "http://127.0.0.1:8000/host?code=demohost&run=run-1"


def test_find_run_id(tmp_path):
    module = _load()
    (tmp_path / "run-20260101-000000").mkdir()
    assert module.find_run_id(tmp_path) == "run-20260101-000000"
    (tmp_path / "run-20260101-000001").mkdir()
    with pytest.raises(SystemExit):
        module.find_run_id(tmp_path)
