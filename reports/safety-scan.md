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
