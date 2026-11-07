"""app/run.ps1 started the way the biomnikhil shortcut starts it, from another
working folder. Stub Python scripts stand in for the update and the app window,
so nothing is downloaded and no window opens. Needs Windows PowerShell and the
pinned uv on PATH (the CI job has both), so it runs in the Windows CI job."""
import http.server
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None or UV is None,
                                reason="Windows launcher, with uv on PATH")

# Records the run in calls.txt. Exits with the code in update-code.txt (2, up to
# date, when there's none), and an update bumps app/VERSION as the real one does.
STUB_UPDATE = """import sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
version_file = root / "app" / "VERSION"
with open(root / "calls.txt", "a") as calls:
    calls.write("update " + version_file.read_text().strip() + "\\n")
code_file = root / "update-code.txt"
code = 2
if code_file.exists():
    code = int(code_file.read_text())
    code_file.unlink()
if code == 0:
    version_file.write_text("0.2.0\\n")
sys.exit(code)
"""
STUB_WINDOW = """import sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
version = (root / "app" / "VERSION").read_text().strip()
with open(root / "calls.txt", "a") as calls:
    calls.write("window " + version + " port " + sys.argv[1] + "\\n")
"""


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _uv_version():
    result = subprocess.run([UV, "--version"], capture_output=True, text=True)
    return result.stdout.split()[1]


@pytest.fixture(scope="session")
def template_venv(tmp_path_factory):
    """A Python environment with pip and a stand-in pywebview, copied into each install."""
    folder = tmp_path_factory.mktemp("template") / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(folder)], check=True)
    (folder / "Lib" / "site-packages" / "webview.py").write_text("")
    return folder


@pytest.fixture
def install(tmp_path, template_venv):
    """A finished release install: app\\VERSION, no .git, everything installed."""
    if _uv_version() != "0.13.5":
        pytest.skip("the uv on PATH isn't the one the launcher pins, so it would download it")
    root = tmp_path / "my apps" / "biomnikhil"
    (root / "app" / "installer").mkdir(parents=True)
    shutil.copy(REPO / "app" / "run.ps1", root / "app" / "run.ps1")
    (root / "app" / "VERSION").write_text("0.1.0\n")
    (root / "app" / "requirements.lock").write_text("")
    (root / "app" / "installer" / "update.py").write_text(STUB_UPDATE)
    (root / "app" / "installer" / "window.py").write_text(STUB_WINDOW)
    (root / "data").mkdir()
    (root / "data" / ".env").write_text(f"PORT={_free_port()}\n")
    shutil.copytree(template_venv, root / "runtime" / ".venv")
    (root / "runtime" / ".venv" / ".biomnikhil-ready").write_text("")
    shutil.copytree(template_venv, root / "runtime" / "tools" / "installer")
    shutil.copy(UV, root / "runtime" / "tools" / "uv.exe")
    return root


def _port(install):
    return int((install / "data" / ".env").read_text().split("=")[1])


def _open_from_shortcut(install, tmp_path, **extra_environment):
    """What the Desktop and Start menu shortcuts run."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    environment = dict(os.environ)
    for name in list(environment):
        if name.upper().startswith("BIOMNIKHIL_"):
            del environment[name]
    environment.update(extra_environment)
    command = [POWERSHELL, "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
               "-File", str(install / "app" / "run.ps1")]
    result = subprocess.run(command, cwd=elsewhere, env=environment,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr


def _calls(install):
    return (install / "calls.txt").read_text().splitlines()


@pytest.fixture
def running_app(install):
    """Something that answers /ping like biomnikhil does, on the install's port."""
    class Ping(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"biomnikhil")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", _port(install)), Ping)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_shortcut_checks_for_an_update_before_the_window(install, tmp_path):
    _open_from_shortcut(install, tmp_path)
    assert _calls(install) == ["update 0.1.0", f"window 0.1.0 port {_port(install)}"]


def test_shortcut_opens_the_updated_version(install, tmp_path):
    (install / "update-code.txt").write_text("0")
    _open_from_shortcut(install, tmp_path)
    # The launcher restarts on the new files, whose check finds nothing newer.
    assert _calls(install) == ["update 0.1.0", "update 0.2.0", f"window 0.2.0 port {_port(install)}"]


def test_shortcut_starts_anyway_when_the_check_fails(install, tmp_path):
    (install / "update-code.txt").write_text("1")
    _open_from_shortcut(install, tmp_path)
    assert _calls(install) == ["update 0.1.0", f"window 0.1.0 port {_port(install)}"]


def test_no_update_while_the_app_is_running(install, tmp_path, running_app):
    _open_from_shortcut(install, tmp_path)
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]


def test_git_checkout_does_not_self_update(install, tmp_path):
    (install / ".git").mkdir()
    _open_from_shortcut(install, tmp_path)
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]


def test_no_update_when_turned_off(install, tmp_path):
    _open_from_shortcut(install, tmp_path, BIOMNIKHIL_NO_UPDATE="1")
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]
