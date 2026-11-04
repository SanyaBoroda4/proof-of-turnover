"""Judge replay: run the recorded demo turn with no key and no network.

Sets POT_REPLAY=1 (every model answer comes from replay/model_cache),
POT_VOICE=device (phone voice), demo access codes and a fresh temp runs
folder (never data/runs), starts the server, walks replay/map.json with
scripts/rehearsal.py --demo-run + replay/actions.json, prints the
cleaner and host links for the run, and keeps serving until Ctrl+C.

Usage:
    python scripts/replay_demo.py [--port 8000]
"""

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLAY_DIR = ROOT / "replay"


def build_env(runs_dir):
    """Environment for the replay server (no key, no network calls)."""
    env = dict(os.environ)
    env["POT_REPLAY"] = "1"
    env["POT_VOICE"] = "device"
    env["POT_MODEL_CACHE_DIR"] = str(REPLAY_DIR / "model_cache")
    env["POT_ACCESS_CODE"] = "demo"
    env["POT_HOST_CODE"] = "demohost"
    env["POT_RUNS_DIR"] = str(runs_dir)
    env.pop("MODEL_API_KEY", None)
    return env


def judge_links(port, run_id):
    return (
        f"http://127.0.0.1:{port}/clean?code=demo&run={run_id}",
        f"http://127.0.0.1:{port}/host?code=demohost&run={run_id}",
    )


def find_run_id(runs_dir):
    runs = sorted(
        p.name for p in Path(runs_dir).iterdir()
        if p.is_dir() and p.name.startswith("run-")
    )
    if len(runs) != 1:
        raise SystemExit(
            f"expected exactly one replay run in {runs_dir}, got {runs}")
    return runs[0]


def wait_for_server(port, proc, timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"server exited early (code {proc.returncode})")
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=2)
            return
        except urllib.error.HTTPError:
            return  # any HTTP answer means the server is up
        except OSError:
            time.sleep(0.5)
    raise SystemExit(f"server did not start on port {port}")


def main(argv):
    parser = argparse.ArgumentParser(description="No-key judge replay.")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    actions = REPLAY_DIR / "actions.json"
    if not (REPLAY_DIR / "map.json").is_file() or not actions.is_file():
        print("replay/map.json and replay/actions.json must exist; "
              "record them first (Step 55e).", file=sys.stderr)
        return 1

    runs_dir = Path(tempfile.mkdtemp(prefix="pot-judge-runs-"))
    env = build_env(runs_dir)
    print(f"Replay runs folder: {runs_dir}", flush=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "pot.server", "--port", str(args.port)],
        cwd=str(ROOT), env=env,
    )
    try:
        wait_for_server(args.port, server)
        walk = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "rehearsal.py"),
             "--base", f"http://127.0.0.1:{args.port}",
             "--code", "demo",
             "--map", "replay/map.json",
             "--demo-run",
             "--actions", "replay/actions.json",
             "--pace", "0", "--language", "en"],
            cwd=str(ROOT),
        )
        if walk.returncode != 0:
            print("replay walk failed", file=sys.stderr)
            return walk.returncode
        run_id = find_run_id(runs_dir)
        cleaner, host = judge_links(args.port, run_id)
        print(f"\nCleaner link for this run: {cleaner}")
        print(f"Host link: {host}")
        print("Serving the replay until Ctrl+C.", flush=True)
        server.wait()
    except KeyboardInterrupt:
        print("\nStopping the replay server.")
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
