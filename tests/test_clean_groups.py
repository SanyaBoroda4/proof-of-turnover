"""Behaviour test for clean.html's one-card-per-photo groups.

Runs the card-group helpers in Node: the shown group is the server
group of the first open item, limited to items still open and not
awaiting a retake check; "N to fix" counts groups, not items.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PRELUDE = r"""
globalThis.window = globalThis;
let cardOrder = ["a.x", "a.y", "b.z"];
let retakePhotos = {};
let status = {
  cards: ["a.x", "a.y", "b.z"],
  card_groups: [
    {key: "P1", photo_id: "P1", shot_id: "s1", item_ids: ["a.x", "a.y"]},
    {key: "b.z", photo_id: "", shot_id: "s2", item_ids: ["b.z"]},
  ],
};
function itemsById() { return {}; }
"""

ASSERTIONS = r"""
function eq(got, want, msg) {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
  }
}
eq(shownGroup().item_ids, ["a.x", "a.y"], "shown group");
eq(shownGroup().shot_id, "s1", "group shot");
eq(shownCard(), "a.x", "shown card is first item");
eq(groupsRemaining(), 2, "two groups remain");
retakePhotos = {"a.x": "P9"};
eq(shownGroup().item_ids, ["a.y"], "retaking item is skipped in group");
eq(groupsRemaining(), 2, "groups still counted with one retaking");
retakePhotos = {"a.x": "P9", "a.y": "P9"};
eq(shownGroup().item_ids, ["b.z"], "next group shows");
eq(groupsRemaining(), 1, "one group remains");
"""


def _groups_block():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    start = script.index("function cardGroups()")
    end = script.index("function navPush")
    return script[start:end]


def test_shown_group_and_group_counting():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    result = subprocess.run(
        [node, "-e", PRELUDE + _groups_block() + ASSERTIONS],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_group_card_uses_combined_photo_and_group_actions():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    assert "function groupCardHtml" in html
    assert "/card?items=" in html
    assert 'form.append("retake_items", itemIds.join(","))' in html
    assert "async function resolveGroup" in html
    assert "groupsRemaining()" in html
