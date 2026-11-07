"""setup-mac.command, run for real against a temporary home folder with a stub
run-mac.sh. A fake osascript stands in for the dialogs, so no window ever opens."""
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac setup script")

# The fake osascript gives the next line of answers.txt, and records what it was asked.
# ERROR:<text> fails the way osascript does, with <text> on stderr. The script closing
# the Terminal window isn't a question, so it's only noted in closed-terminal.txt.
FAKE_OSASCRIPT = """#!/bin/bash
tools="$(dirname "$0")"
if /usr/bin/grep -q 'application "Terminal"'; then
  echo closed >>"$tools/closed-terminal.txt"
  exit 0
fi
printf '%s\\n' "$2" >>"$tools/asked.txt"
answer="$(/usr/bin/head -n 1 "$tools/answers.txt")"
/usr/bin/sed -i '' 1d "$tools/answers.txt"
case "$answer" in
  ERROR:*)
    echo "${answer#ERROR:}" >&2
    exit 1
    ;;
esac
echo "$answer"
"""
CANCELLED = "ERROR:0:95: execution error: User canceled. (-128)"
NO_WINDOW_SERVER = "ERROR:execution error: No user interaction allowed. (-1713)"


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    (folder / "Library").mkdir(parents=True)
    return folder


def _make_source(tmp_path):
    source = tmp_path / "unzipped"
    (source / "app" / "installer").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "setup-mac.command", source / "setup-mac.command")
    shutil.copy(REPO / "uninstall-mac.command", source / "uninstall-mac.command")
    (source / "app" / "run-mac.sh").write_text('pwd -P >"$RAN_FROM_FILE"\n')
    (source / "app" / "installer" / "core.py").write_text("x")
    (source / "app" / "config.py").write_text("x")
    return source


def _run_setup(tmp_path, home, answers=None, install_dir=None, source=None):
    """answers=None runs with no dialogs; a list runs with the fake osascript."""
    assert tmp_path in home.parents  # never the real home folder
    if source is None:
        source = _make_source(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    ran_from_file = tmp_path / "ran-from.txt"
    ran_from_file.unlink(missing_ok=True)
    env = dict(os.environ, HOME=str(home), RAN_FROM_FILE=str(ran_from_file))
    env.pop("BIOMNIKHIL_NO_DIALOGS", None)
    env.pop("BIOMNIKHIL_INSTALL_DIR", None)
    if install_dir is not None:
        env["BIOMNIKHIL_INSTALL_DIR"] = str(install_dir)
    if answers is None:
        env["BIOMNIKHIL_NO_DIALOGS"] = "1"
    else:
        fake = tools / "osascript"
        fake.write_text(FAKE_OSASCRIPT)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        (tools / "answers.txt").write_text("".join(answer + "\n" for answer in answers))
        (tools / "asked.txt").write_text("")
        env["PATH"] = str(tools) + os.pathsep + env["PATH"]
    result = subprocess.run(
        ["/bin/bash", str(source / "setup-mac.command")],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    return result


def _ran_from(tmp_path):
    """Where the stub ran. With dialogs on it starts in the background, so give it a moment."""
    ran_from_file = tmp_path / "ran-from.txt"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ran_from_file.exists() and ran_from_file.read_text().strip():
            return Path(ran_from_file.read_text().strip())
        time.sleep(0.05)
    return None


def _asked(tmp_path):
    return (tmp_path / "tools" / "asked.txt").read_text()


def _saved(home):
    return (home / "Library" / "biomnikhil-location").read_text().strip()


def _finish_install(root):
    ready = root / "runtime" / ".venv" / ".biomnikhil-ready"
    ready.parent.mkdir(parents=True)
    ready.write_text("")


def test_with_no_dialogs_it_installs_in_the_default_folder(tmp_path, home):
    result = _run_setup(tmp_path, home)
    target = home / "Library" / "biomnikhil"
    assert result.returncode == 0, result.stdout + result.stderr
    assert _ran_from(tmp_path) == target.resolve()
    assert (target / "app" / "run-mac.sh").exists()
    assert (target / "uninstall-mac.command").exists()
    assert _saved(home) == str(target)


def test_the_given_folder_gets_a_biomnikhil_folder(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    (chosen / "something of mine.txt").write_text("mine")
    result = _run_setup(tmp_path, home, install_dir=chosen)
    target = chosen.resolve() / "biomnikhil"
    assert result.returncode == 0, result.stdout + result.stderr
    assert _ran_from(tmp_path) == target
    assert _saved(home) == str(target)
    assert (chosen / "something of mine.txt").read_text() == "mine"
    assert not (home / "Library" / "biomnikhil").exists()


def test_a_folder_already_called_biomnikhil_is_used_as_it_is(tmp_path, home):
    chosen = tmp_path / "biomnikhil"
    chosen.mkdir()
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _ran_from(tmp_path) == chosen.resolve()


def test_install_here_uses_the_suggested_folder(tmp_path, home):
    result = _run_setup(tmp_path, home, answers=["Install Here"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(home / "Library" / "biomnikhil") in _asked(tmp_path)
    assert "16 GB" in _asked(tmp_path)
    assert _saved(home) == str(home / "Library" / "biomnikhil")


def test_choosing_a_folder_asks_again_with_that_folder(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    answers = ["Choose Another Folder", f"{chosen}/", "Install Here"]
    result = _run_setup(tmp_path, home, answers=answers)
    target = chosen.resolve() / "biomnikhil"
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{target}\n" in _asked(tmp_path)
    assert _saved(home) == str(target)
    assert (target / "app" / "run-mac.sh").exists()


def test_cancel_changes_nothing(tmp_path, home):
    result = _run_setup(tmp_path, home, answers=[CANCELLED])
    assert result.returncode == 0
    assert "cancelled" in result.stdout
    assert not (tmp_path / "ran-from.txt").exists()
    assert not (home / "Library" / "biomnikhil").exists()
    assert not (home / "Library" / "biomnikhil-location").exists()


def _closed_terminal(tmp_path):
    """Whether setup closed its Terminal window. That runs in the background too."""
    closed_file = tmp_path / "tools" / "closed-terminal.txt"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if closed_file.exists():
            return True
        time.sleep(0.05)
    return False


def test_cancel_closes_the_terminal_window(tmp_path, home):
    _run_setup(tmp_path, home, answers=[CANCELLED])
    assert _closed_terminal(tmp_path)


def test_cancelling_the_folder_chooser_goes_back_to_the_question(tmp_path, home):
    answers = ["Choose Another Folder", CANCELLED, "Install Here"]
    result = _run_setup(tmp_path, home, answers=answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _saved(home) == str(home / "Library" / "biomnikhil")


def test_a_dialog_that_cannot_open_does_not_stop_the_install(tmp_path, home):
    result = _run_setup(tmp_path, home, answers=[NO_WINDOW_SERVER])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _ran_from(tmp_path) == (home / "Library" / "biomnikhil").resolve()


def test_a_refused_folder_explains_and_asks_again(tmp_path, home):
    refused = home / "Dropbox"
    refused.mkdir()
    answers = ["Choose Another Folder", str(refused), "", "Install Here"]
    result = _run_setup(tmp_path, home, answers=answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "syncs to the cloud" in _asked(tmp_path)
    assert not (refused / "biomnikhil").exists()
    assert _saved(home) == str(home / "Library" / "biomnikhil")


def test_a_finished_install_is_kept_without_asking(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    _run_setup(tmp_path, home, install_dir=chosen)
    target = chosen.resolve() / "biomnikhil"
    _finish_install(target)
    source = tmp_path / "unzipped"
    (source / "app" / "new-in-this-version.txt").write_text("new")
    result = _run_setup(tmp_path, home, answers=[], source=source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _asked(tmp_path) == ""
    assert (target / "app" / "new-in-this-version.txt").exists()
    assert not (home / "Library" / "biomnikhil").exists()


def test_a_finished_install_in_the_default_folder_is_kept_without_asking(tmp_path, home):
    _run_setup(tmp_path, home)
    _finish_install(home / "Library" / "biomnikhil")
    (home / "Library" / "biomnikhil-location").unlink()
    result = _run_setup(tmp_path, home, answers=[], source=tmp_path / "unzipped")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _asked(tmp_path) == ""
    assert _ran_from(tmp_path) == (home / "Library" / "biomnikhil").resolve()


def test_an_unfinished_install_is_suggested_again(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    _run_setup(tmp_path, home, install_dir=chosen)
    target = chosen.resolve() / "biomnikhil"
    result = _run_setup(tmp_path, home, answers=["Install Here"], source=tmp_path / "unzipped")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{target}\n" in _asked(tmp_path)
    assert _saved(home) == str(target)


def test_a_saved_folder_that_is_gone_goes_back_to_the_default(tmp_path, home):
    (home / "Library" / "biomnikhil-location").write_text(str(tmp_path / "unplugged drive" / "biomnikhil") + "\n")
    result = _run_setup(tmp_path, home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _saved(home) == str(home / "Library" / "biomnikhil")


def test_the_saved_folder_must_be_an_install(tmp_path, home):
    documents = tmp_path / "documents"
    documents.mkdir()
    (documents / "thesis.txt").write_text("mine")
    (home / "Library" / "biomnikhil-location").write_text(str(documents) + "\n")
    result = _run_setup(tmp_path, home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in documents.iterdir()) == ["thesis.txt"]
    assert _saved(home) == str(home / "Library" / "biomnikhil")


@pytest.mark.parametrize("folder", [
    "Dropbox",
    "OneDrive - Some Lab",
    "Library/Mobile Documents/com~apple~CloudDocs",
    "Library/CloudStorage/GoogleDrive-someone",
])
def test_cloud_folders_are_refused(tmp_path, home, folder):
    chosen = home / folder
    chosen.mkdir(parents=True)
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 1
    assert "syncs to the cloud" in result.stderr
    assert not (tmp_path / "ran-from.txt").exists()
    assert not (chosen / "biomnikhil").exists()
    assert not (home / "Library" / "biomnikhil-location").exists()


@pytest.mark.parametrize("protected", ["Documents", "Desktop", "Downloads", "Documents/apps"])
def test_protected_folders_are_refused(tmp_path, home, protected):
    chosen = home / protected
    chosen.mkdir(parents=True)
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 1
    assert "macOS protects" in result.stderr
    assert not (chosen / "biomnikhil").exists()
    assert not (home / "Library" / "biomnikhil-location").exists()


def test_a_folder_named_like_a_protected_one_is_allowed(tmp_path, home):
    chosen = home / "Documents2"
    chosen.mkdir()
    assert _run_setup(tmp_path, home, install_dir=chosen).returncode == 0
    assert (chosen / "biomnikhil" / "app").is_dir()


def test_the_unzipped_folder_is_refused(tmp_path, home):
    source = _make_source(tmp_path)
    result = _run_setup(tmp_path, home, install_dir=source / "app", source=source)
    assert result.returncode == 1
    assert "unzipped folder" in result.stderr
    assert not (source / "app" / "biomnikhil").exists()


def test_a_folder_called_biomnikhil_with_other_files_is_refused(tmp_path, home):
    chosen = tmp_path / "biomnikhil"
    chosen.mkdir()
    (chosen / "thesis.txt").write_text("mine")
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 1
    assert "other files" in result.stderr
    assert sorted(path.name for path in chosen.iterdir()) == ["thesis.txt"]


def test_a_link_called_biomnikhil_inside_the_chosen_folder_is_refused(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (chosen / "biomnikhil").symlink_to(elsewhere)
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 1
    assert "is a link" in result.stderr
    assert list(elsewhere.iterdir()) == []


def test_a_file_in_the_way_is_refused(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    (chosen / "biomnikhil").write_text("a file")
    result = _run_setup(tmp_path, home, install_dir=chosen)
    assert result.returncode == 1
    assert "already a file" in result.stderr


def test_a_folder_it_cannot_write_to_is_refused(tmp_path, home):
    chosen = tmp_path / "read only"
    chosen.mkdir()
    chosen.chmod(0o500)
    try:
        result = _run_setup(tmp_path, home, install_dir=chosen)
    finally:
        chosen.chmod(0o700)
    assert result.returncode == 1
    assert "can't write there" in result.stderr


def test_the_write_check_leaves_nothing_behind(tmp_path, home):
    chosen = tmp_path / "my apps"
    chosen.mkdir()
    answers = ["Choose Another Folder", str(chosen), CANCELLED]
    result = _run_setup(tmp_path, home, answers=answers)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(chosen.iterdir()) == []


def test_a_relative_path_is_refused(tmp_path, home):
    result = _run_setup(tmp_path, home, install_dir="not/a/full/path")
    assert result.returncode == 1
    assert "full path" in result.stderr


def test_a_git_checkout_runs_where_it_is(tmp_path, home):
    source = _make_source(tmp_path)
    (source / ".git").mkdir()
    result = _run_setup(tmp_path, home, answers=[], source=source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _asked(tmp_path) == ""
    assert _ran_from(tmp_path) == source.resolve()
    assert not (home / "Library" / "biomnikhil-location").exists()
