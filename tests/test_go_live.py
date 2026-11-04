"""Offline tests for scripts/go_live.py helpers (no real network)."""

import sys
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


def test_ascii_qr_renders_blocks():
    art = go_live.ascii_qr("https://example.com/clean?code=demo")
    lines = art.split("\n")
    assert len(lines) > 10
    assert "█" in art or "▀" in art
    assert all(len(line) == len(lines[0]) for line in lines)
