"""Offline tests for scripts/go_live.py helpers (no real network)."""

import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import go_live


SAMPLE_OUTPUT = """
2026-11-04T15:00:00Z INF Thank you for trying Cloudflare Tunnel.
2026-11-04T15:00:02Z INF |  https://brave-otter-1234.trycloudflare.com  |
2026-11-04T15:00:02Z INF Registered tunnel connection
"""


def test_parse_tunnel_url_from_cloudflared_output():
    assert go_live.parse_tunnel_url(SAMPLE_OUTPUT) == (
        "https://brave-otter-1234.trycloudflare.com")
    assert go_live.parse_tunnel_url("no url here") is None
    assert go_live.parse_tunnel_url("") is None
    assert go_live.parse_tunnel_url(None) is None


def test_links_carry_the_right_codes():
    base = "https://brave-otter-1234.trycloudflare.com"
    assert go_live.host_link(base, "hostcode") == (
        "https://brave-otter-1234.trycloudflare.com/host?code=hostcode")
    assert go_live.cleaner_link(base, "cleancode") == (
        "https://brave-otter-1234.trycloudflare.com/clean?code=cleancode")


class _FakeStdout:
    def __init__(self, lines):
        self.lines = list(lines)
        self.read = 0

    def readline(self):
        if self.read < len(self.lines):
            line = self.lines[self.read]
            self.read += 1
            return line
        return ""


class _FakeProc:
    def __init__(self, stdout):
        self.stdout = stdout


def test_watch_tunnel_keeps_reading_after_the_link():
    lines = ["2026 INF | https://brave-otter-1234.trycloudflare.com |\n"]
    lines += [f"2026 INF log line {i}\n" for i in range(1000)]
    stdout = _FakeStdout(lines)
    holder, ready = {}, threading.Event()
    go_live._watch_tunnel(_FakeProc(stdout), holder, ready)
    assert holder["url"] == "https://brave-otter-1234.trycloudflare.com"
    assert ready.is_set()
    assert stdout.read == len(lines)  # every later line was read too


def test_find_free_port_skips_a_busy_one():
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen(1)
    taken = busy.getsockname()[1]
    try:
        found = go_live.find_free_port(taken)
    finally:
        busy.close()
    assert found is not None and found != taken
    probe = socket.socket()
    probe.bind(("127.0.0.1", found))  # the returned port really is free
    probe.close()


def test_server_host_choice():
    assert go_live.server_host_for("cloudflared") == "127.0.0.1"
    assert go_live.server_host_for(None) == "0.0.0.0"
