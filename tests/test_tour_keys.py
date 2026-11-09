"""The first-run tour's keyboard controls, run through the real app/static/tour.js
by tests/tour_keys.js. Needs node, which GitHub's runners come with."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).with_name("tour_keys.js")
PAGE_COUNT = 5


def press(key, times):
    node = shutil.which("node")
    assert node, "node is needed to run the tour's keyboard tests"
    result = subprocess.run(
        [node, str(HARNESS), key, str(times)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("key", [" ", "Enter", "ArrowRight"])
def test_key_moves_the_tour_forward_one_page(key):
    presses = press(key, 1)
    assert presses[0]["progress"] == f"2 of {PAGE_COUNT}"


@pytest.mark.parametrize("key", [" ", "Enter"])
def test_key_does_not_also_click_the_focused_button(key):
    presses = press(key, 1)
    assert presses[0]["defaultPrevented"]


def test_space_walks_every_page_then_closes_the_tour():
    presses = press(" ", PAGE_COUNT)
    expected_progress = []
    for page_number in range(2, PAGE_COUNT + 1):
        expected_progress.append(f"{page_number} of {PAGE_COUNT}")
    expected_progress.append(f"{PAGE_COUNT} of {PAGE_COUNT}")

    shown_progress = []
    for keypress in presses:
        shown_progress.append(keypress["progress"])
    assert shown_progress == expected_progress

    assert presses[PAGE_COUNT - 2]["tourOpen"]
    assert not presses[PAGE_COUNT - 1]["tourOpen"]
