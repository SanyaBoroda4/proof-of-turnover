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
    assert 'notVisible: "Not clear in this photo"' in html
    assert 'notVisible: "No se ve claro en la foto"' in html
    assert 'item.status === "needs photo"' in html
    assert "T().notVisible" in html


def test_shot_of_item_prefers_fewest_items_shot():
    html = _clean()
    assert "(s.item_ids || []).length < (best.item_ids || []).length" in html
    assert "status.recent.find" in html


def test_two_button_row_shrinks_and_wraps_at_360px():
    html = _clean()
    row_rule = html[html.index(".btnrow .btn {"):html.index("}", html.index(".btnrow .btn {"))]
    assert "flex: 1 1 0" in row_rule
    assert "min-width: 0" in row_rule
    assert "white-space: normal" in row_rule
    # The long Russian labels rely on wrapping, not overflow.


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


def test_restock_last_card_present():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    assert "function restockCardHtml" in html
    assert 'id="restockText"' in html
    assert 'id="restockSend"' in html
    assert 'id="restockDone"' in html
    assert '"/notes/text"' in html or "/notes/text" in html
    assert "/restock-done" in html
    assert '"restock"' in html  # kind sent with voice and typed notes
