"""The guards in app/__init__.py and app/api/routes.py that keep a local
code-running API from being driven by other websites or agent-written files."""
from pathlib import Path

from app.config import Config


def test_ping_works_signed_out(client):
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.data == b"biomnikhil"


def test_foreign_host_is_rejected(client):
    # DNS rebinding: another site's hostname pointed at 127.0.0.1.
    response = client.get("/ping", headers={"Host": "attacker.example:8000"})
    assert response.status_code == 403


def test_api_needs_sign_in(client):
    assert client.get("/api/chats").status_code == 401


def test_page_redirects_to_login(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_post_without_token_is_rejected(client, signed_in):
    response = client.post("/api/chats", json={"title": "x"})
    assert response.status_code == 403


def test_post_with_wrong_token_is_rejected(client, signed_in):
    response = client.post("/api/chats", json={"title": "x"}, headers={"X-Biomnikhil-Token": "guess"})
    assert response.status_code == 403


def test_post_with_token_works(client, signed_in, token):
    response = client.post("/api/chats", json={"title": "x"}, headers=token)
    assert response.status_code == 201


def _write_output(task_id, name, data):
    folder = Config.OUTPUT_PATH / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def test_agent_html_is_sandboxed(client, signed_in):
    _write_output("task1", "plot.html", b"<script>fetch('/api/chats')</script>")
    response = client.get("/api/tasks/task1/outputs/plot.html")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == "sandbox allow-scripts"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_agent_image_is_not_sandboxed_but_not_sniffed(client, signed_in):
    _write_output("task1", "plot.png", b"\x89PNG")
    response = client.get("/api/tasks/task1/outputs/plot.png")
    assert "Content-Security-Policy" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_output_path_cannot_escape_its_task(client, signed_in):
    _write_output("task1", "ok.txt", b"ok")
    (Config.OUTPUT_PATH / "secret.txt").write_text("secret")
    response = client.get("/api/tasks/task1/outputs/..%2Fsecret.txt")
    assert response.status_code == 404


def test_zip_skips_paths_outside_the_task(client, signed_in, token):
    (Config.OUTPUT_PATH / "task2").mkdir(parents=True, exist_ok=True)
    (Config.OUTPUT_PATH / "secret.txt").write_text("secret")
    files = [{"task_id": "task2", "path": "../secret.txt"}]
    response = client.post("/api/outputs/zip", json={"files": files}, headers=token)
    assert response.status_code == 404


def test_harness_file_cannot_escape_its_folder(client, signed_in):
    response = client.get("/api/harness/recipes/..%2F..%2Fchats.db")
    assert response.status_code == 404


def test_save_copies_outputs_into_one_downloads_folder(client, signed_in, token, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_output("save1", "table.csv", b"first")
    _write_output("save2", "table.csv", b"second")
    (Config.OUTPUT_PATH / "secret.txt").write_text("secret")
    files = [
        {"task_id": "save1", "path": "table.csv"},
        {"task_id": "save2", "path": "table.csv"},
        {"task_id": "save1", "path": "../secret.txt"},
    ]
    response = client.post("/api/outputs/save", json={"files": files}, headers=token)
    assert response.status_code == 200
    assert response.get_json()["count"] == 2
    folder = Path(response.get_json()["folder"])
    assert folder.parent == tmp_path / "Downloads"
    assert (folder / "table.csv").read_bytes() == b"first"
    assert (folder / "table (2).csv").read_bytes() == b"second"
    assert not (folder / "secret.txt").exists()
