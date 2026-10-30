"""Regression test: one SAM box+mask pair must count as one object.

Uses a recorded sam-3.1 response for the smoke test's 3-red-squares image,
so this runs offline without an API key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from smoke_test import count_segmented_objects

FIXTURE = Path(__file__).with_name("fixtures") / "sam_three_squares.txt"


def test_three_box_mask_pairs_count_as_three():
    assert count_segmented_objects(FIXTURE.read_text(encoding="utf-8")) == 3
