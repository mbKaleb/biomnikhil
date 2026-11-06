"""app/uninstall.ps1, run for real on fake trees under temporary profile folders.
Needs Windows, so it runs in the Windows CI job and skips elsewhere. The file
checks at the end run everywhere."""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_windows = pytest.mark.skipif(
    POWERSHELL is None or sys.platform != "win32", reason="needs Windows PowerShell")

EXIT_FINISHED = 0
EXIT_CANCELLED = 3


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_install(root):
    _write(root / "app" / "installer" / "core.py")
    _write(root / "app" / "config.py")
    shutil.copy(REPO / "app" / "uninstall.ps1", root / "app" / "uninstall.ps1")
    shutil.copy(REPO / "uninstall-windows.cmd", root / "uninstall-windows.cmd")
    _write(root / "runtime" / ".venv" / ".biomnikhil-ready")
    _write(root / "data" / ".env", f"PORT={_free_port()}\n")
    return root


def _make_lake(parent):
    lake = parent / "biomni_data"
    _write(lake / "data_lake" / "genes.parquet")
    _write(lake / "benchmark" / "hle" / "a.json")
    return lake


def _environment(tmp_path):
    profile = tmp_path / "profile"
    local = tmp_path / "local"
    desktop = tmp_path / "desktop"
    programs = tmp_path / "programs"
    program_data = tmp_path / "programdata"
    for folder in (profile, local, desktop, programs, program_data):
        folder.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["USERPROFILE"] = str(profile)
    env["LOCALAPPDATA"] = str(local)
    env["ProgramData"] = str(program_data)
    env["BIOMNIKHIL_UNINSTALL_TEST_DESKTOP"] = str(desktop)
    env["BIOMNIKHIL_UNINSTALL_TEST_PROGRAMS"] = str(programs)
    env.pop("BIOMNI_DATA_PATH", None)
    return env


def _run(tmp_path, root, answers=""):
    assert tmp_path in root.parents  # never a real install
    script = root / "app" / "uninstall.ps1"
    command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    return subprocess.run(command, env=_environment(tmp_path), input=answers, cwd=tmp_path,
                          capture_output=True, text=True, timeout=300)


@pytest.fixture
def install(tmp_path):
    return _make_install(tmp_path / "local" / "biomnikhil")


@needs_windows
def test_removes_the_install_it_runs_from_and_own_lakes(tmp_path, install):
    default_lake = _make_lake(tmp_path / "profile" / "biomnikhil-data")
    in_folder_lake = _make_lake(install)  # the app data preset is the install folder here
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert "biomnikhil was removed." in result.stdout
    assert not install.exists()
    assert not default_lake.exists()
    assert not in_folder_lake.exists()


@needs_windows
def test_the_launcher_removes_its_own_folder(tmp_path, install):
    command = ["cmd", "/c", str(install / "uninstall-windows.cmd")]
    result = subprocess.run(command, env=_environment(tmp_path), input="REMOVE\n\n", cwd=tmp_path,
                            capture_output=True, text=True, timeout=300)
    assert "biomnikhil was removed." in result.stdout, result.stdout + result.stderr
    assert not install.exists()


@needs_windows
@pytest.mark.parametrize("answer", ["", "remove\n", "yes\n"])
def test_anything_but_the_word_cancels(tmp_path, install, answer):
    result = _run(tmp_path, install, answer)
    assert result.returncode == EXIT_CANCELLED, result.stdout + result.stderr
    assert (install / "app" / "config.py").exists()


@needs_windows
def test_chosen_folder_keeps_its_lake_by_default(tmp_path, install):
    chosen = tmp_path / "chosen"
    lake = _make_lake(chosen)
    _write(chosen / "thesis.docx")
    location = {"kind": "custom", "path": str(chosen), "previous": []}
    (install / "data" / "datalake-location.json").write_text(json.dumps(location))
    result = _run(tmp_path, install, "\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (lake / "data_lake" / "genes.parquet").exists()
    assert (chosen / "thesis.docx").exists()
    assert not install.exists()


@needs_windows
def test_shared_lake_removed_only_when_named_and_keeps_other_files(tmp_path, install):
    parent = tmp_path / "programdata" / "biomnikhil"
    lake = _make_lake(parent)
    _write(parent / "readme.txt")
    result = _run(tmp_path, install, f"{lake}\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not lake.exists()
    assert (parent / "readme.txt").exists()


@needs_windows
def test_junctions_are_not_followed(tmp_path, install):
    outside = tmp_path / "outside"
    _write(outside / "precious.txt")
    link = install / "data" / "linked"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (outside / "precious.txt").exists()
    assert not install.exists()


@needs_windows
def test_an_install_folder_that_is_a_junction_is_left_alone(tmp_path):
    elsewhere = _make_install(tmp_path / "elsewhere")
    local = tmp_path / "local"
    local.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(local / "biomnikhil"), str(elsewhere)],
                   check=True, capture_output=True)
    stray = tmp_path / "stray"
    (stray / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "uninstall.ps1", stray / "app" / "uninstall.ps1")
    result = _run(tmp_path, stray, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (elsewhere / "app" / "config.py").exists()


@needs_windows
def test_refuses_the_profile_folder_as_an_install(tmp_path):
    profile = tmp_path / "profile"
    _make_install(profile)
    _write(profile / "keep.txt")
    result = _run(tmp_path, profile, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert "Refused" in result.stdout
    assert (profile / "app" / "config.py").exists()
    assert (profile / "keep.txt").exists()


@needs_windows
def test_a_stray_copy_finds_nothing(tmp_path):
    stray = tmp_path / "stray"
    (stray / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "uninstall.ps1", stray / "app" / "uninstall.ps1")
    _write(stray / "data" / "important.txt")
    result = _run(tmp_path, stray, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (stray / "data" / "important.txt").exists()


@needs_windows
def test_a_preset_that_is_a_junction_keeps_its_target_lake_by_default(tmp_path, install):
    shared = tmp_path / "shared drive"
    lake = _make_lake(shared)
    profile = tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(profile / "biomnikhil-data"), str(shared)],
                   check=True, capture_output=True)
    result = _run(tmp_path, install, "\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (lake / "data_lake" / "genes.parquet").exists()


@needs_windows
def test_a_checkout_keeps_a_lake_inside_tracked_data(tmp_path):
    checkout = _make_install(tmp_path / "checkout")
    lake = _make_lake(checkout / "data")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "data"], check=True)
    result = _run(tmp_path, checkout, "REMOVE\n")
    assert (lake / "data_lake" / "genes.parquet").exists(), result.stdout


@needs_windows
def test_a_git_repository_that_is_not_biomnikhil_is_left_alone(tmp_path):
    other_project = tmp_path / "other project"
    _write(other_project / "data" / "results.csv")
    (other_project / "app").mkdir()
    shutil.copy(REPO / "app" / "uninstall.ps1", other_project / "app" / "uninstall.ps1")
    subprocess.run(["git", "init", "-q", str(other_project)], check=True)
    result = _run(tmp_path, other_project, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (other_project / "data" / "results.csv").exists()


@needs_windows
def test_brackets_and_spaces_in_paths_are_taken_literally(tmp_path):
    root = _make_install(tmp_path / "old [place] x")
    chosen = tmp_path / "my [lab] lake"
    lake = _make_lake(chosen)
    look_alike = _make_lake(tmp_path / "my l lake")
    _write(lake / "notes.txt")
    location = {"kind": "custom", "path": str(chosen), "previous": []}
    (root / "data" / "datalake-location.json").write_text(json.dumps(location))
    result = _run(tmp_path, root, f"{lake}\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not root.exists()
    assert not (lake / "data_lake").exists()
    assert (lake / "notes.txt").exists()
    assert (look_alike / "data_lake" / "genes.parquet").exists()


@needs_windows
def test_a_checkout_keeps_its_source(tmp_path):
    checkout = _make_install(tmp_path / "checkout")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    _write(checkout / ".gitignore", "data/\nruntime/\n")
    subprocess.run(["git", "-C", str(checkout), "add", "app", ".gitignore"], check=True)
    result = _run(tmp_path, checkout, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (checkout / "app" / "config.py").exists()
    assert not (checkout / "data").exists()


def test_the_powershell_script_is_ascii():
    (REPO / "app" / "uninstall.ps1").read_bytes().decode("ascii")


def test_the_launcher_has_windows_line_endings_in_the_repository_rule():
    attributes = (REPO / ".gitattributes").read_text()
    assert "*.cmd text eol=crlf" in attributes
    assert "*.command text eol=lf" in attributes


@needs_windows
def test_a_locked_file_is_reported_and_the_uninstaller_stays(tmp_path, install):
    locked = install / "data" / "locked.txt"
    _write(locked)
    with open(locked, "rb"):  # Python opens without sharing delete
        result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NOT completely removed" in result.stdout
    assert "didn't create" not in result.stdout
    assert (install / "app" / "uninstall.ps1").exists()


@needs_windows
def test_folders_a_lake_merge_emptied_are_removed(tmp_path, install):
    default_parent = tmp_path / "profile" / "biomnikhil-data"
    (default_parent / "biomni_data").mkdir(parents=True)
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not default_parent.exists()
    assert "didn't create" not in result.stdout


@needs_windows
def test_an_old_in_place_install_loses_what_old_releases_left(tmp_path):
    old = _make_install(tmp_path / "old place")
    _write(old / ".env")
    _write(old / ".venv" / "file")
    _write(old / "Start biomnikhil.cmd")
    result = _run(tmp_path, old, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not old.exists()


def test_the_launcher_passes_on_every_exit_code():
    launcher = (REPO / "uninstall-windows.cmd").read_text()
    assert "exit /b 3" in launcher
    assert "exit /b 2" in launcher
