"""app/run-mac.sh started the way the biomnikhil app icon starts it: no Terminal,
a bare environment and another working folder. A fake Python stands in for the
update and the app window, so nothing is downloaded and no window opens."""
import http.server
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac launcher")

# Records each run in calls.txt. update.py exits with the code in update-code.txt
# (2, up to date, when there's none) and an update bumps app/VERSION, as the real
# one does. The app window records the version it opened on.
FAKE_PYTHON = """#!/bin/bash
calls="$(dirname "$0")/calls.txt"
case "$1" in
  app/installer/update.py)
    echo "update $(cat app/VERSION)" >>"$calls"
    code=2
    if [ -f update-code.txt ]; then
      code=$(cat update-code.txt)
      rm update-code.txt
    fi
    if [ "$code" = 0 ]; then
      echo 0.2.0 >app/VERSION
    fi
    exit "$code"
    ;;
  app/installer/window.py)
    echo "window $(cat app/VERSION) port $2" >>"$calls"
    ;;
esac
exit 0
"""
FAKE_UV = """#!/bin/bash
if [ "$1" = --version ]; then
  echo "uv 0.13.5"
fi
exit 0
"""


def _make_executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def install(tmp_path):
    """A finished release install: app/VERSION, no .git, everything installed."""
    root = tmp_path / "my apps" / "biomnikhil"
    (root / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "run-mac.sh", root / "app" / "run-mac.sh")
    (root / "app" / "VERSION").write_text("0.1.0\n")
    (root / "data").mkdir()
    (root / "data" / ".env").write_text(f"PORT={_free_port()}\n")
    venv_bin = root / "runtime" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _make_executable(venv_bin / "python", "#!/bin/bash\nexit 0\n")
    _make_executable(venv_bin / "pip", "#!/bin/bash\nexit 0\n")
    (root / "runtime" / ".venv" / ".biomnikhil-ready").touch()
    installer_bin = root / "runtime" / "tools" / "installer" / "bin"
    installer_bin.mkdir(parents=True)
    _make_executable(installer_bin / "python", FAKE_PYTHON)
    _make_executable(root / "runtime" / "tools" / "uv", FAKE_UV)
    return root


def _port(install):
    return int((install / "data" / ".env").read_text().split("=")[1])


def _open_from_icon(install, tmp_path):
    """What ~/Applications/biomnikhil.app/Contents/MacOS/biomnikhil runs."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    script = install / "app" / "run-mac.sh"
    result = subprocess.run(["/bin/bash", str(script)], cwd="/", env=environment,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def _calls(install):
    calls_file = install / "runtime" / "tools" / "installer" / "bin" / "calls.txt"
    return calls_file.read_text().splitlines()


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


def test_icon_checks_for_an_update_before_the_window(install, tmp_path):
    _open_from_icon(install, tmp_path)
    assert _calls(install) == ["update 0.1.0", f"window 0.1.0 port {_port(install)}"]


def test_icon_opens_the_updated_version(install, tmp_path):
    (install / "update-code.txt").write_text("0")
    _open_from_icon(install, tmp_path)
    # The launcher restarts on the new files, whose check finds nothing newer.
    assert _calls(install) == ["update 0.1.0", "update 0.2.0", f"window 0.2.0 port {_port(install)}"]


def test_icon_starts_anyway_when_the_check_fails(install, tmp_path):
    (install / "update-code.txt").write_text("1")
    _open_from_icon(install, tmp_path)
    assert _calls(install) == ["update 0.1.0", f"window 0.1.0 port {_port(install)}"]


def test_no_update_while_the_app_is_running(install, tmp_path, running_app):
    _open_from_icon(install, tmp_path)
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]


def test_starts_without_a_settings_file(install, tmp_path):
    # Port 8000 then, which may really be in use here, so this only checks that a
    # missing data/.env doesn't stop the launch.
    (install / "data" / ".env").unlink()
    shutil.copy(REPO / "app" / ".env.example", install / "app" / ".env.example")
    _open_from_icon(install, tmp_path)
    assert _calls(install)[-1] == "window 0.1.0 port 8000"
    assert (install / "data" / ".env").exists()


def test_git_checkout_does_not_self_update(install, tmp_path):
    (install / ".git").mkdir()
    _open_from_icon(install, tmp_path)
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]


def test_no_update_when_turned_off(install, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "BIOMNIKHIL_NO_UPDATE": "1"}
    subprocess.run(["/bin/bash", str(install / "app" / "run-mac.sh")], cwd="/", env=environment,
                   capture_output=True, text=True, timeout=60, check=True)
    assert _calls(install) == [f"window 0.1.0 port {_port(install)}"]
