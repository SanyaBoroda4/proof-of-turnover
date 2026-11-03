"""Step 51e: New photo button, hint line, Not visible tag, spare rolls."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _clean():
    return (ROOT / "web" / "clean.html").read_text(encoding="utf-8")


def test_card_button_is_new_photo_with_hint_in_all_languages():
    html = _clean()
    assert 'fixed: "New photo"' in html
    assert 'fixed: "Foto nueva"' in html
    assert "Fixed it \u2014 or it's there but not in the photo? Take a new photo." in html
    assert "Toma una foto nueva." in html
    assert 'id="cardFixed"' in html
    assert "T().newPhotoHint" in html


def test_needs_photo_cards_say_not_visible():
    html = _clean()
    assert 'notVisible: "Not visible in this photo"' in html
    assert 'notVisible: "No se ve en esta foto"' in html
    assert 'item.status === "needs photo"' in html
    assert "T().notVisible" in html


def test_spare_roll_instructions_show_holder_and_tank():
    tour = json.loads(
        (ROOT / "properties/rental-1/tour.json").read_text(encoding="utf-8"))
    by_id = {s["shot_id"]: s for s in tour["shots"]}
    powder = by_id["powder-02"]
    assert "toilet paper holder and the top of the toilet tank" in powder["instruction_en"]
    assert "spare rolls" in powder["instruction_en"]
    assert powder["instruction_es"] and powder["instruction_ru"]
    hall = by_id["hall-bathroom-02"]
    assert "shelves above it" in hall["instruction_en"]
    assert "toilet paper holder" in hall["instruction_en"]
    assert hall["instruction_es"] and hall["instruction_ru"]
