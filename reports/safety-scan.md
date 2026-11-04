# Safety scan of the git history (Step 55g)

Commits scanned: 81 (`git rev-list --all`).

## .env and ignored data

- `.env` ever committed: never (`git log --all -- .env` is empty).
- `.gitignore` covers `.env`: yes; covers `data/`: yes.

## Secret-shaped text in history

No API keys or tokens in any commit. `MODEL_API_KEY=` appears only as code that reads the environment (`pot/config.py: MODEL_API_KEY = os.environ.get(...)`, `.env.example` placeholder), never with a literal value. No `Bearer ` tokens and no key-shaped strings (`sk-`, `ghp_`, `AKIA`, `xai-`). Long random-looking strings occur only as base64 house-photo data inside recorded model requests (`replay/model_cache`) — photos, not credentials.

## Image files ever committed (for manual review)

| Path | First commit |
|---|---|
| properties/rental-1/house-plan.png | 66b3c37b2 |
| replay/photos/01.jpg | 961e0b549 |
| replay/photos/02.jpg | 961e0b549 |
| replay/photos/03.jpg | 961e0b549 |
| replay/photos/04.jpg | 961e0b549 |
| replay/photos/05.jpg | 961e0b549 |
| replay/photos/06.jpg | 961e0b549 |
| replay/photos/07.jpg | 961e0b549 |
| replay/photos/08.jpg | 961e0b549 |
| replay/photos/09.jpg | 961e0b549 |
| replay/photos/10.jpg | 961e0b549 |
| replay/photos/11.jpg | 961e0b549 |

Total image files: 12. House photos under `data/` were never committed (gitignored). The replay photos are Alex's own Nov 2 pictures of the rental, chosen for the judge demo (see Step 55c); `properties/rental-1/house-plan.png` is the house plan diagram.

## Clean-clone test (Step 55h)

- Cloned the pushed repo from GitHub into a fresh temp folder (HEAD `dcbf3b2`), created a new venv and installed `requirements.txt`: OK.
- `python -m pytest tests -q` in the clone: 365 passed (same as the main folder).
- Blocked-network replay: ran `python scripts/replay_demo.py --port 8007` with `HTTPS_PROXY=http://127.0.0.1:9` and `HTTP_PROXY=http://127.0.0.1:9` (a Python request to `https://example.com` under the same env fails — connection refused by the dead proxy). The replay completed end to end:
  - 53 model calls, every one a cache hit (`recorded`); cache misses = 0.
  - Same cards as the recording: only the powder-room toilet paper card left open (one group, photo P02); the laundry card was fixed by the new photo (`laundry.floor_clear` and `laundry.no_dirty_linens` proven on P11, with `*.before.jpg` evidence saved).
  - Muse Wonder second opinions for the two powder items are in the cache (2 files), ready if a judge answers "It's fine".
  - Judge flow verified over the API: powder items resolved as Can't fix -> Out of supplies (200), Finish (200), Approve refused without force (400, skipped areas not photographed), Approve with force (200), run state `done`.
- Two proxy fixes came out of this test and are included in the pushed history: replay child processes bypass proxies for localhost (`df7ea03`), and the replay startup probe bypasses proxies too (`dcbf3b2`).
