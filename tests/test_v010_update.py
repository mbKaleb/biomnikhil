"""A v0.1.0 install must survive updating to this release. Runs v0.1.0's real
launchers (tests/fixtures/v0.1.0, copied from that tag) with the network faked:
they copy the release over the folder and restart their own path, which has to
reach app/'s launcher instead of updating again."""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OLD_LAUNCHERS = REPO / "tests" / "fixtures" / "v0.1.0"
WINDOWS = sys.platform == "win32"
LAUNCHER = "run.ps1" if WINDOWS else "run-mac.sh"
TIME_LIMIT_SECONDS = 60

FAKE_CURL = """#!/bin/bash
echo call >> "$FAKE_DIR/downloads.log"
for argument in "$@"; do
  case "$argument" in *api.github.com*) echo '"tag_name": "v9.9.9"'; exit 0;; esac
done
while [ $# -gt 0 ]; do
  if [ "$1" = -o ]; then cp "$FAKE_DIR/biomnikhil.zip" "$2"; fi
  shift
done
"""

FAKE_NETWORK_PS1 = """param($Install, $FakeDir)
function Invoke-RestMethod {
    Add-Content "$FakeDir\\downloads.log" "call"
    return [pscustomobject]@{ tag_name = "v9.9.9" }
}
function Invoke-WebRequest {
    param($Uri, $OutFile)
    Add-Content "$FakeDir\\downloads.log" "call"
    Copy-Item "$FakeDir\\biomnikhil.zip" $OutFile
}
& "$Install\\run.ps1"
exit $LASTEXITCODE
"""

NEW_LAUNCHER_STUB = {
    "run-mac.sh": '#!/bin/bash\necho started > "$FAKE_DIR/new-launcher-ran"\n',
    "run.ps1": 'Set-Content "$env:FAKE_DIR\\new-launcher-ran" "started"\nexit 0\n',
}


def _build_release_zip(zip_path: Path) -> None:
    """The shape release.yml builds: the root bridge, app/'s launcher, and the
    version only in app/VERSION."""
    with zipfile.ZipFile(zip_path, "w") as release:
        release.write(REPO / LAUNCHER, f"biomnikhil/{LAUNCHER}")
        release.writestr(f"biomnikhil/app/{LAUNCHER}", NEW_LAUNCHER_STUB[LAUNCHER])
        release.writestr("biomnikhil/app/VERSION", "9.9.9\n")


def _run_old_launcher(install: Path, fake_dir: Path) -> subprocess.CompletedProcess:
    environment = dict(os.environ, FAKE_DIR=str(fake_dir))
    environment.pop("BIOMNIKHIL_NO_UPDATE", None)
    if WINDOWS:
        wrapper = fake_dir / "fake-network.ps1"
        wrapper.write_text(FAKE_NETWORK_PS1)
        command = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
            "-Install", str(install), "-FakeDir", str(fake_dir),
        ]
    else:
        fake_bin = fake_dir / "bin"
        fake_bin.mkdir()
        fake_curl = fake_bin / "curl"
        fake_curl.write_text(FAKE_CURL)
        fake_curl.chmod(0o755)
        environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
        command = ["bash", str(install / LAUNCHER)]
    return subprocess.run(
        command, env=environment, cwd=install, capture_output=True, text=True,
        timeout=TIME_LIMIT_SECONDS,
    )


def test_v010_install_updates_once_and_starts_the_new_launcher(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    shutil.copy(OLD_LAUNCHERS / LAUNCHER, install / LAUNCHER)
    (install / "VERSION").write_text("0.1.0\n")
    kept_chat_database = install / "data" / "user" / "chats.db"
    kept_chat_database.parent.mkdir(parents=True)
    kept_chat_database.write_bytes(b"chats")
    _build_release_zip(fake_dir / "biomnikhil.zip")

    result = _run_old_launcher(install, fake_dir)

    assert (fake_dir / "new-launcher-ran").exists(), result.stdout + result.stderr
    # One version check and one zip: a second round is the update loop.
    assert len((fake_dir / "downloads.log").read_text().splitlines()) == 2
    assert (install / "app" / "VERSION").read_text().strip() == "9.9.9"
    assert kept_chat_database.read_bytes() == b"chats"
