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

RUNS_DIR = Path("data/runs")
MODEL_CACHE_DIR = Path("data/model_cache")


def ensure_dirs():
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


ensure_dirs()
