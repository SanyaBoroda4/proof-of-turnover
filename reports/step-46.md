# Step 46: language link opens at once, powder examples, replaceable stand-ins

## What changed

- **Direct in-page navigation.** The cleaner page's “Idioma · Language · Язык” link now opens the language screen directly (`langPicked = false`, `navPush("language")`), and the houses screen “Back” link opens the choice screen directly (`modePicked = ""`, `navPush("choice")`). The setup room “Back” button goes straight to the previous room — or the review screen from the first room — with its own `navPush()`. No in-page link calls `history.back()` anymore; `goBack()` was removed from both pages. The phone's own back gesture still works through the existing `popstate` handling.
- **Temporary stand-in examples.** Example-map entries can carry `"temporary": true`. `scripts/apply_examples.py` writes a `<shot>.temporary` marker next to the example for those entries and removes the marker for entries without it. `pot/examples.py` gained `is_temporary()` (plus marker path/set helpers). On Approve, a shot's example is now learned when the shot has none **or** its example is temporary, and the marker is deleted after learning; curated examples are still never overwritten.
- **Powder room examples.** The two new powder-room photos (checked: no person, no reflection) are mapped as temporary stand-ins: powder-00 (whole room from the doorway), powder-02 (cropped to the toilet-paper holder), and powder-03 (sink and mirror). The existing stand-ins master-bathroom-01 and hall-bathroom-01 (powder-toilet photos) and kitchen-01 (listing photo) are also marked temporary, so the first approved real photo replaces them.
- Applied the map locally: **37 example files** and **6 `.temporary` markers** (powder-00, powder-02, powder-03, master-bathroom-01, hall-bathroom-01, kitchen-01). Shots still without an example: laundry-01, kitchen-03, kitchen-04, kitchen-06, kitchen-12, living-01. Nothing under `data/` is committed.

## Tests

- New `tests/test_navigation.py`: the three handlers open their screens directly and neither page script contains `history.back()`/`goBack()` anymore.
- New server test: Approve replaces a temporary example and removes its marker; the existing test still proves Approve never replaces a curated example.
- Full suite: **313 passed**. Both changed page scripts pass `node --check`.
