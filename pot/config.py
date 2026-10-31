"""Shared configuration for Proof of Turnover.

Loads .env automatically. Model names default to the hackathon Meta models
and can each be overridden with an environment variable of the same name.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.meta.ai/v1"

MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "")

REASONING_MODEL = os.environ.get("REASONING_MODEL", "muse-spark-1.3-contributor")
SAM_MODEL = os.environ.get("SAM_MODEL", "sam-3.1")
ASR_MODEL = os.environ.get("ASR_MODEL", "muse-voice-transcribe-1.0")

REPLAY = os.environ.get("POT_REPLAY") == "1"

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _project_dir(env_name, default):
    """A data dir anchored at the project folder, with an env override.

    All paths stay relative to the project folder no matter which folder
    the server starts in; the env var may point anywhere (used by the
    judge replay bundle).
    """
    value = os.environ.get(env_name)
    path = Path(value) if value else PROJECT_ROOT / default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


RUNS_DIR = _project_dir("POT_RUNS_DIR", "data/runs")
MODEL_CACHE_DIR = _project_dir("POT_MODEL_CACHE_DIR", "data/model_cache")
DEMO_DIR = _project_dir("POT_DEMO_DIR", "data/demo-build")

#: Supported cleaner languages: (code, native name). Everything reads
#: from this list, so a new language is one entry + one tour regeneration.
CLEANER_LANGUAGES = (
    ("es", "Español"),
    ("en", "English"),
    ("ru", "Русский"),
)
CLEANER_LANGUAGE_CODES = frozenset(code for code, _name in CLEANER_LANGUAGES)


def ensure_dirs():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


ensure_dirs()
