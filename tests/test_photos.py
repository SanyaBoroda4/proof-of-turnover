"""Offline tests for pot.photos with Pillow-built images (no people)."""

from datetime import datetime

from PIL import Image
from PIL.Image import Exif

from pot.photos import load_photo_set


def _jpeg(path, size, color, orientation=None, taken=None):
    img = Image.new("RGB", size, color)
    exif = Exif()
    if orientation is not None:
        exif[274] = orientation
    if taken is not None:
        exif[36867] = taken
    img.save(path, "JPEG", exif=exif)


def _incoming(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    _jpeg(incoming / "morning.jpg", (100, 70), "blue", taken="2026:10:30 09:00:00")
    # Stored 120x80 with EXIF orientation 6: upright it is 80x120.
    _jpeg(incoming / "rot.jpg", (120, 80), "red", orientation=6, taken="2026:10:30 10:00:00")
    Image.new("RGB", (60, 40), "green").save(incoming / "a.png")
    (incoming / "b.png").write_bytes((incoming / "a.png").read_bytes())
    (incoming / "notes.txt").write_text("not an image")
    return incoming


def test_ids_follow_time_order_and_rotation_fixed(tmp_path):
    photos = load_photo_set(_incoming(tmp_path), tmp_path / "run")
    assert [p.id for p in photos] == ["P01", "P02", "P03"]
    assert [p.original_name for p in photos] == ["morning.jpg", "rot.jpg", "a.png"]
    assert photos[0].taken_at == datetime(2026, 10, 30, 9, 0, 0)
    assert photos[2].taken_at is None
    rotated = photos[1]
    assert (rotated.width, rotated.height) == (80, 120)
    with Image.open(rotated.check_path) as saved:
        assert saved.size == (80, 120)
    assert rotated.overview_path.name == "P02_overview.jpg"
    assert rotated.check_path.parent == tmp_path / "run" / "photos"
    for photo in photos:
        assert photo.received_at is not None
        assert photo.check_path.is_file()
        assert photo.overview_path.is_file()


def test_duplicate_and_non_image_skipped_with_reasons(tmp_path):
    photos = load_photo_set(_incoming(tmp_path), tmp_path / "run")
    skipped = {entry.name: entry.reason for entry in photos.skipped}
    assert set(skipped) == {"b.png", "notes.txt"}
    assert "duplicate" in skipped["b.png"]
    assert "a.png" in skipped["b.png"]
    assert "not an image" in skipped["notes.txt"]


def test_accepts_list_of_files(tmp_path):
    incoming = _incoming(tmp_path)
    photos = load_photo_set([incoming / "a.png", incoming / "morning.jpg"], tmp_path / "run")
    assert [p.id for p in photos] == ["P01", "P02"]
    assert [p.original_name for p in photos] == ["morning.jpg", "a.png"]
