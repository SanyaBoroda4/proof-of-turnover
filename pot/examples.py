"""Example photos: how each tour shot should look.

Stored in data/examples/<property_id>/<shot_id>.jpg (1200 px long
side), served to the phone behind the access code. Rental-1's examples
are picked by hand (data/inputs/examples_map.json); new examples are
learned when the host approves a turnover (the photo that proved a
shot's critical items), and setup-made houses start with each room's
first setup photo on every shot of that room.
"""

from pathlib import Path

from PIL import Image, ImageOps

from pot import config
from pot.proof import PROVEN

#: Long side of a stored example photo, in pixels.
EXAMPLE_LONG_SIDE = 1200


def example_path(property_id, shot_id):
    """Filesystem path of one example photo (may not exist yet)."""
    return Path(config.EXAMPLES_DIR) / property_id / f"{shot_id}.jpg"


def has_example(property_id, shot_id):
    """True when the shot already has an example photo."""
    return example_path(property_id, shot_id).is_file()


def save_example(property_id, shot_id, source):
    """Store source (path or bytes) as the shot's example; returns the path."""
    if isinstance(source, (bytes, bytearray)):
        from io import BytesIO

        handle = Image.open(BytesIO(bytes(source)))
    else:
        handle = Image.open(source)
    with handle:
        handle.load()
        image = ImageOps.exif_transpose(handle).convert("RGB")
    image.thumbnail((EXAMPLE_LONG_SIDE, EXAMPLE_LONG_SIDE), Image.LANCZOS)
    path = example_path(property_id, shot_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=88)
    return path


def read_example(property_id, shot_id):
    """The example photo bytes, or None when the shot has none."""
    path = example_path(property_id, shot_id)
    if not path.is_file():
        return None
    return path.read_bytes()


def proving_photo_for_shot(state, shot):
    """The run photo that proved all the shot's critical items, or None.

    Only a single photo that proved every critical item of the shot
    counts: split or missing proof learns nothing.
    """
    critical = []
    for item_id in shot.get("item_ids", []):
        item = state.items_by_id.get(item_id)
        if item is not None and item.critical:
            critical.append(item_id)
    if not critical:
        return None
    photos = set()
    for item_id in critical:
        proof = state.proofs.get(item_id)
        if proof is None or proof.status != PROVEN or not proof.photo_id:
            return None
        photos.add(proof.photo_id)
    if len(photos) != 1:
        return None
    return state.photos_by_id.get(next(iter(photos)))
