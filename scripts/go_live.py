"""One-command live walkthrough in your own house.

    python scripts/go_live.py [--port 8000]

Starts the server with fresh host/cleaner access codes (or the ones
from POT_HOST_CODE / POT_ACCESS_CODE), opens a Cloudflare quick tunnel
when cloudflared is installed, opens the host page in the laptop's
browser and prints the links (the host page shows the QR code for the
phone). The server reads .env itself; this script never reads .env
and never prints any key. Ctrl+C stops the tunnel and the server.
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
import webbrowser
from pathlib import Path

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def parse_tunnel_url(text):
    """The first trycloudflare URL in cloudflared's output, or None."""
    match = TUNNEL_RE.search(text or "")
    return match.group(0) if match else None


def host_link(base, code):
    return f"{base}/host?code={code}"


def cleaner_link(base, code):
    return f"{base}/clean?code={code}"


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


def _open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _stop(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


CLOUDFLARED_WINDOWS = (
    Path(r"C:\Program Files (x86)") / "cloudflared" / "cloudflared.exe",
    Path(r"C:\Program Files") / "cloudflared" / "cloudflared.exe",
)


def find_cloudflared():
    """cloudflared on PATH, or where winget installs it on Windows."""
    found = shutil.which("cloudflared")
    if found:
        return found
    if os.name == "nt":
        for candidate in CLOUDFLARED_WINDOWS:
            if candidate.is_file():
                return str(candidate)
    return None


def server_host_for(cloudflared):
    """Server bind address: local-only with a tunnel, LAN without."""
    return "127.0.0.1" if cloudflared else "0.0.0.0"


def _port_busy(port, bind_host):
    """Busy: something answers on 127.0.0.1:port, or a test bind fails."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        pass
    probe = socket.socket()
    try:
        probe.bind((bind_host, port))
    except OSError:
        return True
    finally:
        probe.close()
    return False


def find_free_port(start, bind_host="127.0.0.1", tries=20):
    """First free port at or after start, or None."""
    for port in range(start, start + tries):
        if not _port_busy(port, bind_host):
            return port
    return None


def _watch_tunnel(proc, holder, ready):
    # readline, not iteration: iterating a pipe can stall on Windows.
    # Keep reading to the end after the link is found: cloudflared
    # keeps logging, and an unread pipe fills up and freezes it.
    while True:
        line = proc.stdout.readline()
        if not line:
            return
        url = parse_tunnel_url(line)
        if url and "url" not in holder:
            holder["url"] = url
            ready.set()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    env = dict(os.environ)
    host_code = env.get("POT_HOST_CODE") or secrets.token_urlsafe(8)
    cleaner_code = env.get("POT_ACCESS_CODE") or secrets.token_urlsafe(8)
    env["POT_HOST_CODE"] = host_code
    env["POT_ACCESS_CODE"] = cleaner_code

    cloudflared = find_cloudflared()
    host = server_host_for(cloudflared)
    port = find_free_port(args.port, bind_host=host)
    if port is None:
        print(f"No free port found from {args.port} to {args.port + 19}. "
              "Close another program and run go_live.py again.", flush=True)
        return 1
    if port != args.port:
        print(f"Port {args.port} is busy (another program uses it), "
              f"using {port}.", flush=True)

    server = subprocess.Popen(
        [sys.executable, "-m", "pot.server", "--host", host,
         "--port", str(port)], env=env)
    tunnel = None
    try:
        if not wait_for_url(f"http://127.0.0.1:{port}/", timeout=30):
            print("The server did not start; see its messages above.",
                  flush=True)
            return 1
        if cloudflared:
            tunnel = subprocess.Popen(
                [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
            holder, ready = {}, threading.Event()
            watcher = threading.Thread(
                target=_watch_tunnel, args=(tunnel, holder, ready),
                daemon=True)
            watcher.start()
            ready.wait(timeout=90)
            base = holder.get("url")
            if not base:
                print("The tunnel did not start (check the internet). "
                      "Press Ctrl+C and run go_live.py again.", flush=True)
                print(f"Laptop (host):  "
                      f"{host_link(f'http://127.0.0.1:{port}', host_code)}",
                      flush=True)
            else:
                # A new trycloudflare name can take minutes to resolve;
                # asking too early poisons the DNS cache for ~60 s.
                print("Waiting for the tunnel link to open "
                      "(can take 1-3 minutes)...", flush=True)
                time.sleep(10)
                opened = wait_for_url(base, timeout=180)
                print(flush=True)
                if opened:
                    _open_browser(host_link(base, host_code))
                    print(f"Laptop (host):  {host_link(base, host_code)} "
                          "(opened in your browser)", flush=True)
                    print("Phone (cleaner): scan the QR code on the host "
                          f"page, or open {cleaner_link(base, cleaner_code)}",
                          flush=True)
                else:
                    print(f"Laptop (host):  {host_link(base, host_code)}",
                          flush=True)
                    print(f"Phone (cleaner): "
                          f"{cleaner_link(base, cleaner_code)}", flush=True)
                    print("Then scan the QR code on the host page with "
                          "your phone.", flush=True)
                    print("The link did not open from this laptop yet. "
                          "Wait a minute and try it. If it still does not "
                          "open, press Ctrl+C and run go_live.py again.",
                          flush=True)
        else:
            print(flush=True)
            print("cloudflared is not installed, so the phone cannot use",
                  flush=True)
            print("the in-page camera or voice notes. Install it with:",
                  flush=True)
            print("  Windows: winget install --id Cloudflare.cloudflared",
                  flush=True)
            print("  macOS:   brew install cloudflared", flush=True)
            print("If you just installed it, open a new terminal window "
                  "and run go_live.py again.", flush=True)
            print("If your computer asks whether to allow incoming "
                  "connections, choose Allow.", flush=True)
            ip = lan_ip()
            laptop = host_link(f"http://{ip}:{port}", host_code)
            _open_browser(laptop)
            print(f"Laptop (host):  {laptop} (opened in your browser)",
                  flush=True)
            print("Phone (cleaner, file picker only): scan the QR code "
                  "on the host page, or open "
                  f"{cleaner_link(f'http://{ip}:{port}', cleaner_code)}",
                  flush=True)
        print(flush=True)
        print("Keep this window open; press Ctrl+C to stop.", flush=True)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop(tunnel)
        _stop(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
