"""Behaviour test for clean.html's wide-camera fallback.

Runs the page's camera block in Node with a fake track that has no
zoom and a fake device list with no ultra-wide camera: asking for the
wide view repeatedly for one overview shot must try the camera at most
once and must not rebuild the page in a loop.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PRELUDE = r"""
globalThis.window = globalThis;
const WIDE = true;
const DEMO = false;
let camStarting = false;
let camMode = "live";
let camSheet = null;
let camWaiting = false;
let camWide = false;
let camWideBusy = false;
let camWideShot = null;
let camWideImpossible = false;
let camDeviceId = null;
let camNormalId = null;
let lastKey = "key";
let tour = {};
let status = {};
let renderCalls = 0;
let enumerateCalls = 0;
const track = {
  readyState: "live",
  getCapabilities: () => ({}),
  getSettings: () => ({ deviceId: "back" }),
  applyConstraints: async () => { throw new Error("no zoom"); },
  stop: () => {},
};
let camStream = {
  getVideoTracks: () => [track],
  getTracks: () => [track],
};
const video = {
  srcObject: null,
  videoWidth: 640,
  play: () => Promise.resolve(),
};
globalThis.document = {
  getElementById: (id) => (id === "camView" ? video : { hidden: false }),
  querySelectorAll: () => [],
};
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { mediaDevices: {
    enumerateDevices: async () => {
      enumerateCalls += 1;
      return [{ kind: "videoinput", label: "Back Camera", deviceId: "back" }];
    },
    getUserMedia: async () => { throw new Error("unused"); },
  } },
});
function renderMaybe() { renderCalls += 1; }
function screenOf() { return "round"; }
function roundCurrent() { return { shot_id: "porch-01", overview: true }; }
function shotOfItem() { return null; }
function playQuiet() {}
async function waitForVideo() { return true; }
"""

ASSERTIONS = r"""
const tick = () => new Promise((resolve) => setTimeout(resolve, 25));
(async () => {
  await syncWideCamera();
  await syncWideCamera();
  await syncWideCamera();
  await tick();
  if (enumerateCalls !== 1) {
    throw new Error(`camera tried ${enumerateCalls} times, expected 1`);
  }
  if (renderCalls > 1) {
    throw new Error(`renderMaybe called ${renderCalls} times, expected at most 1`);
  }
  if (camWide) throw new Error("wide must stay off when it is impossible");
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _camera_block():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    start = script.index("function camLive()")
    end = script.index("function waitForVideo")
    return script[start:end]


def test_wide_camera_failure_does_not_loop():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "-e", PRELUDE + _camera_block() + ASSERTIONS],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
