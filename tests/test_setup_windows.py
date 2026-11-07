"""app/setup.ps1, run for real with a stub run.ps1. Needs Windows PowerShell or
pwsh, so it runs in the Windows CI job and skips where neither exists."""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell here")


@pytest.fixture
def short_folder():
    """pytest's folders are already too long to pass setup's path limit on the CI machines."""
    folder = Path(tempfile.mkdtemp(prefix="bk")).resolve()
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _make_source(tmp_path, stub="exit 0"):
    source = tmp_path / "unzipped"
    (source / "app" / "installer").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "app" / "setup.ps1", source / "app" / "setup.ps1")
    (source / "app" / "run.ps1").write_text(stub)
    (source / "app" / "installer" / "core.py").write_text("x")
    (source / "app" / "config.py").write_text("x")
    return source


def _run_script(tmp_path, script, install_dir=None):
    local = tmp_path / "local"
    local.mkdir(exist_ok=True)
    env = dict(os.environ, LOCALAPPDATA=str(local), BIOMNIKHIL_NO_DIALOGS="1")
    env.pop("BIOMNIKHIL_INSTALL_DIR", None)
    if install_dir is not None:
        env["BIOMNIKHIL_INSTALL_DIR"] = str(install_dir)
    command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)


def _run_setup(tmp_path, stub, install_dir=None):
    source = _make_source(tmp_path, stub)
    result = _run_script(tmp_path, source / "app" / "setup.ps1", install_dir)
    return result, tmp_path / "local" / "biomnikhil"


def _saved(tmp_path):
    return (tmp_path / "local" / "biomnikhil-location.txt").read_text(encoding="utf-8-sig").strip()


def _finish_install(root):
    ready = root / "runtime" / ".venv" / ".biomnikhil-ready"
    ready.parent.mkdir(parents=True)
    ready.write_text("")


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


def test_the_default_folder_is_saved(tmp_path):
    result, target = _run_setup(tmp_path, "exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _saved(tmp_path).lower() == str(target).lower()


def test_the_given_folder_gets_a_biomnikhil_folder(tmp_path, short_folder):
    chosen = short_folder / "my apps"
    chosen.mkdir()
    (chosen / "mine.txt").write_text("mine")
    result, default = _run_setup(tmp_path, "exit 0", install_dir=chosen)
    target = chosen / "biomnikhil"
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "app" / "run.ps1").exists()
    assert (target / "setup.log").exists()
    assert (chosen / "mine.txt").read_text() == "mine"
    assert _saved(tmp_path).lower() == str(target).lower()
    assert not default.exists()


def test_a_folder_already_called_biomnikhil_is_used_as_it_is(tmp_path, short_folder):
    chosen = short_folder / "biomnikhil"
    chosen.mkdir()
    result, _ = _run_setup(tmp_path, "exit 0", install_dir=chosen)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (chosen / "app" / "run.ps1").exists()
    assert not (chosen / "biomnikhil").exists()


def test_a_finished_install_is_kept(tmp_path, short_folder):
    chosen = short_folder / "my apps"
    chosen.mkdir()
    _run_setup(tmp_path, "exit 0", install_dir=chosen)
    target = chosen / "biomnikhil"
    _finish_install(target)
    result, default = _run_setup(tmp_path, "exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not default.exists()
    assert _saved(tmp_path).lower() == str(target).lower()


def test_an_unfinished_install_is_suggested_again(tmp_path, short_folder):
    chosen = short_folder / "my apps"
    chosen.mkdir()
    _run_setup(tmp_path, "exit 0", install_dir=chosen)
    result, default = _run_setup(tmp_path, "exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not default.exists()


def test_setup_from_the_chosen_install_runs_in_place(tmp_path, short_folder):
    chosen = short_folder / "my apps"
    chosen.mkdir()
    _run_setup(tmp_path, "exit 0", install_dir=chosen)
    target = chosen / "biomnikhil"
    shutil.rmtree(tmp_path / "unzipped")
    result = _run_script(tmp_path, target / "app" / "setup.ps1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Copying" not in result.stdout
    assert not (tmp_path / "local" / "biomnikhil").exists()


def test_the_saved_folder_must_be_an_install(tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "thesis.txt").write_text("mine")
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "biomnikhil-location.txt").write_text(str(documents) + "\n")
    result, default = _run_setup(tmp_path, "exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in documents.iterdir()) == ["thesis.txt"]
    assert (default / "app" / "run.ps1").exists()


@pytest.mark.parametrize("folder, reason", [
    ("OneDrive - Some Lab", "syncs to the cloud"),
    ("Dropbox", "syncs to the cloud"),
    ("x" * 120, "too long"),
])
def test_unsuitable_folders_are_refused(tmp_path, short_folder, folder, reason):
    chosen = short_folder / folder
    chosen.mkdir()
    result, _ = _run_setup(tmp_path, "exit 0", install_dir=chosen)
    assert result.returncode == 1
    assert reason in result.stdout
    assert not (chosen / "biomnikhil").exists()
    assert not (tmp_path / "local" / "biomnikhil-location.txt").exists()


def test_a_network_path_is_refused(tmp_path):
    result, _ = _run_setup(tmp_path, "exit 0", install_dir="\\\\server\\share")
    assert result.returncode == 1
    assert "network" in result.stdout


def test_a_relative_path_is_refused(tmp_path):
    result, _ = _run_setup(tmp_path, "exit 0", install_dir="not\\a\\full\\path")
    assert result.returncode == 1
    assert "full path" in result.stdout


def test_the_unzipped_folder_is_refused(tmp_path, short_folder):
    source = _make_source(short_folder)
    result = _run_script(tmp_path, source / "app" / "setup.ps1", install_dir=source / "app")
    assert result.returncode == 1
    assert "unzipped folder" in result.stdout
    assert not (source / "app" / "biomnikhil").exists()


def test_a_folder_called_biomnikhil_with_other_files_is_refused(tmp_path, short_folder):
    chosen = short_folder / "biomnikhil"
    chosen.mkdir()
    (chosen / "thesis.txt").write_text("mine")
    result, _ = _run_setup(tmp_path, "exit 0", install_dir=chosen)
    assert result.returncode == 1
    assert "other files" in result.stdout
    assert sorted(path.name for path in chosen.iterdir()) == ["thesis.txt"]


def test_a_file_in_the_way_is_refused(tmp_path, short_folder):
    chosen = short_folder / "my apps"
    chosen.mkdir()
    (chosen / "biomnikhil").write_text("a file")
    result, _ = _run_setup(tmp_path, "exit 0", install_dir=chosen)
    assert result.returncode == 1
    assert "already a file" in result.stdout
