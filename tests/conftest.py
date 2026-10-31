import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pot import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_data_dirs(tmp_path, monkeypatch):
    """Every test gets its own runs/cache dirs; the real data/ stays clean."""
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "data" / "runs")
    monkeypatch.setattr(config, "MODEL_CACHE_DIR", tmp_path / "data" / "model_cache")
