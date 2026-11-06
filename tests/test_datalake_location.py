"""Picking where the data lake goes: the choices offered, and which folders are refused."""
import json
import pickle
import sys
from collections import namedtuple

import pytest

from app import config
from app.config import Config
from app.services import datalake


@pytest.fixture
def picker(tmp_path, monkeypatch):
    """A picker with no env override, a throwaway home, and its own saved file."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("BIOMNI_DATA_PATH", raising=False)
    monkeypatch.setattr(datalake.Path, "home", lambda: home)
    monkeypatch.setattr(config, "DATA_LOCATION_FILE", tmp_path / "location.json")
    monkeypatch.setattr(config, "_DATA_ROOT", tmp_path / "install-data")
    monkeypatch.setattr(Config, "DATA_PATH", tmp_path / "install-data")
    return tmp_path


def _kinds():
    return [choice["kind"] for choice in datalake.location()["choices"]]


def test_fresh_install_offers_four_locations(picker):
    assert _kinds() == ["default", "custom", "system", "local_app"]
    assert datalake.location()["selected"] == "default"
    assert datalake.location()["chosen"] is False


def test_partial_download_in_the_app_folder_stays_on_offer(picker):
    lake = Config.DATA_PATH / "biomni_data"
    lake.mkdir(parents=True)
    (lake / "file.parquet").write_bytes(b"partial")
    assert _kinds()[0] == "install"
    assert datalake.location()["selected"] == "install"


def test_set_location_saves_and_moves_the_lake(picker):
    target = picker / "elsewhere"
    result = datalake.set_location("custom", str(target))
    assert Config.DATA_PATH == target.resolve()
    assert result["path"] == str(target.resolve() / "biomni_data")
    assert result["location"]["chosen"] is True
    saved = json.loads(config.DATA_LOCATION_FILE.read_text())
    assert saved["kind"] == "custom"
    assert saved["path"] == str(target.resolve())


def test_default_location_is_in_the_home_folder(picker):
    datalake.set_location("default")
    assert Config.DATA_PATH == (picker / "home" / "biomnikhil-data").resolve()


@pytest.mark.parametrize("path", ["", "relative/folder"])
def test_custom_path_must_be_absolute(picker, path):
    with pytest.raises(datalake.LocationError, match="full path"):
        datalake.set_location("custom", path)


def test_unknown_kind_is_refused(picker):
    with pytest.raises(datalake.LocationError, match="Unknown"):
        datalake.set_location("nonsense")


def test_unwritable_folder_is_refused(picker):
    blocker = picker / "a-file"
    blocker.write_text("")
    with pytest.raises(datalake.LocationError, match="Can't write"):
        datalake.set_location("custom", str(blocker / "child"))
    assert not config.DATA_LOCATION_FILE.exists()


def test_too_little_disk_space_is_refused(picker, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(datalake.shutil, "disk_usage", lambda _path: usage(1, 1, 1024))
    with pytest.raises(datalake.LocationError, match="GB free"):
        datalake.set_location("custom", str(picker / "small"))


def test_env_var_locks_the_location(picker, monkeypatch):
    monkeypatch.setenv("BIOMNI_DATA_PATH", str(picker))
    assert datalake.location()["locked"] is True
    with pytest.raises(datalake.LocationError, match="BIOMNI_DATA_PATH"):
        datalake.set_location("default")


def test_route_reports_a_refused_folder(client, signed_in, token, picker):
    response = client.post(
        "/api/datalake/location",
        json={"kind": "custom", "path": "relative"},
        headers=token,
    )
    assert response.status_code == 400
    assert "full path" in response.get_json()["error"]


def _lake_file(data_path, name, contents):
    path = data_path / "biomni_data" / "data_lake" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path


def test_old_location_is_found_as_a_duplicate(picker):
    _lake_file(Config.DATA_PATH, "a.csv", b"a")
    old = Config.DATA_PATH.resolve()
    datalake.set_location("custom", str(picker / "new"))
    found = datalake.duplicates()
    assert [item["path"] for item in found] == [str(old / "biomni_data")]


def test_merge_moves_missing_files_and_drops_identical_ones(picker):
    old = Config.DATA_PATH
    _lake_file(old, "only-old.csv", b"old")
    _lake_file(old, "both.csv", b"same")
    datalake.set_location("custom", str(picker / "new"))
    _lake_file(Config.DATA_PATH, "both.csv", b"same")

    datalake._merge_duplicates()

    lake = Config.DATA_PATH / "biomni_data" / "data_lake"
    assert (lake / "only-old.csv").read_bytes() == b"old"
    assert (lake / "both.csv").read_bytes() == b"same"
    assert not (old / "biomni_data").exists()
    assert datalake.duplicates() == []


def test_merge_replaces_a_damaged_file(picker):
    old = Config.DATA_PATH
    _lake_file(old, "file.json", b'{"ok": true}')
    datalake.set_location("custom", str(picker / "new"))
    damaged = _lake_file(Config.DATA_PATH, "file.json", b'{"ok":')

    datalake._merge_duplicates()

    assert damaged.read_bytes() == b'{"ok": true}'


def test_merge_keeps_copies_it_cannot_prove_identical(picker):
    old = Config.DATA_PATH
    differing = _lake_file(old, "file.csv", b"old version")
    damaged = _lake_file(old, "broken.json", b"{")
    datalake.set_location("custom", str(picker / "new"))
    kept = _lake_file(Config.DATA_PATH, "file.csv", b"new version")

    datalake._merge_duplicates()

    assert kept.read_bytes() == b"new version"
    assert differing.read_bytes() == b"old version"
    assert damaged.exists()
    assert len(datalake.duplicates()) == 1


def test_location_inside_a_lake_is_refused(picker):
    lake = Config.DATA_PATH / "biomni_data"
    lake.mkdir(parents=True)
    for path in (lake, lake / "nested"):
        with pytest.raises(datalake.LocationError, match="inside an existing data lake"):
            datalake.set_location("custom", str(path))


def test_merge_reports_what_it_left_behind(picker):
    differing = _lake_file(Config.DATA_PATH, "file.csv", b"old version")
    datalake.set_location("custom", str(picker / "new"))
    _lake_file(Config.DATA_PATH, "file.csv", b"new version")

    datalake._merge_duplicates()

    problems = datalake.status()["merge_problems"]
    assert [problem["path"] for problem in problems] == [str(differing.resolve())]
    assert "differs" in problems[0]["reason"]


def test_shared_folder_is_never_merged_from(picker, monkeypatch):
    shared = picker / "shared"
    presets = dict(datalake._location_presets(), system=shared)
    monkeypatch.setattr(datalake, "_location_presets", lambda: presets)
    shared_file = _lake_file(shared, "file.csv", b"shared")
    datalake.set_location("default")

    assert datalake.duplicates() == []
    datalake._merge_duplicates()
    assert shared_file.exists()


def test_lake_in_a_shared_folder_merges_nothing(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_shared_lake_problem", lambda data_path: None)
    private_file = _lake_file(Config.DATA_PATH, "file.csv", b"private")
    datalake.set_location("system", str(picker / "shared"))

    assert datalake.duplicates() == []
    datalake._merge_duplicates()
    assert private_file.exists()


def test_leaving_a_shared_folder_does_not_merge_from_it(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_shared_lake_problem", lambda data_path: None)
    shared = picker / "shared"
    datalake.set_location("system", str(shared))
    shared_file = _lake_file(shared, "file.csv", b"shared")
    datalake.set_location("custom", str(picker / "new"))

    assert datalake.duplicates() == []
    assert shared_file.exists()


def test_read_only_shared_lake_with_complete_files_is_allowed(picker, monkeypatch):
    shared = picker / "shared"
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: True)
    lake_file = _lake_file(shared, "file.pkl", b"data")
    for path in (shared, shared / "biomni_data", lake_file.parent):
        path.chmod(0o755)
    lake_file.chmod(0o644)
    real_mkdir = datalake.Path.mkdir

    def refuse_inside_shared(self, *args, **kwargs):
        if self == shared.resolve():
            raise PermissionError("read-only")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(datalake.Path, "mkdir", refuse_inside_shared)
    datalake.set_location("system", str(shared))
    assert Config.DATA_PATH == shared.resolve()


def test_tasks_wait_for_a_location(client, signed_in, token, picker, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: False)
    response = client.post("/api/chat", json={"prompt": "hello"}, headers=token)
    assert response.status_code == 409
    assert response.get_json()["needs_data_lake_location"] is True


def test_complete_lake_in_the_app_folder_needs_no_choice(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: True)
    assert datalake.needs_location() is False


def test_chosen_location_needs_no_further_choice(picker, monkeypatch):
    datalake.set_location("default")
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: False)
    assert datalake.needs_location() is False


def test_picked_folder_is_copied_from_and_never_emptied(picker):
    datalake.set_location("custom", str(picker / "maybe-shared"))
    source = _lake_file(Config.DATA_PATH, "file.csv", b"data")
    datalake.set_location("default")
    assert datalake.duplicates()[0]["kept"] is True

    datalake._merge_duplicates()

    assert source.read_bytes() == b"data"
    assert (Config.DATA_PATH / "biomni_data" / "data_lake" / "file.csv").read_bytes() == b"data"


def test_verifying_a_pickle_does_not_run_it(tmp_path):
    marker = tmp_path / "ran"
    payload = pickle.dumps(_WritesMarker(str(marker)))
    path = tmp_path / "planted.pkl"
    path.write_bytes(payload)
    assert datalake._check_file(path) is None
    assert not marker.exists()


def test_truncated_pickle_fails_verification(tmp_path):
    path = tmp_path / "short.pkl"
    path.write_bytes(pickle.dumps({"a": 1})[:-1])
    assert datalake._check_file(path) is not None


class _WritesMarker:
    def __init__(self, path):
        self.path = path

    def __reduce__(self):
        return (open, (self.path, "w"))


def test_missing_folder_blocks_tasks_and_downloads(picker):
    target = picker / "drive"
    datalake.set_location("custom", str(target))
    target.rmdir()

    assert datalake.status()["state"] == "unavailable"
    assert "isn't available" in datalake.task_blocker()
    assert datalake.start_download() is False
    assert not target.exists()


def test_incomplete_shared_lake_is_refused(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: False)
    with pytest.raises(datalake.LocationError, match="administrator"):
        datalake.set_location("system", str(picker / "shared"))
    assert not (picker / "shared").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_writable_shared_lake_is_refused(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: True)
    shared = picker / "shared"
    planted = _lake_file(shared, "file.pkl", b"data")
    for path in (shared, shared / "biomni_data", planted.parent):
        path.chmod(0o755)
    planted.chmod(0o666)
    with pytest.raises(datalake.LocationError, match="other users"):
        datalake.set_location("system", str(shared))


def test_shared_lake_that_becomes_incomplete_blocks_tasks(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_shared_lake_problem", lambda data_path: None)
    datalake.set_location("system", str(picker))
    monkeypatch.setattr(datalake, "_shared_lake_problem", lambda data_path: "broken")
    assert datalake.task_blocker() == "broken"


def test_app_never_downloads_into_a_shared_lake(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_shared_lake_problem", lambda data_path: None)
    datalake.set_location("system", str(picker))
    assert datalake.start_download() is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_shared_lake_under_an_open_folder_is_refused(picker, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: True)
    open_parent = picker / "open"
    shared = open_parent / "shared"
    lake_file = _lake_file(shared, "file.pkl", b"data")
    for path in (shared, shared / "biomni_data", lake_file.parent):
        path.chmod(0o755)
    lake_file.chmod(0o644)
    open_parent.chmod(0o777)
    with pytest.raises(datalake.LocationError, match="any user"):
        datalake.set_location("system", str(shared))
