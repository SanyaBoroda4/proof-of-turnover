"""Behaviour test for clean.html's neural-voice fallback pause.

Runs the page's speech block in Node with stubbed fetch/audio/phone
speech: after a neural failure the next sentences must use the phone
voice without another /api/voice call for 2 minutes.
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
let fakeNow = 1000000;
Date.now = () => fakeNow;
const phone = [];
let fetchCalls = 0;
let fetchMode = "reject";
let playMode = "ok";
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
  onended: null,
  onerror: null,
  pause: () => {},
  load: () => {},
  play: () => (playMode === "reject"
    ? Promise.reject(new Error("play rejected"))
    : Promise.resolve()),
};
globalThis.document = { getElementById: () => audio };
globalThis.URL.createObjectURL = () => "blob:voice";
globalThis.fetch = async () => {
  fetchCalls += 1;
  if (fetchMode === "reject") throw new Error("offline");
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
  if (fetchCalls !== 1) throw new Error(`expected 1 fetch, got ${fetchCalls}`);
  if (phone.join("|") !== "one|two") {
    throw new Error(`phone fallback wrong: ${phone.join("|")}`);
  }

  fakeNow += 120001;
  fetchMode = "ok";
  playMode = "reject";
  speak("three");
  await tick();
  if (fetchCalls !== 2) throw new Error(`expected retry fetch, got ${fetchCalls}`);
  if (phone.join("|") !== "one|two|three") {
    throw new Error(`play fallback wrong: ${phone.join("|")}`);
  }

  playMode = "ok";
  speak("four");
  await tick();
  if (fetchCalls !== 2) {
    throw new Error(`pause after play failure broken: ${fetchCalls} fetches`);
  }
  if (phone.join("|") !== "one|two|three|four") {
    throw new Error(`paused fallback wrong: ${phone.join("|")}`);
  }
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _speech_block():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    start = script.index("const VOICE_PREFS")
    end = script.index("function silentClipUrl")
    return script[start:end]


def test_neural_voice_failure_pauses_for_two_minutes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "-e", PRELUDE + _speech_block() + ASSERTIONS],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
