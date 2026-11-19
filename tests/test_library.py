"""User Files: saving a run's output into the library, tags, and where a file came from."""
import io

import pytest

from app.config import Config
from app.services import chat_store, library


@pytest.fixture(autouse=True)
def empty_library():
    Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    for path in Config.UPLOAD_PATH.iterdir():
        if path.is_file():
            path.unlink()
            library.forget(path.name)


def _make_output(task_id, relpath, text="a,b\n1,2\n"):
    path = Config.OUTPUT_PATH / task_id / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _save(client, token, files, chat_id=None):
    body = {"files": files, "chat_id": chat_id}
    return client.post("/api/files/from-outputs", json=body, headers=token)


def _listed(client):
    return {entry["name"]: entry for entry in client.get("/api/files").get_json()["files"]}


def test_saving_an_output_copies_it_and_records_where_it_came_from(client, token, signed_in):
    chat = chat_store.create_chat("Peak calling")
    _make_output("task-a", "results/peaks.csv")

    response = _save(client, token, [{"task_id": "task-a", "path": "results/peaks.csv"}], chat["id"])

    assert response.status_code == 201
    assert response.get_json()["saved"][0]["name"] == "peaks.csv"
    assert (Config.UPLOAD_PATH / "peaks.csv").read_text() == "a,b\n1,2\n"
    entry = _listed(client)["peaks.csv"]
    assert entry["source"] == "chat"
    assert entry["chat_id"] == chat["id"]
    assert entry["chat_title"] == "Peak calling"
    assert entry["origin_path"] == "results/peaks.csv"


def test_saving_the_same_output_twice_makes_one_copy(client, token, signed_in):
    _make_output("task-a", "peaks.csv")
    entry = [{"task_id": "task-a", "path": "peaks.csv"}]

    first = _save(client, token, entry).get_json()["saved"][0]
    second = _save(client, token, entry).get_json()["saved"][0]

    assert first["new"] is True
    assert second["new"] is False
    assert second["name"] == first["name"]
    assert list(_listed(client)) == ["peaks.csv"]


def test_same_name_from_another_run_gets_a_number_not_an_overwrite(client, token, signed_in):
    _make_output("task-a", "plot.csv", "first")
    _make_output("task-b", "plot.csv", "second")

    _save(client, token, [{"task_id": "task-a", "path": "plot.csv"}])
    second = _save(client, token, [{"task_id": "task-b", "path": "plot.csv"}]).get_json()["saved"][0]

    assert second["name"] == "plot-2.csv"
    assert (Config.UPLOAD_PATH / "plot.csv").read_text() == "first"
    assert (Config.UPLOAD_PATH / "plot-2.csv").read_text() == "second"


def test_saved_name_is_one_a_run_can_be_attached_by(client, token, signed_in):
    _make_output("task-a", "my plot (final).png", "png")

    name = _save(client, token, [{"task_id": "task-a", "path": "my plot (final).png"}]).get_json()["saved"][0]["name"]

    from werkzeug.utils import secure_filename
    assert secure_filename(name) == name
    assert (Config.UPLOAD_PATH / name).is_file()


def test_paths_that_leave_the_run_folder_are_not_saved(client, token, signed_in):
    (Config.OUTPUT_PATH / "task-a").mkdir(parents=True, exist_ok=True)
    (Config.OUTPUT_PATH / "secret.txt").write_text("secret")

    response = _save(client, token, [{"task_id": "task-a", "path": "../secret.txt"}])

    assert response.status_code == 404
    assert not (Config.UPLOAD_PATH / "secret.txt").exists()


def test_save_needs_the_request_token(client, signed_in):
    response = client.post("/api/files/from-outputs", json={"files": [{"task_id": "t", "path": "p"}]})
    assert response.status_code == 403


def test_hand_uploads_are_listed_as_uploads(client, token, signed_in):
    client.post(
        "/api/files",
        data={"files": (io.BytesIO(b"x"), "counts.tsv")},
        headers=token,
        content_type="multipart/form-data",
    )

    entry = _listed(client)["counts.tsv"]

    assert entry["source"] == "upload"
    assert entry["chat_id"] is None
    assert entry["tags"] == []


def test_a_file_put_in_the_folder_by_hand_still_lists(client, signed_in):
    (Config.UPLOAD_PATH / "dropped.csv").write_text("x")
    (Config.UPLOAD_PATH / ".DS_Store").write_text("junk")

    listed = _listed(client)

    assert "dropped.csv" in listed
    assert ".DS_Store" not in listed
    assert listed["dropped.csv"]["source"] == "upload"


def test_tags_are_cleaned_and_kept_with_the_file(client, token, signed_in):
    (Config.UPLOAD_PATH / "sample.mzML").write_text("x")

    response = client.put(
        "/api/files/sample.mzML/tags",
        json={"tags": ["  Mass Spec ", "mass spec", "Batch-1", ""]},
        headers=token,
    )

    assert response.get_json()["tags"] == ["mass spec", "batch-1"]
    assert _listed(client)["sample.mzML"]["tags"] == ["batch-1", "mass spec"]


def test_tags_reject_bad_input(client, token, signed_in):
    (Config.UPLOAD_PATH / "sample.csv").write_text("x")

    too_long = client.put("/api/files/sample.csv/tags", json={"tags": ["x" * 31]}, headers=token)
    not_a_list = client.put("/api/files/sample.csv/tags", json={"tags": "raw"}, headers=token)
    unknown = client.put("/api/files/nope.csv/tags", json={"tags": ["a"]}, headers=token)

    assert too_long.status_code == 400
    assert not_a_list.status_code == 400
    assert unknown.status_code == 404


def test_tags_survive_a_reupload_but_not_a_delete(client, token, signed_in):
    (Config.UPLOAD_PATH / "sample.csv").write_text("old")
    client.put("/api/files/sample.csv/tags", json={"tags": ["keep"]}, headers=token)

    client.post(
        "/api/files",
        data={"files": (io.BytesIO(b"new"), "sample.csv")},
        headers=token,
        content_type="multipart/form-data",
    )
    assert _listed(client)["sample.csv"]["tags"] == ["keep"]

    client.delete("/api/files/sample.csv", headers=token)
    (Config.UPLOAD_PATH / "sample.csv").write_text("again")
    assert _listed(client)["sample.csv"]["tags"] == []


def test_a_file_removed_outside_the_app_loses_its_tags(client, token, signed_in):
    (Config.UPLOAD_PATH / "gone.csv").write_text("x")
    client.put("/api/files/gone.csv/tags", json={"tags": ["old"]}, headers=token)

    (Config.UPLOAD_PATH / "gone.csv").unlink()
    assert "gone.csv" not in _listed(client)

    (Config.UPLOAD_PATH / "gone.csv").write_text("x")
    assert _listed(client)["gone.csv"]["tags"] == []


def test_a_deleted_chat_leaves_the_saved_file_without_a_title(client, token, signed_in):
    chat = chat_store.create_chat("Temporary")
    _make_output("task-a", "peaks.csv")
    _save(client, token, [{"task_id": "task-a", "path": "peaks.csv"}], chat["id"])

    chat_store.delete_chat(chat["id"])

    entry = _listed(client)["peaks.csv"]
    assert entry["source"] == "chat"
    assert entry["chat_title"] is None
