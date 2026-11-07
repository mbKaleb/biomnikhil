"""A run only gets the files attached to its message: nothing carries over
from earlier runs or chats through the namespace, recipes or input folders."""
from pathlib import Path

from app.config import Config
from app.services import biomni_runner, hints
from app.services.tasks import registry


def _upload(name):
    Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    path = Config.UPLOAD_PATH / name
    path.write_text("data")
    return path


def _submitted_prompt(monkeypatch, client, token, files):
    captured = {}

    def fake_submit(prompt, chat_id=None, user=None, attached=None, features=None):
        captured["attached"] = attached
        return "task"

    monkeypatch.setattr(biomni_runner, "submit", fake_submit)
    client.post("/api/chat", json={"prompt": "go", "files": files}, headers=token)
    return captured["attached"]


def test_chat_passes_only_the_checked_files(monkeypatch, client, token, signed_in):
    checked = _upload("checked.csv")
    _upload("unchecked.csv")
    attached = _submitted_prompt(monkeypatch, client, token, ["checked.csv"])
    assert attached == [str(checked)]


def test_input_folder_holds_only_the_attached_files():
    attached = _upload("wanted.csv")
    _upload("other.csv")
    links = biomni_runner._prepare_inputs("task-inputs", [str(attached)])
    input_dir = Config.INPUT_PATH / "task-inputs"
    names = []
    for path in input_dir.iterdir():
        names.append(path.name)
    assert names == ["wanted.csv"]
    assert links[0].read_text() == "data"
    note = biomni_runner._inputs_note(links)
    assert str(links[0]) in note
    assert "other.csv" not in note


def test_note_says_when_nothing_is_attached():
    assert "attached no files" in biomni_runner._inputs_note([])


def test_finished_run_removes_links_but_not_uploads(monkeypatch):
    upload = _upload("kept.csv")

    class Agent:
        llm = None
        module2api = {}

        def go_stream(self, prompt):
            yield {"output": "<solution>done</solution>"}

    monkeypatch.setattr(biomni_runner, "_get_agent", lambda: Agent())
    task = registry.create("a prompt")
    biomni_runner._prepare_inputs(task.id, [str(upload)])
    biomni_runner._run(task.id, "a prompt", None, "a prompt", None, user=None)
    assert not (Config.INPUT_PATH / task.id).exists()
    assert upload.read_text() == "data"


def test_run_namespace_does_not_carry_over():
    from biomni.tool import support_tools

    biomni_runner._patch_run_python_repl()
    support_tools.run_python_repl('path = "/somewhere/old.mzML"')
    biomni_runner._reset_run_namespace()
    assert "NameError" in support_tools.run_python_repl("print(path)")


def test_recipe_has_no_files_from_its_run():
    upload = _upload("sample1.mzML")
    input_file = Config.INPUT_PATH / "task1" / "sample1.mzML"
    output_file = Config.OUTPUT_PATH / "task1" / "plots" / "a.png"
    code = (
        f'a = "{input_file}"\n'
        f'b = "{upload}"\n'
        f'c = "{output_file}"\n'
        'd = os.path.join(folder, "sample1.mzML")\n'
    )
    steps = [{"kind": "agent", "text": f"<execute>{code}</execute>"}]
    hints.save_recipe_if_verified("an mzml task", steps, [{"name": "a.png"}])
    recipe = hints._recipe_path("mzml").read_text()
    assert "sample1" not in recipe
    assert str(Config.OUTPUT_PATH) not in recipe
    assert 'a = "<INPUT_FILE>"' in recipe
    # What follows the placeholder keeps the run's own path separators.
    expected_output = Path("<OUTPUT_DIR>") / "plots" / "a.png"
    assert f'c = "{expected_output}"' in recipe
