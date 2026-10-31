"""Build the judge demo photo bundle: check-size JPEGs with no metadata.

Usage: python scripts/make_demo.py <photo folder> [--out data/demo-build]

Sorts the supported images by name, rotates them upright, shrinks them
to check size, and saves meta-free JPEGs as 01.jpg, 02.jpg, ... plus a
manifest.json mapping bundle names back to sources. The recorded model
cache gets added on Tuesday, after the last code change; every photo
gets checked before anything goes public.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageOps

from pot import config
from pot.photos import CHECK_LONG_SIDE, IMAGE_EXTENSIONS


def build_bundle(source_dir, out_dir):
    """Normalize a folder of photos into a meta-free bundle; returns manifest."""
    source = Path(source_dir)
    if not source.is_dir():
        raise ValueError(f"Not a folder: {source}")
    out = Path(out_dir)
    if not out.is_absolute():
        out = config.PROJECT_ROOT / out
    photos_dir = out / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (p for p in source.iterdir()
         if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )
    manifest = []
    for number, path in enumerate(files, start=1):
        with Image.open(path) as handle:
            handle.load()
            image = ImageOps.exif_transpose(handle).convert("RGB")
        image.thumbnail((CHECK_LONG_SIDE, CHECK_LONG_SIDE), Image.LANCZOS)
        dest = photos_dir / f"{number:02d}.jpg"
        image.save(dest, "JPEG", quality=90)  # no exif: no metadata kept
        manifest.append({"bundle": dest.name, "source": path.name,
                         "size": list(image.size)})
    (out / "manifest.json").write_text(
        json.dumps({"photos": manifest}, indent=2), encoding="utf-8"
    )
    return manifest


def main(argv):
    parser = argparse.ArgumentParser(prog="scripts.make_demo")
    parser.add_argument("folder")
    parser.add_argument("--out", default="data/demo-build")
    args = parser.parse_args(argv[1:])
    try:
        manifest = build_bundle(args.folder, args.out)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Bundle: {args.out} ({len(manifest)} photos)")
    for entry in manifest:
        print(f"  {entry['bundle']} <- {entry['source']} ({entry['size'][0]}x{entry['size'][1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
