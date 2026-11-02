"""Photo intake: load a turnover's photo set into a run folder.

Each photo is rotated upright from EXIF, stamped with taken_at (EXIF, when
present) and received_at (arrival time, so old photos cannot be passed off
as new), sorted by taken_at then file name, and saved as two upright
JPEGs: a check copy (max 1600 px long side) and an overview copy
(max 768 px long side).
"""

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple, Union

from PIL import Image, ImageOps

from pot import config

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - pillow-heif is a dependency
    pass

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
Image.MAX_IMAGE_PIXELS = 60_000_000  # bound against decompression bombs
CHECK_LONG_SIDE = 1600
OVERVIEW_LONG_SIDE = 768

_EXIF_IFD_POINTER = 0x8769
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME_DIGITIZED = 36868
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


def normalize_photo_id(value):
    """Normalize a photo id cited by the model: "Photo P07" -> "P07".

    Strips a leading "Photo", spaces and quotes, and uppercases, so the
    label the model was shown ("Photo P07"), "p07" and "P07" all match.
    """
    text = str(value or "").strip().strip("\"'").strip()
    if text.lower().startswith("photo"):
        text = text[len("photo") :].strip().strip("\"'").strip()
    return text.upper()


class PhotoSet(list):
    """List of Photo with a .skipped record of Skipped(name, reason)."""

    def __init__(self, photos=(), skipped=()):
        super().__init__(photos)
        self.skipped = list(skipped)


def _taken_at(image):
    """Taken time: Exif sub-IFD original/digitized first, then main DateTime.

    Phones store DateTimeOriginal in the Exif sub-IFD (0x8769); the main
    DateTime (306) becomes the edit time once a photo is edited.
    """
    try:
        exif = image.getexif()
    except Exception:
        return None
    try:
        sub = exif.get_ifd(_EXIF_IFD_POINTER)
    except Exception:
        sub = {}
    for source, tag in (
        (sub, _EXIF_DATETIME_ORIGINAL),
        (sub, _EXIF_DATETIME_DIGITIZED),
        (exif, _EXIF_DATETIME),
    ):
        value = source.get(tag)
        if not value:
            continue
        try:
            return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None


def read_taken_at(source_path):
    """Camera time of an image file, or None when it cannot be read."""
    try:
        with Image.open(source_path) as handle:
            handle.load()
            return _taken_at(handle)
    except Exception:
        return None


def _save_jpeg(image, path, long_side):
    copy = image.copy()
    copy.thumbnail((long_side, long_side), Image.LANCZOS)
    copy.save(path, "JPEG", quality=90)


def _is_upright(handle):
    """True when no EXIF rotation applies (orientation missing or 1)."""
    try:
        orientation = handle.getexif().get(274)
    except Exception:
        return True
    return orientation in (None, 1)


def ingest_file(source_path, photos_dir, photo_id, keep_bytes=False):
    """Ingest one photo file as an upright check+overview pair; returns a Photo.

    With keep_bytes (demo mode), an uploaded JPEG that is already check
    size and upright is kept byte for byte, so the recorded request matches
    exactly on replay.
    """
    source = Path(source_path)
    if not source.is_file():
        raise ValueError(f"Photo not found: {source}")
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Not an image (extension {source.suffix or 'none'}): {source.name}")
    try:
        with Image.open(source) as handle:
            handle.load()
            taken = _taken_at(handle)
            if (
                keep_bytes
                and source.suffix.lower() in (".jpg", ".jpeg")
                and max(handle.size) <= CHECK_LONG_SIDE
                and _is_upright(handle)
            ):
                width, height = handle.size
                image = None
            else:
                image = ImageOps.exif_transpose(handle).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Cannot be read as an image ({exc}): {source.name}") from exc
    if image is not None:
        width, height = image.size
    received = datetime.now()
    photos_dir = Path(photos_dir)
    photos_dir.mkdir(parents=True, exist_ok=True)
    check_path = photos_dir / f"{photo_id}_check.jpg"
    overview_path = photos_dir / f"{photo_id}_overview.jpg"
    if image is None:
        shutil.copyfile(source, check_path)
    else:
        image.thumbnail((CHECK_LONG_SIDE, CHECK_LONG_SIDE), Image.LANCZOS)
        image.save(check_path, "JPEG", quality=90)
    with Image.open(check_path) as saved:
        saved.load()
        _save_jpeg(saved, overview_path, OVERVIEW_LONG_SIDE)
    return Photo(
        id=photo_id,
        original_name=source.name,
        taken_at=taken,
        received_at=received,
        check_path=check_path,
        overview_path=overview_path,
        width=width,
        height=height,
    )


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

    # Pass 1: hash, stamp and shrink each photo, then drop the full-size
    # image, so memory stays flat (ids are assigned after sorting in pass 2).
    loaded = []  # (taken, name, taken, received, width, height, tmp check path)
    skipped = []
    seen_pixels = {}
    for index, path in enumerate(files):
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
        width, height = image.size
        received = datetime.now()
        tmp_path = photos_dir / f"_tmp_{index:04d}.jpg"
        image.thumbnail((CHECK_LONG_SIDE, CHECK_LONG_SIDE), Image.LANCZOS)
        image.save(tmp_path, "JPEG", quality=90)
        del image
        loaded.append((taken, path.name, taken, received, width, height, tmp_path))

    # Pass 2: sort, assign ids, rename the check copies, derive overviews.
    loaded.sort(key=lambda item: (item[0] is None, item[0] or datetime.min, item[1]))
    result = PhotoSet(skipped=skipped)
    for number, (_, name, taken, received, width, height, tmp_path) in enumerate(loaded, start=1):
        photo_id = f"P{number:02d}"
        check_path = photos_dir / f"{photo_id}_check.jpg"
        overview_path = photos_dir / f"{photo_id}_overview.jpg"
        tmp_path.replace(check_path)
        with Image.open(check_path) as saved:
            saved.load()
            _save_jpeg(saved, overview_path, OVERVIEW_LONG_SIDE)
        result.append(
            Photo(
                id=photo_id,
                original_name=name,
                taken_at=taken,
                received_at=received,
                check_path=check_path,
                overview_path=overview_path,
                width=width,
                height=height,
            )
        )
    return result


def new_run_dir():
    """Create a fresh timestamped folder under the runs dir."""
    runs = config.RUNS_DIR
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
    run_dir = new_run_dir()
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
