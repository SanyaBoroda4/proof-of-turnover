"""Offline tests for scripts/make_demo.py with generated images."""

import json
import sys
from pathlib import Path

from PIL import Image
from PIL.Image import Exif

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from make_demo import build_bundle


def _incoming(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2000, 100), "blue").save(directory / "wide.jpg", "JPEG")
    exif = Exif()
    exif[274] = 6
    exif.get_ifd(0x8769)[36867] = "2026:10:30 09:00:00"
    Image.new("RGB", (120, 80), "red").save(directory / "rot.jpg", "JPEG", exif=exif)
    (directory / "notes.txt").write_text("not an image")
    return directory


def test_build_bundle_is_check_size_upright_and_meta_free(tmp_path):
    manifest = build_bundle(_incoming(tmp_path / "in"), tmp_path / "bundle")
    assert [entry["bundle"] for entry in manifest] == ["01.jpg", "02.jpg"]
    assert [entry["source"] for entry in manifest] == ["rot.jpg", "wide.jpg"]
    for entry in manifest:
        path = tmp_path / "bundle" / "photos" / entry["bundle"]
        with Image.open(path) as image:
            image.load()
            assert max(image.size) <= 1600
            assert dict(image.getexif()) == {}
    with Image.open(tmp_path / "bundle" / "photos" / "01.jpg") as rotated:
        assert rotated.size == (80, 120)
    saved = json.loads((tmp_path / "bundle" / "manifest.json").read_text(encoding="utf-8"))
    assert saved["photos"] == manifest


def test_build_bundle_rejects_non_folder(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="Not a folder"):
        build_bundle(tmp_path / "missing", tmp_path / "bundle")
