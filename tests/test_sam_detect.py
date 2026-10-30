"""Tests for pot.sam.detect on a recorded sam-3.1 response (offline)."""

from pathlib import Path

from pot.sam import detect

FIXTURE = Path(__file__).with_name("fixtures") / "sam_three_squares.txt"


def test_fixture_returns_three_boxes():
    boxes = detect(FIXTURE.read_text(encoding="utf-8"))
    assert len(boxes) == 3
    for box in boxes:
        assert set(box) == {"left", "top", "right", "bottom", "confidence"}
    assert {(b["left"], b["top"], b["right"], b["bottom"]) for b in boxes} == {
        (50, 50, 151, 151),
        (480, 100, 581, 201),
        (270, 200, 371, 301),
    }


def test_min_confidence_filter():
    text = FIXTURE.read_text(encoding="utf-8")
    assert len(detect(text, min_confidence=0.5)) == 3
    assert len(detect(text, min_confidence=0.9)) == 3
    assert detect(text, min_confidence=0.99) == []


def test_empty_output_yields_no_boxes():
    assert detect("") == []
    assert detect("   ") == []
