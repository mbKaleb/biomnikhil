"""The Settings tab: its API, and starting setup and cleanup. Nothing here starts
a helper script or closes anything: those calls are replaced."""
import importlib
import json
import os
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from app.config import Config
from app.services import maintenance
from app.services.tasks import registry

REPO = Path(__file__).resolve().parents[1]

needs_mac = pytest.mark.skipif(sys.platform != "darwin", reason="Mac helper script")
needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows PowerShell")


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "settings.json"
    monkeypatch.setattr(Config, "SETTINGS_FILE", path)
    return path


@pytest.fixture
def started(monkeypatch):
    """Records what would start, in place of the helpers and the app closing."""
    calls = []

    def start_helper(action):
        calls.append(("helper", action))

    def close_app():
        calls.append(("close", None))

    monkeypatch.setattr(maintenance, "_start_helper_windows", start_helper)
    monkeypatch.setattr(maintenance, "_start_helper_mac", start_helper)
    monkeypatch.setattr(maintenance, "_close_app", close_app)
    return calls


# ---- /api/settings -------------------------------------------------------------

def test_settings_need_sign_in(client):
    assert client.get("/api/settings").status_code == 401


def test_beta_is_off_by_default(client, signed_in, settings_file):
    data = client.get("/api/settings").get_json()
    assert data["beta_updates"] is False
    assert data["version"]


def test_saving_beta_needs_the_request_token(client, signed_in, settings_file):
    response = client.post("/api/settings", json={"beta_updates": True})
    assert response.status_code == 403
    assert not settings_file.exists()


def test_beta_setting_is_saved(client, signed_in, token, settings_file):
    response = client.post("/api/settings", json={"beta_updates": True}, headers=token)
    assert response.get_json() == {"beta_updates": True}
    assert client.get("/api/settings").get_json()["beta_updates"] is True
    assert json.loads(settings_file.read_text())["beta_updates"] is True


def test_saving_keeps_other_settings(client, signed_in, token, settings_file):
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text('{"something_else": 5}')
    client.post("/api/settings", json={"beta_updates": True}, headers=token)
    assert json.loads(settings_file.read_text()) == {"something_else": 5, "beta_updates": True}


@pytest.mark.parametrize("value", ["true", 1, None])
def test_beta_setting_must_be_a_boolean(client, signed_in, token, settings_file, value):
    response = client.post("/api/settings", json={"beta_updates": value}, headers=token)
    assert response.status_code == 400
    assert not settings_file.exists()


def test_the_updater_reads_what_the_app_saves(client, signed_in, token, tmp_path, settings_file):
    import update

    client.post("/api/settings", json={"beta_updates": True}, headers=token)
    assert update.beta_updates_on(tmp_path) is True


# ---- /api/maintenance ----------------------------------------------------------

@pytest.mark.parametrize("action", ["setup", "cleanup"])
def test_maintenance_starts_the_helper_before_closing(client, signed_in, token, started, action):
    response = client.post(f"/api/maintenance/{action}", headers=token)
    assert response.status_code == 202
    assert started == [("helper", action), ("close", None)]


def test_maintenance_needs_the_request_token(client, signed_in, started):
    assert client.post("/api/maintenance/cleanup").status_code == 403
    assert started == []


def test_maintenance_needs_sign_in(client, token, started):
    assert client.post("/api/maintenance/cleanup", headers=token).status_code == 401
    assert started == []


def test_uninstall_is_not_an_action(client, signed_in, token, started):
    assert client.post("/api/maintenance/uninstall", headers=token).status_code == 404
    assert started == []


def test_maintenance_refuses_from_another_computer(client, signed_in, token, started):
    response = client.post(
        "/api/maintenance/cleanup", headers=token,
        environ_overrides={"REMOTE_ADDR": "192.168.1.20"},
    )
    assert response.status_code == 403
    assert started == []


def test_maintenance_waits_for_a_running_task(client, signed_in, token, started):
    task = registry.create("a task in the way")
    try:
        response = client.post("/api/maintenance/setup", headers=token)
    finally:
        registry.fail(task.id, "test over")
    assert response.status_code == 409
    assert "task" in response.get_json()["error"]
    assert started == []


def test_maintenance_waits_for_the_data_lake_download(client, signed_in, token, started, monkeypatch):
    monkeypatch.setattr(maintenance.datalake, "is_downloading", lambda: True)
    response = client.post("/api/maintenance/cleanup", headers=token)
    assert response.status_code == 409
    assert started == []


def test_the_app_stays_open_when_the_helper_cannot_start(client, signed_in, token, started, monkeypatch):
    def cannot_start(action):
        raise OSError("no such program")

    monkeypatch.setattr(maintenance, "_start_helper_windows", cannot_start)
    monkeypatch.setattr(maintenance, "_start_helper_mac", cannot_start)
    response = client.post("/api/maintenance/cleanup", headers=token)
    assert response.status_code == 500
    assert "no such program" in response.get_json()["error"]
    assert started == []


def test_closing_asks_the_window_and_then_exits(tmp_path, monkeypatch):
    request_file = tmp_path / "data" / ".close-request"
    monkeypatch.setattr(Config, "CLOSE_REQUEST_FILE", request_file)
    timers = []

    class FakeTimer:
        daemon = False

        def __init__(self, delay, function, args):
            self.function = function
            self.args = args
            timers.append(self)

        def start(self):
            pass

    monkeypatch.setattr(maintenance.threading, "Timer", FakeTimer)
    maintenance._close_app()
    assert request_file.exists()
    assert timers[0].function is os._exit
    assert timers[0].args == (0,)


def _window_module(monkeypatch):
    """app/installer/window.py, with no GUI libraries behind it. Importing it turns
    on diagnostics, which would write to the real data/ and hook this process."""
    fake_diagnostics = types.ModuleType("diagnostics")
    fake_diagnostics.enable = lambda: None
    fake_gui = types.ModuleType("gui")
    fake_gui._mac_branding = lambda: None
    monkeypatch.setitem(sys.modules, "diagnostics", fake_diagnostics)
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setitem(sys.modules, "gui", fake_gui)
    monkeypatch.setattr(sys, "argv", ["window.py"])
    monkeypatch.delitem(sys.modules, "window", raising=False)
    module = importlib.import_module("window")
    monkeypatch.delitem(sys.modules, "window", raising=False)
    return module


def test_the_app_and_its_window_use_the_same_close_request(monkeypatch):
    window = _window_module(monkeypatch)
    assert window.CLOSE_REQUEST == Config.CLOSE_REQUEST_FILE


def test_the_app_and_the_updater_use_the_same_settings_file():
    import update

    assert Config.SETTINGS_FILE == REPO / "data" / "settings.json"
    assert '"data" / "settings.json"' in Path(update.__file__).read_text()


def test_the_window_closes_when_asked(tmp_path, monkeypatch):
    window = _window_module(monkeypatch)
    request_file = tmp_path / ".close-request"
    monkeypatch.setattr(window, "CLOSE_REQUEST", request_file)
    destroyed = threading.Event()
    fake_window = types.SimpleNamespace(destroy=destroyed.set)
    watcher = threading.Thread(target=window.close_when_asked, args=(fake_window,), daemon=True)
    watcher.start()
    assert not destroyed.wait(0.2)
    request_file.touch()
    assert destroyed.wait(5)


def test_helper_environment_drops_what_the_server_was_given(monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere")
    monkeypatch.setenv("BIOMNIKHIL_REINSTALL", "1")
    monkeypatch.setenv("KEPT_VARIABLE", "kept")
    environment = maintenance._helper_environment()
    assert "VIRTUAL_ENV" not in environment
    assert "BIOMNIKHIL_REINSTALL" not in environment
    assert environment["KEPT_VARIABLE"] == "kept"


# ---- the helper scripts -------------------------------------------------------

def test_mac_helpers_are_executable():
    assert os.access(REPO / "app" / "maintenance-mac.sh", os.X_OK)
    assert os.access(REPO / "app" / "cleanup-mac.command", os.X_OK)


def test_uninstallers_do_not_count_the_helpers_as_the_app():
    assert "*maintenance-mac*|*cleanup-mac.command*" in (REPO / "app" / "uninstall-mac.sh").read_text()
    assert "maintenance\\.ps1" in (REPO / "app" / "uninstall.ps1").read_text()


@needs_mac
@pytest.mark.parametrize("script", ["maintenance-mac.sh", "cleanup-mac.command"])
def test_mac_helpers_parse(script):
    subprocess.run(["/bin/bash", "-n", str(REPO / "app" / script)], check=True)


@needs_mac
def test_mac_helper_refuses_an_unknown_action(tmp_path):
    result = subprocess.run(
        ["/bin/bash", str(REPO / "app" / "maintenance-mac.sh"), "uninstall"],
        capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "Unknown action" in result.stdout


@needs_windows
def test_windows_helper_parses():
    script = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{REPO / 'app' / 'maintenance.ps1'}', "
        "[ref]$null, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Host $_ }; exit 1 }"
    )
    powershell = shutil.which("powershell")
    subprocess.run([powershell, "-NoProfile", "-Command", script], check=True)


@needs_windows
def test_windows_helper_refuses_an_unknown_action():
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(REPO / "app" / "maintenance.ps1"), "-Action", "uninstall"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Waiting for biomnikhil" not in result.stdout
