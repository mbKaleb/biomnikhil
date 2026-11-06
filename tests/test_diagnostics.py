"""app/installer/diagnostics.py records errors from a process with no console."""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = """
import sys
sys.stdout = None
sys.stderr = None
sys.path.insert(0, {installer!r})
import diagnostics
from pathlib import Path
diagnostics.LOG = Path({log!r})
diagnostics.enable()
import threading
thread = threading.Thread(target=lambda: 1 / 0)
thread.start()
thread.join()
raise ValueError("boom from main")
"""


def test_records_errors_without_a_console(tmp_path):
    log = tmp_path / "data" / "diagnostics.log"
    script = SCRIPT.format(installer=str(REPO / "app" / "installer"), log=str(log))
    subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=60)
    text = log.read_text()
    assert "ZeroDivisionError" in text
    assert "boom from main" in text
    assert "platform:" in text
