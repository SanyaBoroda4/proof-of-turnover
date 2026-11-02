"""Save the example photos named in data/inputs/examples_map.json.

For each shot with a "file", the photo is stored with
pot.examples.save_example("rental-1", shot_id, ...). When an entry has
"crop": [left, top, right, bottom] (fractions of width and height), the
crop is applied (after EXIF transpose) before saving. An entry with
"temporary": true also gets a <shot>.temporary marker next to the
example (removed again for entries without it), so Approve knows the
stand-in may be replaced by the first real clean photo. Only the shots
the map names are written; no example photo is ever deleted.

Run from the project root:

    .venv\\Scripts\\python scripts\\apply_examples.py
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageOps  # noqa: E402

from pot.examples import save_example, set_temporary  # noqa: E402

MAP = os.path.join("data", "inputs", "examples_map.json")
PROPERTY = "rental-1"


def cropped_bytes(entry):
    """The entry's photo with its fractional crop applied, as JPEG bytes."""
    with Image.open(entry["file"]) as handle:
        handle.load()
        image = ImageOps.exif_transpose(handle).convert("RGB")
    left, top, right, bottom = entry["crop"]
    width, height = image.size
    image = image.crop((int(left * width), int(top * height),
                        int(right * width), int(bottom * height)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=95)
    return buffer.getvalue()


def main():
    with open(MAP, encoding="utf-8") as handle:
        entries = json.load(handle)["shots"]
    for shot_id, entry in entries.items():
        if not entry.get("file"):
            continue
        source = cropped_bytes(entry) if entry.get("crop") else entry["file"]
        print(f"{shot_id}: {save_example(PROPERTY, shot_id, source)}")
        set_temporary(PROPERTY, shot_id, bool(entry.get("temporary")))


if __name__ == "__main__":
    main()
