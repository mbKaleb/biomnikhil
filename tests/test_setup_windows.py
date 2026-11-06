"""app/setup.ps1, run for real with a stub run.ps1. Needs Windows PowerShell or
pwsh, so it runs in the Windows CI job and skips where neither exists."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell here")


def _run_setup(tmp_path, stub):
    source = tmp_path / "unzipped"
    (source / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "setup.ps1", source / "app" / "setup.ps1")
    (source / "app" / "run.ps1").write_text(stub)
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    env = dict(os.environ, LOCALAPPDATA=str(local), BIOMNIKHIL_NO_DIALOGS="1")
    command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "app" / "setup.ps1")]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    return result, local / "biomnikhil"


def test_copies_the_app_and_removes_stale_files(tmp_path):
    stale = tmp_path / "local" / "biomnikhil" / "app" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    result, target = _run_setup(tmp_path, "exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "app" / "run.ps1").exists()
    assert not stale.exists()
    assert (target / "setup.log").exists()


def test_a_failure_is_reported_with_the_log(tmp_path):
    result, target = _run_setup(tmp_path, "exit 7")
    assert result.returncode == 7
    assert "setup.log" in result.stdout
    assert (target / "setup.log").exists()


def test_a_thrown_error_is_reported_with_the_log(tmp_path):
    result, target = _run_setup(tmp_path, "throw 'boom'")
    assert result.returncode == 1
    assert "boom" in result.stdout
