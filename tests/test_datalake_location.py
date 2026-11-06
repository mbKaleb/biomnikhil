"""The data lake's fixed home inside the install, and bringing in lakes earlier versions left elsewhere."""
import json
import pickle

import pytest

from app import config
from app.config import Config
from app.services import datalake


@pytest.fixture
def install(tmp_path, monkeypatch):
    """An install with no env override and a throwaway home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("BIOMNI_DATA_PATH", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
    monkeypatch.setattr(datalake.Path, "home", lambda: home)
    monkeypatch.setattr(config, "DATA_LOCATION_FILE", tmp_path / "location.json")
    monkeypatch.setattr(Config, "DATA_PATH", tmp_path / "install-data")
    monkeypatch.setattr(datalake, "_merge_problems", [])
    return tmp_path


def _lake_file(data_path, name, content):
    path = data_path / "biomni_data" / "data_lake" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _installed(name):
    return Config.DATA_PATH / "biomni_data" / "data_lake" / name


def test_lake_lives_in_the_install_data_folder():
    source = open(config.__file__).read()
    assert 'Path(_env("BIOMNI_DATA_PATH", str(_DATA_ROOT)))' in source


def test_old_saved_location_no_longer_moves_the_lake(install):
    config.DATA_LOCATION_FILE.write_text(json.dumps({"kind": "custom", "path": str(install / "old")}))
    before = Config.DATA_PATH
    datalake.status()
    assert Config.DATA_PATH == before


def test_lake_in_the_old_home_default_is_moved_in(install):
    source = _lake_file(install / "home" / "biomnikhil-data", "file.csv", b"data")
    assert datalake.duplicates()[0]["kept"] is False
    datalake._merge_duplicates()
    assert not source.exists()
    assert _installed("file.csv").read_bytes() == b"data"
    assert datalake.duplicates() == []


def test_lake_in_a_typed_in_folder_is_copied_and_kept(install):
    old = install / "maybe-shared"
    source = _lake_file(old, "file.csv", b"data")
    config.DATA_LOCATION_FILE.write_text(json.dumps({"kind": "custom", "path": str(old)}))
    assert datalake.duplicates()[0]["kept"] is True
    datalake._merge_duplicates()
    assert source.read_bytes() == b"data"
    assert _installed("file.csv").read_bytes() == b"data"


def test_shared_system_lake_is_never_read(install):
    shared = install / "shared"
    _lake_file(shared, "file.pkl", b"data")
    config.DATA_LOCATION_FILE.write_text(json.dumps({"kind": "system", "path": str(shared)}))
    assert datalake.duplicates() == []


def test_damaged_file_stays_behind_and_is_reported(install):
    old = install / "home" / "biomnikhil-data"
    damaged = _lake_file(old, "file.json", b'{"ok":')
    datalake._merge_duplicates()
    assert damaged.exists()
    assert not _installed("file.json").exists()
    assert "damaged" in datalake.status()["merge_problems"][0]["reason"]


def test_differing_file_keeps_the_installed_copy(install):
    old = install / "home" / "biomnikhil-data"
    differing = _lake_file(old, "file.csv", b"old version")
    _lake_file(Config.DATA_PATH, "file.csv", b"new version")
    datalake._merge_duplicates()
    assert differing.exists()
    assert _installed("file.csv").read_bytes() == b"new version"


def test_env_var_leaves_other_lakes_alone(install, monkeypatch):
    _lake_file(install / "home" / "biomnikhil-data", "file.csv", b"data")
    monkeypatch.setenv("BIOMNI_DATA_PATH", str(install))
    assert datalake.duplicates() == []


def test_tasks_wait_for_a_complete_lake(client, signed_in, token, install, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: False)
    response = client.post("/api/chat", json={"prompt": "hello"}, headers=token)
    assert response.status_code == 409
    assert response.get_json()["needs_data_lake"] is True


def test_complete_lake_lets_tasks_run(install, monkeypatch):
    monkeypatch.setattr(datalake, "_manifest_complete", lambda lake_dir=None: True)
    assert datalake.task_blocker() is None


def test_location_route_is_gone(client, signed_in, token):
    response = client.post("/api/datalake/location", json={"kind": "custom"}, headers=token)
    assert response.status_code in (404, 405)


def test_corrupt_file_is_listed_in_the_form_repair_looks_for(install, monkeypatch):
    bad_file = _lake_file(Config.DATA_PATH, "bad.csv", b"")
    monkeypatch.setattr(datalake, "_invalid_files", [])
    monkeypatch.setattr(datalake, "_validated", False)
    monkeypatch.setattr(datalake, "_valid", None)
    monkeypatch.setattr(datalake, "_validation_error", None)
    datalake._run_validation()
    assert datalake._invalid_files == ["data_lake/bad.csv"]
    assert bad_file.exists()


def test_json_is_read_as_utf8_whatever_the_system_encoding(tmp_path):
    json_file = tmp_path / "file.json"
    json_file.write_bytes('{"name": "β-catenin"}'.encode())
    assert datalake._check_file(json_file) is None


def test_verifying_a_pickle_does_not_run_it(tmp_path):
    marker = tmp_path / "ran"
    path = tmp_path / "planted.pkl"
    path.write_bytes(pickle.dumps(_WritesMarker(str(marker))))
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
