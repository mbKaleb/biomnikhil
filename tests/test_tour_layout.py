"""The first-run tour's layout engine (app/static/tour_layout.js), run by node's
own test runner through tests/tour_layout.test.js. Needs node, which GitHub's
runners come with."""
import shutil
import subprocess
from pathlib import Path

TESTS = Path(__file__).with_name("tour_layout.test.js")


def test_tour_layout_engine_passes_its_node_tests():
    node = shutil.which("node")
    assert node, "node is needed to run the tour's layout tests"
    result = subprocess.run(
        [node, "--test", str(TESTS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
