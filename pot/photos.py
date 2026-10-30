"""Photo intake: load a turnover's photo set into a run folder.

Each photo is rotated upright from EXIF, stamped with taken_at (EXIF, when
present) and received_at (arrival time, so old photos cannot be passed off
as new), sorted by taken_at then file name, and saved as two upright
JPEGs: a check copy (max 1600 px long side) and an overview copy
(max 768 px long side).
"""

import sys
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple, Union

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - pillow-heif is a dependency
    pass

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
CHECK_LONG_SIDE = 1600
OVERVIEW_LONG_SIDE = 768

_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME = 306


@dataclass
class Photo:
    id: str
    original_name: str
    taken_at: Union[datetime, None]
    received_at: datetime
    check_path: Path
    overview_path: Path
    width: int
    height: int


class Skipped(NamedTuple):
    name: str
    reason: str


class PhotoSet(list):
    """List of Photo with a .skipped record of Skipped(name, reason)."""

    def __init__(self, photos=(), skipped=()):
        super().__init__(photos)
        self.skipped = list(skipped)


def _taken_at(image):
    try:
        exif = image.getexif()
    except Exception:
        return None
    for tag in (_EXIF_DATETIME_ORIGINAL, _EXIF_DATETIME):
        value = exif.get(tag)
        if not value:
            continue
        try:
            return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _save_jpeg(image, path, long_side):
    copy = image.copy()
    copy.thumbnail((long_side, long_side), Image.LANCZOS)
    copy.save(path, "JPEG", quality=90)


def load_photo_set(folder_or_files, run_dir):
    """Load photos into run_dir/photos/, returning a PhotoSet.

    folder_or_files is a directory (top level only) or a list of files.
    Non-images and exact pixel duplicates are skipped and recorded in
    PhotoSet.skipped with the reason.
    """
    if isinstance(folder_or_files, (str, Path)) and Path(folder_or_files).is_dir():
        files = sorted(
            (p for p in Path(folder_or_files).iterdir() if p.is_file()),
            key=lambda p: p.name.lower(),
        )
    elif isinstance(folder_or_files, (str, Path)):
        files = [Path(folder_or_files)]
    else:
        files = [Path(f) for f in folder_or_files]

    photos_dir = Path(run_dir) / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    loaded = []  # (taken_sort, name, Photo-without-id, image)
    skipped = []
    seen_pixels = {}
    for path in files:
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            skipped.append(Skipped(path.name, f"not an image (extension {path.suffix or 'none'})"))
            continue
        try:
            with Image.open(path) as handle:
                handle.load()
                taken = _taken_at(handle)
                image = ImageOps.exif_transpose(handle).convert("RGB")
        except Exception as exc:
            skipped.append(Skipped(path.name, f"cannot be read as an image ({exc})"))
            continue
        digest = sha256(image.tobytes()).hexdigest()
        if digest in seen_pixels:
            skipped.append(Skipped(path.name, f"exact duplicate of {seen_pixels[digest]}"))
            continue
        seen_pixels[digest] = path.name
        loaded.append((taken, path.name, taken, datetime.now(), image))

    loaded.sort(key=lambda item: (item[0] is None, item[0] or datetime.min, item[1]))
    result = PhotoSet(skipped=skipped)
    for number, (_, name, taken, received, image) in enumerate(loaded, start=1):
        photo_id = f"P{number:02d}"
        check_path = photos_dir / f"{photo_id}_check.jpg"
        overview_path = photos_dir / f"{photo_id}_overview.jpg"
        _save_jpeg(image, check_path, CHECK_LONG_SIDE)
        _save_jpeg(image, overview_path, OVERVIEW_LONG_SIDE)
        result.append(
            Photo(
                id=photo_id,
                original_name=name,
                taken_at=taken,
                received_at=received,
                check_path=check_path,
                overview_path=overview_path,
                width=image.width,
                height=image.height,
            )
        )
    return result


def _new_run_dir():
    runs = Path("data/runs")
    runs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("run-%Y%m%d-%H%M%S")
    candidate = runs / stamp
    suffix = 2
    while candidate.exists():
        candidate = runs / f"{stamp}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def main(argv):
    if len(argv) != 2:
        print("Usage: python -m pot.photos <folder>", file=sys.stderr)
        return 1
    folder = Path(argv[1])
    if not folder.is_dir():
        print(f"Not a folder: {folder}", file=sys.stderr)
        return 1
    run_dir = _new_run_dir()
    photos = load_photo_set(folder, run_dir)
    print(f"Run: {run_dir}")
    print(f"{'ID':4} {'File':32} {'Taken at':16} Size")
    for photo in photos:
        taken = photo.taken_at.strftime("%Y-%m-%d %H:%M") if photo.taken_at else "-"
        print(f"{photo.id:4} {photo.original_name:32} {taken:16} {photo.width}x{photo.height}")
    if photos.skipped:
        print(f"Skipped ({len(photos.skipped)}):")
        for entry in photos.skipped:
            print(f"  {entry.name} - {entry.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
