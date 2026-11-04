"""In-page navigation links must open their screen directly.

Regression test for Step 46: #changeLang, #toChoice and the setup
room Back button used to call history.back(), so after a run each tap
only stepped one history entry instead of opening the target screen.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _script(page):
    html = (ROOT / "web" / page).read_text(encoding="utf-8")
    return re.search(r"<script>(.*)</script>", html, re.S).group(1)


def _handler(script, element_id):
    match = re.search(
        rf'getElementById\("{element_id}"\)\.onclick = .*?\n  \}};',
        script, re.S)
    assert match, f"#{element_id} handler not found"
    return match.group(0)


def test_language_and_choice_links_open_screens_directly():
    script = _script("clean.html")
    assert "history.back" not in script
    assert "goBack" not in script

    language = _handler(script, "changeLang")
    assert 'langPicked = false' in language
    assert 'navPush("language")' in language

    choice = _handler(script, "toChoice")
    assert 'modePicked = ""' in choice
    assert 'navPush("choice")' in choice


def test_setup_next_room_on_own_full_width_row():
    script = _script("setup.html")
    # Next room is no longer inside the Back/Skip rowbtns row.
    assert 'id="rnext"' in script
    row_start = script.index('<div class="rowbtns">\n    <button class="btn secondary grow" id="rback">')
    row = script[row_start:script.index("</div>", row_start)]
    assert 'id="rback"' in row and 'id="rskip"' in row
    assert 'id="rnext"' not in row


def test_host_follow_mode_does_not_pin_run_in_url():
    script = _script("host.html")
    assert 'params.get("follow") === "1"' in script
    use_run = script[script.index("function useRun"):script.index("async function fetchRunsList")]
    assert "if (FOLLOW) return;" in use_run


def test_clean_taken_line_is_centered():
    html = (ROOT / "web" / "clean.html").read_text(encoding="utf-8")
    assert 'class="small centerline">${esc(T().skippedLine' in html


def test_setup_room_back_goes_straight_to_previous_room():
    script = _script("setup.html")
    assert "history.back" not in script
    assert "goBack" not in script

    back = _handler(script, "rback")
    assert "roomIdx--" in back
    assert 'screen = "review"' in back
    assert "navPush()" in back


def test_host_empty_page_shows_setup_link_and_qr():
    script = _script("host.html")
    assert "No turnovers yet." in script
    assert "/setup?code=" in script and "Set up a new house" in script
    assert "scan this QR code and tap Start turnover" in script
    assert 'id="qr"' in script and 'id="cleanUrl"' in script
    # The empty block is built once (showEmpty guards it), so the 2 s
    # poll never rebuilds the QR image and makes it flicker.
    assert "function showEmpty" in script and "emptyShown" in script
