"""Download Alex's own Airbnb listing photos for example pictures.

The listing photos are wide, clean shots of rental-1. They are saved
under data/photos/Airbnb/ (data/ is gitignored; nothing is committed).

Run from the project root:
    .venv\\Scripts\\python scripts/fetch_listing_photos.py
"""

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE = (
    "https://a0.muscache.com/im/pictures/hosting/"
    "Hosting-1613226994381357286/original/"
)
OUT_DIR = Path("data/photos/Airbnb")

# name -> (listing file, width)
PHOTOS = {
    "Exterior_1": ("d00cb67b-5bbf-48fc-b6c5-f380d7e27962.jpeg", 2560),
    "Additional_1": ("8b220880-0c25-47b6-8f4c-68dafcc415ec.jpeg", 1440),
    "Additional_5": ("02a79134-2028-4b64-83d9-0d29156aa354.jpeg", 1440),
    "Full_kitchen_1": ("5b541df9-b40b-495c-b186-2182956971a7.jpeg", 1440),
    "Full_kitchen_4": ("75b3aba9-2a73-496e-8975-9aba310981fc.jpeg", 1440),
    "Living_room_1": ("d62dd470-dae8-45ed-b74f-f66b321e390d.jpeg", 1440),
    "Dining_area_2": ("065c60d7-f1b6-4d5f-9bbd-4ca4c5700af1.jpeg", 1440),
    "Bedroom_1_6": ("c7719137-f6bb-4dc4-a454-c039a2af485b.jpeg", 1440),
    "Bedroom_2_1": ("4603192c-e639-4c83-9d27-02887667ce8c.jpeg", 1440),
    "Bedroom_3_1": ("4258ca19-6ba0-4044-af4c-6906699e6cc1.jpeg", 1440),
    "Full_bathroom_1_1": ("c6455ff8-07ee-4c3f-a4e7-0a89443d73eb.jpeg", 1440),
    "Full_bathroom_2_1": ("f9771c1b-9bf6-4dae-a33d-0865759be5a2.jpeg", 1440),
    "Full_bathroom_2_2": ("cf1c4f49-a587-4163-a258-bb2e81a88991.jpeg", 1440),
}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, (filename, width) in PHOTOS.items():
        url = f"{BASE}{filename}?im_w={width}"
        request = urllib.request.Request(
            url, headers={"User-Agent": "proof-of-turnover example fetch"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        if not data.startswith(b"\xff\xd8"):
            raise RuntimeError(f"{name}: download is not a JPEG")
        path = OUT_DIR / f"{name}.jpeg"
        path.write_bytes(data)
        print(f"{name}.jpeg  {len(data)} bytes")


if __name__ == "__main__":
    main()
