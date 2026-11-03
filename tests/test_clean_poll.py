"""Behaviour test for clean.html's GET timeout and poll loop.

Runs the page's api() in Node with a fetch that never answers until
its AbortSignal fires: the GET must abort on the (shortened) timeout
and the next GET must succeed, so a hung status request cannot stall
the poll loop forever. Also checks the poll loop reschedules in a
finally block and that becoming visible refreshes at once.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PRELUDE = r"""
globalThis.window = globalThis;
let fetchCalls = 0;
globalThis.fetch = (path, opts) => {
  fetchCalls += 1;
  if (fetchCalls === 1) {
    return new Promise((resolve, reject) => {
      const signal = opts && opts.signal;
      if (!signal) return; // never answers, and nothing can abort it
      signal.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    });
  }
  return Promise.resolve({
    status: 200, ok: true,
    json: async () => ({fine: true}),
  });
};
"""

ASSERTIONS = r"""
(async () => {
  const started = Date.now();
  let aborted = false;
  try {
    await api("/api/runs/x/status");
  } catch (e) {
    aborted = e && e.name === "AbortError";
  }
  if (!aborted) throw new Error("hung GET was not aborted by the timeout");
  if (Date.now() - started > 5000) throw new Error("abort took too long");
  const again = await api("/api/runs/x/status");
  if (!again || again.fine !== true) throw new Error("next GET did not recover");
  if (fetchCalls !== 2) throw new Error(`expected 2 fetches, got ${fetchCalls}`);
})().catch((error) => { console.error(error); process.exit(1); });
"""


def _api_block():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    start = script.index("const GET_TIMEOUT_MS")
    end = script.index("const UPLOAD_TIMEOUT_MS")
    block = script[start:end]
    # Shorten the 12 s timeout so the test runs fast.
    return block.replace("12000", "60")


def test_hung_get_aborts_and_next_get_recovers():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "-e", PRELUDE + _api_block() + ASSERTIONS],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_poll_loop_reschedules_in_finally_and_visibility_refreshes():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    poll = script[script.index("function startPoll"):script.index("async function boot")]
    assert "finally" in poll
    assert "pollTimer = setTimeout(tick" in poll
    assert 'document.addEventListener("visibilitychange"' in script
    visible = script[script.index("visibilitychange"):]
    assert "await refresh()" in visible[:600]
