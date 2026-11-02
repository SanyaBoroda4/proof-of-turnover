"""Behaviour test for clean.html's neural-voice fallback.

Runs the page's speech block in Node with stubbed fetch/audio/phone
speech: every line tries the neural voice again, a 503 counts toward
three-in-a-row phone-only mode, and Repeat replays a cached clip with
no new fetch.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PRELUDE = r"""
globalThis.window = globalThis;
const CODE = "test";
let LANG = "en";
const phone = [];
const played = [];
let fetchCalls = 0;
let fetchMode = "reject";
globalThis.speechSynthesis = {
  getVoices: () => [],
  speak: (u) => { phone.push(u.text); if (u.onend) u.onend(); },
  cancel: () => {},
};
globalThis.SpeechSynthesisUtterance = class {
  constructor(text) { this.text = text; }
};
const audio = {
  src: "",
  currentTime: 0,
  onended: null,
  onerror: null,
  pause: () => {},
  load: () => {},
  removeAttribute: () => {},
  play: () => { played.push(audio.src); return Promise.resolve(); },
};
globalThis.document = { getElementById: () => audio };
globalThis.URL.createObjectURL = () => "blob:voice";
globalThis.fetch = async () => {
  fetchCalls += 1;
  if (fetchMode === "reject") throw new Error("offline");
  if (fetchMode === "503") return { ok: false, status: 503 };
  return { ok: true, blob: async () => ({}) };
};
"""

ASSERTIONS = r"""
const tick = () => new Promise((resolve) => setTimeout(resolve, 25));
function speak(text) { speechQueue.push(text); pumpSpeech(); }
(async () => {
  speak("one");
  await tick();
  speak("two");
  await tick();
  if (fetchCalls !== 2) {
    throw new Error(`neural must be retried per line, got ${fetchCalls}`);
  }
  if (phone.join("|") !== "one|two") {
    throw new Error(`phone fallback wrong: ${phone.join("|")}`);
  }

  fetchMode = "503";
  speak("three");
  await tick();
  speak("four");
  await tick();
  speak("five");
  await tick();
  if (fetchCalls !== 5) throw new Error(`expected 5 fetches, got ${fetchCalls}`);
  if (!voicePhoneOnly) throw new Error("three 503s must switch to phone only");
  speak("six");
  await tick();
  if (fetchCalls !== 5) {
    throw new Error(`phone-only session must not fetch, got ${fetchCalls}`);
  }
  if (phone.join("|") !== "one|two|three|four|five|six") {
    throw new Error(`phone-only fallback wrong: ${phone.join("|")}`);
  }

  voiceCache.set("again", "blob:again");
  repeatSay("again");
  await tick();
  if (fetchCalls !== 5) throw new Error("repeat must not fetch again");
  if (played[played.length - 1] !== "blob:again") {
    throw new Error(`repeat played ${played[played.length - 1]}`);
  }
  if (phone.join("|") !== "one|two|three|four|five|six") {
    throw new Error("cached repeat must not use the phone voice");
  }
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _speech_block():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    start = script.index("const VOICE_PREFS")
    end = script.index("let wakeLock")
    return script[start:end]


def test_neural_voice_retries_per_line_and_repeat_uses_cache():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "-e", PRELUDE + _speech_block() + ASSERTIONS],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
