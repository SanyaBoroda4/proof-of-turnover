"""One-command live walkthrough in your own house.

    python scripts/go_live.py [--port 8000]

Starts the server with fresh host/cleaner access codes (or the ones
from POT_HOST_CODE / POT_ACCESS_CODE), opens a Cloudflare quick tunnel
when cloudflared is installed, prints the laptop (host) link and an
ASCII QR code of the phone (cleaner) link. The server reads .env
itself; this script never reads .env and never prints any key.
Ctrl+C stops the tunnel and the server.
"""

import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def parse_tunnel_url(text):
    """The first trycloudflare URL in cloudflared's output, or None."""
    match = TUNNEL_RE.search(text or "")
    return match.group(0) if match else None


def host_link(base, code):
    return f"{base}/host?code={code}"


def cleaner_link(base, code):
    return f"{base}/clean?code={code}"


def ascii_qr(text):
    """The QR code for text as terminal block characters."""
    import qrcode

    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = []
    for i in range(0, len(matrix), 2):
        top = matrix[i]
        bottom = matrix[i + 1] if i + 1 < len(matrix) else [False] * len(top)
        lines.append("".join(
            "█" if t and b else "▀" if t else "▄" if b else " "
            for t, b in zip(top, bottom)
        ))
    return "\n".join(lines)


def _answers(url, timeout=5):
    """True when the URL gives any HTTP answer (even 401/404)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def wait_for_url(url, timeout=90):
    """Poll until the URL answers or the timeout passes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _answers(url):
            return True
        time.sleep(2)
    return False


def lan_ip():
    """This machine's LAN address (for the no-tunnel fallback links)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.0.1", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _stop(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _watch_tunnel(proc, holder, ready):
    for line in proc.stdout:
        url = parse_tunnel_url(line)
        if url and "url" not in holder:
            holder["url"] = url
            ready.set()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    port = args.port

    env = dict(os.environ)
    host_code = env.get("POT_HOST_CODE") or secrets.token_urlsafe(8)
    cleaner_code = env.get("POT_ACCESS_CODE") or secrets.token_urlsafe(8)
    env["POT_HOST_CODE"] = host_code
    env["POT_ACCESS_CODE"] = cleaner_code

    server = subprocess.Popen(
        [sys.executable, "-m", "pot.server", "--port", str(port)], env=env)
    tunnel = None
    try:
        if not wait_for_url(f"http://127.0.0.1:{port}/", timeout=30):
            print("The server did not start; see its messages above.")
            return 1
        cloudflared = shutil.which("cloudflared")
        if cloudflared:
            tunnel = subprocess.Popen(
                [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
            holder, ready = {}, threading.Event()
            watcher = threading.Thread(
                target=_watch_tunnel, args=(tunnel, holder, ready),
                daemon=True)
            watcher.start()
            ready.wait(timeout=90)
            base = holder.get("url")
            if base and wait_for_url(base, timeout=90):
                print()
                print(f"Laptop (host):  {host_link(base, host_code)}")
                print("Phone (cleaner): scan this QR code")
                print(ascii_qr(cleaner_link(base, cleaner_code)))
                print(cleaner_link(base, cleaner_code))
            else:
                print("The tunnel did not come up; use the LAN links below.")
                base = None
        else:
            print()
            print("cloudflared is not installed, so the phone cannot use")
            print("the in-page camera or voice notes. Install it with:")
            print("  Windows: winget install --id Cloudflare.cloudflared")
            print("  macOS:   brew install cloudflared")
            base = None
        if not base:
            ip = lan_ip()
            print(f"Laptop (host):  {host_link(f'http://{ip}:{port}', host_code)}")
            print(f"Phone (cleaner, file picker only): "
                  f"{cleaner_link(f'http://{ip}:{port}', cleaner_code)}")
        print()
        print("Keep this window open; press Ctrl+C to stop.")
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop(tunnel)
        _stop(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
