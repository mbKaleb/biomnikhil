"""app/uninstall-mac.sh, run for real on fake trees under a temporary HOME. The
script is always a copy inside the fake tree, so it never sees a real install."""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac only")

EXIT_FINISHED = 0
EXIT_INCOMPLETE = 1
EXIT_REFUSED = 2
EXIT_CANCELLED = 3


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _make_install(root, env_lines=()):
    """An install with the uninstaller in it, on a port nothing listens on."""
    _write(root / "app" / "installer" / "core.py")
    _write(root / "app" / "config.py")
    (root / "app").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "app" / "uninstall-mac.sh", root / "app" / "uninstall-mac.sh")
    _write(root / "runtime" / ".venv" / ".biomnikhil-ready")
    lines = [f"PORT={_free_port()}"]
    lines.extend(env_lines)
    _write(root / "data" / ".env", "\n".join(lines) + "\n")
    _write(root / "data" / "user" / "chats.db")
    _write(root / "data" / "webview" / "cookies")
    return root


def _make_lake(parent):
    lake = parent / "biomni_data"
    _write(lake / "data_lake" / "genes.parquet")
    _write(lake / "benchmark" / "hle" / "a.json")
    _write(lake / ".sizes.json", "{}")
    return lake


def _save_location(root, kind, path, previous=()):
    location = root / "data" / "datalake-location.json"
    location.write_text(json.dumps({"kind": kind, "path": str(path), "previous": [str(p) for p in previous]}))


def _environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["BIOMNIKHIL_UNINSTALL_TEST_SYSTEM_DIR"] = str(tmp_path / "system-support")
    env.pop("BIOMNI_DATA_PATH", None)
    return env


def _run(tmp_path, root, answers="", extra_env=None):
    assert tmp_path in root.parents  # never a real install
    command = ["/bin/bash", str(root / "app" / "uninstall-mac.sh")]
    env = _environment(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(command, env=env, input=answers, cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)


@pytest.fixture
def home(tmp_path):
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


@pytest.fixture
def install(home):
    return _make_install(home / "Library" / "biomnikhil")


def test_removes_install_shortcuts_and_own_lakes(tmp_path, home, install):
    default_lake = _make_lake(home / "biomnikhil-data")
    app_data_lake = _make_lake(home / "Library" / "Application Support" / "biomnikhil")
    _make_lake(install / "data")

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert "biomnikhil was removed." in result.stdout
    assert str(default_lake / "data_lake") in result.stdout  # listed with a size
    assert " KB  " in result.stdout
    assert not install.exists()
    assert not default_lake.parent.exists()
    assert not app_data_lake.parent.exists()
    assert (home / "Library").is_dir()


def test_shortcuts_made_by_the_installer_are_removed(tmp_path, home, install, monkeypatch):
    import core

    monkeypatch.setenv("HOME", str(home))
    (home / "Applications").mkdir()
    (home / "Desktop").mkdir()
    # The shortcuts name the install they start, and the uninstaller removes that install,
    # so they must never name this repository.
    monkeypatch.setattr(core, "APP", install / "app")
    shutil.copytree(REPO / "app" / "static" / "icons", install / "app" / "static" / "icons")
    # Only core's view of subprocess: the uninstaller below needs the real one.
    monkeypatch.setattr(core, "subprocess", types.SimpleNamespace(run=lambda *args, **kwargs: None))
    core._mac_app()
    assert (home / "Desktop" / "biomnikhil.app").is_symlink()
    assert (home / "Applications" / "Uninstall biomnikhil.command").exists()
    shortcut_text = (home / "Applications" / "Uninstall biomnikhil.command").read_text()
    assert str(REPO) not in shortcut_text

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not (home / "Applications" / "biomnikhil.app").exists()
    assert not (home / "Desktop" / "biomnikhil.app").is_symlink()
    assert not (home / "Applications" / "Uninstall biomnikhil.command").exists()


def test_look_alike_shortcuts_stay(tmp_path, home, install):
    _write(home / "Applications" / "biomnikhil.app" / "Contents" / "Info.plist", "someone else's")
    other = tmp_path / "other.app"
    other.mkdir()
    (home / "Desktop").mkdir()
    (home / "Desktop" / "biomnikhil.app").symlink_to(other)

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (home / "Applications" / "biomnikhil.app" / "Contents" / "Info.plist").exists()
    assert (home / "Desktop" / "biomnikhil.app").is_symlink()


@pytest.mark.parametrize("answer", ["", "remove\n", "yes\n", "REMOVE everything\n"])
def test_anything_but_the_word_cancels(tmp_path, home, install, answer):
    lake = _make_lake(home / "biomnikhil-data")
    result = _run(tmp_path, install, answer)
    assert result.returncode == EXIT_CANCELLED
    assert "Nothing was removed" in result.stdout
    assert (install / "app" / "config.py").exists()
    assert (lake / "data_lake" / "genes.parquet").exists()


def test_nothing_found_removes_nothing(tmp_path, home):
    """Started from a folder that isn't an install: the stray copy stays."""
    stray = tmp_path / "stray"
    (stray / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "uninstall-mac.sh", stray / "app" / "uninstall-mac.sh")
    _write(stray / "data" / "important.txt")
    result = _run(tmp_path, stray, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED
    assert "No biomnikhil files were found" in result.stdout
    assert (stray / "data" / "important.txt").exists()


def test_chosen_folder_loses_only_the_lake_and_defaults_to_kept(tmp_path, home, install):
    chosen = tmp_path / "my folder"
    lake = _make_lake(chosen)
    _write(chosen / "thesis.docx")
    _save_location(install, "custom", chosen)

    result = _run(tmp_path, install, "\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert str(lake) in result.stdout
    assert f"Kept: {lake}" in result.stdout
    assert (lake / "data_lake" / "genes.parquet").exists()
    assert (chosen / "thesis.docx").exists()
    assert not install.exists()


def test_chosen_folder_confirmed_by_path_keeps_everything_else(tmp_path, home, install):
    chosen = tmp_path / "my folder"
    lake = _make_lake(chosen)
    _write(chosen / "thesis.docx")
    _write(lake / "my-notes.txt")
    _save_location(install, "custom", chosen)

    result = _run(tmp_path, install, f"{lake}\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not (lake / "data_lake").exists()
    assert not (lake / "benchmark").exists()
    assert not (lake / ".sizes.json").exists()
    assert (lake / "my-notes.txt").exists()
    assert (chosen / "thesis.docx").exists()
    assert "holds files biomnikhil didn't create" in result.stdout


def test_lake_in_the_home_folder_never_touches_home(tmp_path, home, install):
    """The custom preset starts at the home folder."""
    lake = _make_lake(home)
    _write(home / "keep.txt")
    _save_location(install, "custom", home)

    result = _run(tmp_path, install, f"{lake}\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not lake.exists()
    assert (home / "keep.txt").exists()


def test_shared_lake_needs_its_full_path(tmp_path, home, install):
    shared_parent = tmp_path / "system-support" / "biomnikhil"
    lake = _make_lake(shared_parent)
    _save_location(install, "system", shared_parent)

    result = _run(tmp_path, install, "yes\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (lake / "data_lake" / "genes.parquet").exists()
    assert not install.exists()


def test_shared_lake_removed_when_named(tmp_path, home, install):
    shared_parent = tmp_path / "system-support" / "biomnikhil"
    lake = _make_lake(shared_parent)

    result = _run(tmp_path, install, f"{lake}\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not shared_parent.exists()


def test_shared_lake_is_not_taken_for_an_own_one(tmp_path, home, install):
    """Saved as shared, in a folder that is also the default preset."""
    parent = home / "biomnikhil-data"
    lake = _make_lake(parent)
    _save_location(install, "system", parent)
    result = _run(tmp_path, install, "\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (lake / "data_lake" / "genes.parquet").exists()


def test_every_recorded_location_is_found(tmp_path, home):
    previous = tmp_path / "previous"
    from_env_file = tmp_path / "from-env-file"
    install = _make_install(home / "Library" / "biomnikhil", [f'BIOMNI_DATA_PATH="{from_env_file}"'])
    previous_lake = _make_lake(previous)
    env_lake = _make_lake(from_env_file)
    _save_location(install, "default", home / "biomnikhil-data", [previous])
    _make_lake(home / "biomnikhil-data")

    # Two "may be shared" prompts, in the order the script finds them.
    result = _run(tmp_path, install, f"{previous_lake}\n{env_lake}\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not (previous_lake / "data_lake").exists()
    assert not (env_lake / "data_lake").exists()
    assert not (home / "biomnikhil-data").exists()


def test_a_folder_named_biomni_data_without_lake_content_stays(tmp_path, home, install):
    chosen = tmp_path / "chosen"
    _write(chosen / "biomni_data" / "mine.txt")
    _save_location(install, "custom", chosen)
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (chosen / "biomni_data" / "mine.txt").exists()
    assert "doesn't hold data_lake/" in result.stdout


def test_unusable_saved_paths_are_ignored(tmp_path, home, install):
    for bad_path in ("", "relative/folder", "/", str(tmp_path / "gone"), str(tmp_path / ".." / "x")):
        _save_location(install, "custom", bad_path)
        result = _run(tmp_path, install, "")
        assert result.returncode == EXIT_CANCELLED, bad_path
        assert (install / "app" / "config.py").exists()


def test_symlinks_are_not_followed(tmp_path, home, install):
    outside = tmp_path / "outside"
    _write(outside / "precious.txt")
    (install / "data" / "linked").symlink_to(outside)
    (install / "runtime" / "linked").symlink_to(outside)

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (outside / "precious.txt").exists()
    assert not install.exists()


def test_a_symlinked_lake_is_left_alone(tmp_path, home, install):
    real_lake = _make_lake(tmp_path / "real")
    (home / "biomnikhil-data").mkdir()
    (home / "biomnikhil-data" / "biomni_data").symlink_to(real_lake)

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (real_lake / "data_lake" / "genes.parquet").exists()
    assert (home / "biomnikhil-data" / "biomni_data").is_symlink()
    assert "because it's a symlink" in result.stdout


def test_symlink_at_the_standard_location_is_left_alone(tmp_path, home):
    elsewhere = _make_install(tmp_path / "elsewhere")
    (home / "Library").mkdir()
    link = home / "Library" / "biomnikhil"
    link.symlink_to(elsewhere)
    stray = tmp_path / "stray"
    (stray / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "uninstall-mac.sh", stray / "app" / "uninstall-mac.sh")

    result = _run(tmp_path, stray, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED
    assert (elsewhere / "app" / "config.py").exists()
    assert link.is_symlink()


def test_refuses_the_home_folder_as_an_install(tmp_path, home):
    """The script found inside what looks like an install, at the home folder."""
    _make_install(home)
    _write(home / "keep.txt")
    result = _run(tmp_path, home, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED
    assert "Refused" in result.stdout
    assert (home / "app" / "config.py").exists()
    assert (home / "keep.txt").exists()


def test_a_broken_home_is_refused(tmp_path, install):
    env = _environment(tmp_path)
    for bad_home in ("", "relative", "/"):
        env["HOME"] = bad_home
        command = ["/bin/bash", str(install / "app" / "uninstall-mac.sh")]
        result = subprocess.run(command, env=env, input="REMOVE\n", capture_output=True, text=True, timeout=60)
        assert result.returncode == EXIT_REFUSED, bad_home
    assert (install / "app" / "config.py").exists()


def test_an_in_place_install_it_started_from_is_removed(tmp_path, home):
    in_place = _make_install(tmp_path / "old place")
    _make_lake(in_place / "data")
    _write(in_place / "setup-mac.command")
    _write(in_place / "my-own-file.txt")

    result = _run(tmp_path, in_place, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not (in_place / "app").exists()
    assert not (in_place / "data").exists()
    assert not (in_place / "setup-mac.command").exists()
    assert (in_place / "my-own-file.txt").exists()  # not ours
    assert "holds files biomnikhil didn't create" in result.stdout


def _make_checkout(root):
    _make_install(root)
    _write(root / "tests" / "test_x.py")
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.org"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _write(root / ".gitignore", "data/\nruntime/\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), *identity, "commit", "-qm", "x"], check=True)


def test_a_checkout_keeps_its_source(tmp_path, home):
    checkout = tmp_path / "checkout"
    _make_checkout(checkout)
    _make_lake(checkout / "data")

    result = _run(tmp_path, checkout, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert "git checkout" in result.stdout
    assert (checkout / "app" / "config.py").exists()
    assert (checkout / "app" / "uninstall-mac.sh").exists()
    assert (checkout / "tests" / "test_x.py").exists()
    assert (checkout / ".git").is_dir()
    assert not (checkout / "data").exists()
    assert not (checkout / "runtime").exists()
    status = subprocess.run(["git", "-C", str(checkout), "status", "--short"], capture_output=True, text=True)
    assert status.stdout == ""


def test_a_checkout_keeps_tracked_data(tmp_path, home):
    checkout = tmp_path / "checkout"
    _make_install(checkout)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "data/.env"], check=True)

    result = _run(tmp_path, checkout, "REMOVE\n")

    assert (checkout / "data" / ".env").exists()
    assert (checkout / "runtime" / ".venv" / ".biomnikhil-ready").exists()
    assert "git tracks files" in result.stdout or "isn't available" in result.stdout


def test_a_checkout_keeps_a_lake_inside_tracked_data(tmp_path, home):
    checkout = tmp_path / "checkout"
    _make_install(checkout)
    lake = _make_lake(checkout / "data")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "data"], check=True)

    result = _run(tmp_path, checkout, "REMOVE\n")

    assert (lake / "data_lake" / "genes.parquet").exists(), result.stdout
    assert (lake / "benchmark" / "hle" / "a.json").exists()


def test_a_git_dir_in_the_environment_does_not_hide_tracked_data(tmp_path, home):
    checkout = tmp_path / "checkout"
    _make_install(checkout)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "add", "data/.env"], check=True)
    other_repository = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other_repository)], check=True)

    result = _run(tmp_path, checkout, "REMOVE\n", {"GIT_DIR": str(other_repository / ".git")})

    assert (checkout / "data" / ".env").exists(), result.stdout
    assert (checkout / "runtime" / ".venv" / ".biomnikhil-ready").exists()


def test_a_git_repository_that_is_not_biomnikhil_is_left_alone(tmp_path, home):
    other_project = tmp_path / "other project"
    _write(other_project / "data" / "results.csv")
    _write(other_project / "runtime" / "keep.txt")
    (other_project / "app").mkdir()
    shutil.copy(REPO / "app" / "uninstall-mac.sh", other_project / "app" / "uninstall-mac.sh")
    subprocess.run(["git", "init", "-q", str(other_project)], check=True)

    result = _run(tmp_path, other_project, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (other_project / "data" / "results.csv").exists()
    assert (other_project / "runtime" / "keep.txt").exists()


@pytest.mark.parametrize("preset", ["biomnikhil-data", "Library/Application Support/biomnikhil"])
def test_a_preset_that_is_a_symlink_keeps_its_target_lake_by_default(tmp_path, home, install, preset):
    shared = tmp_path / "shared drive"
    lake = _make_lake(shared)
    link = home / preset
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(shared)

    result = _run(tmp_path, install, "\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (lake / "data_lake" / "genes.parquet").exists(), result.stdout
    assert (lake / ".sizes.json").exists()


def test_spaces_and_wildcards_in_a_lake_path_match_only_that_path(tmp_path, home, install):
    chosen = tmp_path / "my [lab] * lake"
    lake = _make_lake(chosen)
    look_alike = _make_lake(tmp_path / "my l * lake")
    _write(lake / "notes.txt")
    _save_location(install, "custom", chosen)

    result = _run(tmp_path, install, f"{lake.resolve()}\nREMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not (lake / "data_lake").exists()
    assert (lake / "notes.txt").exists()
    assert (look_alike / "data_lake" / "genes.parquet").exists()


@pytest.mark.parametrize("text", ["", "{", "[]", "null", '{"path": ""}', '{"path": 5, "previous": 5}',
                                  '{"path": "relative/x"}', '{"path": "/"}', '{"previous": ["", "..", "/"]}'])
def test_empty_or_malformed_saved_settings_remove_nothing_extra(tmp_path, home, install, text):
    _write(home / "biomni_data" / "data_lake" / "keep.txt")
    _write(home / "unrelated.txt")
    (install / "data" / "datalake-location.json").write_text(text)

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not install.exists()
    assert (home / "biomni_data" / "data_lake" / "keep.txt").exists()
    assert (home / "unrelated.txt").exists()


class _Ping(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"biomnikhil")

    def log_message(self, *args):
        pass


def test_refuses_when_it_answers_on_its_port(tmp_path, home):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ping)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        install = _make_install(home / "Library" / "biomnikhil")
        (install / "data" / ".env").write_text(f"PORT={server.server_address[1]}\n")
        result = _run(tmp_path, install, "REMOVE\n")
    finally:
        server.shutdown()
    assert result.returncode == EXIT_REFUSED
    assert "biomnikhil is running" in result.stdout
    assert (install / "app" / "config.py").exists()


def test_refuses_while_the_server_runs_from_the_install(tmp_path, home, install):
    server = install / "runtime" / ".venv" / "bin" / "waitress-serve"
    _write(server, "sleep 60\n")
    process = subprocess.Popen(["/bin/bash", str(server)])
    try:
        time.sleep(0.3)
        result = _run(tmp_path, install, "REMOVE\n")
    finally:
        process.kill()
        process.wait()
    assert result.returncode == EXIT_REFUSED
    assert (install / "app" / "config.py").exists()


def test_refuses_while_the_window_runs_by_relative_path(tmp_path, home, install):
    """run-mac.sh starts window.py with a path relative to the install."""
    _write(install / "app" / "installer" / "window.py", "import time\ntime.sleep(60)\n")
    process = subprocess.Popen([sys.executable, "app/installer/window.py", "8000"], cwd=install)
    try:
        time.sleep(0.3)
        result = _run(tmp_path, install, "REMOVE\n")
    finally:
        process.kill()
        process.wait()
    assert result.returncode == EXIT_REFUSED, result.stdout
    assert (install / "app" / "config.py").exists()


def test_reports_what_could_not_be_removed(tmp_path, home, install):
    """A locked file (chflags uchg) stops rm: the run must say so, not claim success."""
    locked = install / "data" / "locked.txt"
    _write(locked)
    subprocess.run(["/usr/bin/chflags", "uchg", str(locked)], check=True)
    try:
        result = _run(tmp_path, install, "REMOVE\n")
        assert result.returncode == EXIT_INCOMPLETE, result.stdout
        assert "NOT completely removed" in result.stdout
        assert str(install / "data") in result.stdout
        assert "biomnikhil was removed." not in result.stdout
    finally:
        subprocess.run(["/usr/bin/chflags", "nouchg", str(locked)], check=True)


def test_read_only_folders_are_removed(tmp_path, home, install):
    cache = install / "runtime" / "tools" / "cache"
    _write(cache / "wheel.whl")
    cache.chmod(0o500)
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not install.exists()


def test_finishes_after_deleting_its_own_folder(tmp_path, home, install):
    """Output after the deletion proves the running script survived it."""
    result = _run(tmp_path, install, "REMOVE\n")
    assert not (install / "app" / "uninstall-mac.sh").exists()
    assert result.stdout.rstrip().endswith("so they stay.")


def test_the_launcher_runs_it_and_passes_on_the_answer(tmp_path, home, install):
    launcher = install / "uninstall-mac.command"
    shutil.copy(REPO / "uninstall-mac.command", launcher)
    result = subprocess.run(["/bin/bash", str(launcher)], env=_environment(tmp_path), input="REMOVE\n",
                            cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not install.exists()


def test_launchers_are_executable_and_app_scripts_ship_with_the_install():
    assert os.access(REPO / "uninstall-mac.command", os.X_OK)
    assert (REPO / "app" / "uninstall.ps1").exists()
    assert (REPO / "app" / "uninstall-mac.sh").exists()


def _make_stray(tmp_path):
    """The uninstaller in a folder that isn't an install, so only HOME is searched."""
    stray = tmp_path / "stray"
    (stray / "app").mkdir(parents=True)
    shutil.copy(REPO / "app" / "uninstall-mac.sh", stray / "app" / "uninstall-mac.sh")
    return stray


def test_folders_a_lake_merge_emptied_are_removed(tmp_path, home, install):
    """_merge_duplicates empties a lake but leaves the folder around it."""
    default_parent = home / "biomnikhil-data"
    default_parent.mkdir()
    app_data_parent = home / "Library" / "Application Support" / "biomnikhil"
    _write(app_data_parent / "biomni_data" / ".sizes.json", "{}")

    result = _run(tmp_path, install, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not default_parent.exists()
    assert not app_data_parent.exists()


def test_emptied_lake_folders_are_found_without_an_install(tmp_path, home):
    default_parent = home / "biomnikhil-data"
    (default_parent / "biomni_data").mkdir(parents=True)
    result = _run(tmp_path, _make_stray(tmp_path), "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert str(default_parent) in result.stdout
    assert not default_parent.exists()


def test_every_probe_and_finder_file_goes_with_an_empty_folder(tmp_path, home, install):
    parent = home / "biomnikhil-data"
    _write(parent / ".DS_Store")
    _write(parent / ".biomnikhil-write-test")
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not parent.exists()


def test_an_old_in_place_install_loses_what_old_releases_left(tmp_path, home):
    old = _make_install(tmp_path / "old place")
    for name in (".env", ".env.example", ".gitignore", "README.md", "requirements.txt", "requirements.lock",
                 "wsgi.py", "Start biomnikhil.cmd", "Start biomnikhil.command", ".setup-mac.command.update"):
        _write(old / name)
    for folder in (".venv", "tools", "docs", "scripts", "tests"):
        _write(old / folder / "file")

    result = _run(tmp_path, old, "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not old.exists()
    assert "didn't create" not in result.stdout


def test_a_lake_named_only_by_an_old_root_env_file_is_offered(tmp_path, home):
    chosen = tmp_path / "chosen"
    lake = _make_lake(chosen)
    old = _make_install(tmp_path / "old place")
    _write(old / ".env", f"BIOMNI_DATA_PATH={chosen}\n")
    result = _run(tmp_path, old, f"{lake}\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not lake.exists()


def test_a_failed_run_keeps_what_it_takes_to_run_again(tmp_path, home, install):
    shutil.copy(REPO / "uninstall-mac.command", install / "uninstall-mac.command")
    locked = install / "data" / "locked.txt"
    _write(locked)
    subprocess.run(["/usr/bin/chflags", "uchg", str(locked)], check=True)
    try:
        first = _run(tmp_path, install, "REMOVE\n")
        assert first.returncode == EXIT_INCOMPLETE, first.stdout
        assert "didn't create" not in first.stdout
        assert (install / "app" / "uninstall-mac.sh").exists()
        assert (install / "uninstall-mac.command").exists()
    finally:
        subprocess.run(["/usr/bin/chflags", "nouchg", str(locked)], check=True)

    second = _run(tmp_path, install, "REMOVE\n")

    assert second.returncode == EXIT_FINISHED, second.stdout + second.stderr
    assert not install.exists()


def test_a_partial_install_in_the_standard_folder_is_removed(tmp_path, home):
    standard = home / "Library" / "biomnikhil"
    _write(standard / "runtime" / "tools" / "uv")
    _write(standard / "data" / "app.log")
    result = _run(tmp_path, _make_stray(tmp_path), "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not standard.exists()


def test_an_empty_standard_folder_is_removed(tmp_path, home):
    """setup-mac.command makes the folder before it copies anything."""
    standard = home / "Library" / "biomnikhil"
    standard.mkdir(parents=True)
    result = _run(tmp_path, _make_stray(tmp_path), "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not standard.exists()


@pytest.mark.parametrize("folder_name", ["old place", "it's old"])
def test_the_install_the_app_shortcut_starts_is_removed(tmp_path, home, folder_name):
    """Otherwise the shortcut goes and the install it starts stays, unreported."""
    import shlex

    old = _make_install(tmp_path / folder_name)
    bundle = home / "Applications" / "biomnikhil.app" / "Contents"
    _write(bundle / "Info.plist", "edu.ucla.biomnikhil")
    _write(bundle / "MacOS" / "biomnikhil",
           f"#!/bin/bash\nexec /bin/bash {shlex.quote(str(old / 'app' / 'run-mac.sh'))}\n")

    result = _run(tmp_path, _make_stray(tmp_path), "REMOVE\n")

    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not old.exists()
    assert not bundle.exists()


def test_a_shortcut_to_something_else_removes_nothing_there(tmp_path, home):
    other = tmp_path / "other"
    _write(other / "data" / "mine.txt")
    bundle = home / "Applications" / "biomnikhil.app" / "Contents"
    _write(bundle / "Info.plist", "edu.ucla.biomnikhil")
    _write(bundle / "MacOS" / "biomnikhil", f"#!/bin/bash\nexec /bin/bash {other}/app/run-mac.sh\n")
    result = _run(tmp_path, _make_stray(tmp_path), "REMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert (other / "data" / "mine.txt").exists()


def test_a_lake_at_the_install_root_does_not_report_the_root_as_kept(tmp_path, home):
    old = tmp_path / "old place"
    _make_install(old, [f"BIOMNI_DATA_PATH={old}"])
    lake = _make_lake(old)
    result = _run(tmp_path, old, f"{lake}\nREMOVE\n")
    assert result.returncode == EXIT_FINISHED, result.stdout + result.stderr
    assert not old.exists()
    assert "didn't create" not in result.stdout


def test_the_list_is_shown_again_only_after_a_lake_question(tmp_path, home, install):
    result = _run(tmp_path, install, "REMOVE\n")
    assert result.stdout.count("This will permanently delete:") == 1


def test_setup_puts_the_uninstall_launcher_in_the_install(tmp_path, home):
    extracted = tmp_path / "extracted"
    _write(extracted / "app" / "run-mac.sh", "exit 0\n")
    shutil.copy(REPO / "setup-mac.command", extracted / "setup-mac.command")
    shutil.copy(REPO / "uninstall-mac.command", extracted / "uninstall-mac.command")
    env = _environment(tmp_path)
    env["BIOMNIKHIL_NO_DIALOGS"] = "1"
    subprocess.run(["/bin/bash", str(extracted / "setup-mac.command")], env=env, check=True,
                   cwd=tmp_path, capture_output=True, timeout=60)
    assert os.access(home / "Library" / "biomnikhil" / "uninstall-mac.command", os.X_OK)


@pytest.mark.parametrize("name", ["app/uninstall-mac.sh", "app/uninstall.ps1",
                                  "uninstall-mac.command", "uninstall-windows.cmd"])
def test_the_uninstallers_are_not_left_out_of_the_release_zip(name):
    attribute = subprocess.run(["git", "-C", str(REPO), "check-attr", "export-ignore", "--", name],
                               capture_output=True, text=True, check=True)
    assert attribute.stdout.strip().endswith("unspecified")
