"""The optional workflow stages: lab context, preprocessing and review."""
import pytest

from app.config import Config
from app.services import biomni_runner, chat_store, workflow
from app.services.tasks import registry


class RecordingAgent:
    """Answers each prompt with the next reply and keeps the prompts it saw."""

    llm = None
    module2api = {}

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def go_stream(self, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield {"output": f"<solution>{reply}</solution>"}


@pytest.fixture
def agents(monkeypatch):
    built = {}

    def install(main, preprocess=(), review=(), assay=()):
        built["main"] = RecordingAgent(main)
        built["preprocess"] = RecordingAgent(preprocess)
        built["review"] = RecordingAgent(review)
        built["assay"] = RecordingAgent(assay)
        monkeypatch.setattr(biomni_runner, "_get_agent", lambda: built["main"])
        monkeypatch.setattr(biomni_runner, "_get_stage_agent", lambda role, model: built[role])
        monkeypatch.setattr(biomni_runner, "_reset_run_namespace", lambda: resets.append(1))
        return built

    resets = []
    install.resets = resets
    return install


def _run(features, input_files=None, lab_context=""):
    chat_id = chat_store.create_chat("test")["id"]
    task = registry.create("a prompt", chat_id=chat_id)
    output_dir = Config.OUTPUT_PATH / task.id
    output_dir.mkdir(parents=True)
    biomni_runner._run(
        task.id, "a prompt", chat_id, "a prompt", output_dir, None,
        features, input_files or [], lab_context,
    )
    return registry.get(task.id)


def test_all_off_runs_only_the_main_agent(agents):
    built = agents(["the answer"])
    task = _run({})
    assert task["result"] == "the answer"
    assert built["preprocess"].prompts == []
    assert built["review"].prompts == []


def test_features_are_only_on_when_exactly_true():
    selected = workflow.selected_features({"review": "true", "context": True, "other": True})
    assert selected == {"context": True, "preprocess": False, "assay": False, "review": False}


def test_preprocess_runs_first_and_main_gets_its_output(agents, tmp_path):
    built = agents(["the answer"], preprocess=["STATUS: READY\nlooks fine"])
    task = _run({"preprocess": True}, [tmp_path / "data.csv"])
    assert "data.csv" in built["preprocess"].prompts[0]
    assert "looks fine" in built["main"].prompts[0]
    assert workflow.PREPROCESS_FOLDER in built["main"].prompts[0]
    assert task["result"] == "the answer"


def test_preprocess_is_skipped_without_files(agents):
    built = agents(["the answer"])
    task = _run({"preprocess": True})
    assert built["preprocess"].prompts == []
    assert any("skipped" in step["text"] for step in task["steps"])


def test_blocked_preprocess_stops_the_task_and_review(agents, tmp_path):
    built = agents(["never"], preprocess=["STATUS: BLOCKED\nlabels don't match"])
    task = _run({"preprocess": True, "review": True}, [tmp_path / "data.csv"])
    assert built["main"].prompts == []
    assert built["review"].prompts == []
    assert "labels don't match" in task["result"]


def test_preprocess_without_a_status_warns(agents, tmp_path):
    agents(["the answer"], preprocess=["looks fine"])
    task = _run({"preprocess": True}, [tmp_path / "data.csv"])
    assert "clear status" in task["result"]


def test_status_and_verdict_only_count_at_the_start():
    quoted = "Summary.\nVERDICT: HOLDS\nSTATUS: BLOCKED"
    assert workflow.review_verdict(quoted) is None
    assert workflow.preprocess_blocked_reason(quoted) is None


def test_stage_agents_get_the_install_hints(agents, tmp_path, monkeypatch):
    monkeypatch.setattr(biomni_runner.hints, "build_hints", lambda prompt: "INSTALL HINTS")
    built = agents(["the answer"], preprocess=["STATUS: READY"], review=["VERDICT: HOLDS"])
    _run({"preprocess": True, "review": True}, [tmp_path / "data.csv"])
    assert "INSTALL HINTS" in built["preprocess"].prompts[0]
    assert "INSTALL HINTS" in built["review"].prompts[0]


def test_changed_input_file_is_flagged(agents, tmp_path):
    built = agents(["the answer"])
    data_file = tmp_path / "data.csv"
    data_file.write_text("a,b\n")

    def edits_the_file(prompt):
        data_file.write_text("a,b,c\n")
        yield {"output": "<solution>the answer</solution>"}

    built["main"].go_stream = edits_the_file
    task = _run({}, [data_file])
    assert "data.csv" in task["result"]
    assert "changed during the run" in task["result"]


def test_unchanged_input_file_is_not_flagged(agents, tmp_path):
    agents(["the answer"])
    data_file = tmp_path / "data.csv"
    data_file.write_text("a,b\n")
    assert _run({}, [data_file])["result"] == "the answer"


@pytest.mark.parametrize("review,expected", [
    (["VERDICT: HOLDS"], True),
    (["VERDICT: HOLDS WITH CAVEATS"], True),
    (["VERDICT: DOES NOT HOLD"], False),
    (["no verdict"], False),
    ([RuntimeError("boom")], False),
])
def test_recipe_is_saved_only_after_a_passing_review(agents, monkeypatch, review, expected):
    saved = []
    monkeypatch.setattr(biomni_runner.hints, "save_recipe_if_verified", lambda *args: saved.append(args))
    agents(["first", "second"], review=review)
    _run({"review": True})
    assert bool(saved) is expected


def test_stages_stay_on_the_model_and_effort_the_run_started_with(agents, tmp_path, monkeypatch):
    built = agents(["the answer"], preprocess=["STATUS: READY"])
    monkeypatch.setattr(biomni_runner, "_current_model", "first-model")
    monkeypatch.setattr(biomni_runner, "_current_effort", "low")
    models = []
    monkeypatch.setattr(
        biomni_runner, "_get_stage_agent",
        lambda role, model: models.append(model) or built[role],
    )
    original_stream = built["preprocess"].go_stream

    def switches_mid_run(prompt):
        biomni_runner._current_model = "second-model"
        biomni_runner._current_effort = "high"
        yield from original_stream(prompt)

    built["preprocess"].go_stream = switches_mid_run
    efforts = []
    original_stream_main = built["main"].go_stream

    def records_effort(prompt):
        efforts.append(biomni_runner._run_effort[1])
        yield from original_stream_main(prompt)

    built["main"].go_stream = records_effort
    _run({"preprocess": True}, [tmp_path / "data.csv"])
    assert models == ["first-model"]
    assert efforts == ["low"]


def test_failed_preprocess_still_runs_the_task_and_says_so(agents, tmp_path):
    built = agents(["the answer"], preprocess=[RuntimeError("boom")])
    task = _run({"preprocess": True}, [tmp_path / "data.csv"])
    assert built["main"].prompts
    assert task["status"] == "done"
    assert "original files" in task["result"]


def test_stopping_in_preprocess_stops_the_run(agents, tmp_path):
    built = agents(["never"], preprocess=[RuntimeError("stopped by user")])
    task = _run({"preprocess": True}, [tmp_path / "data.csv"])
    assert built["main"].prompts == []
    assert task["status"] == "error"


def test_review_sees_code_and_answer_but_not_the_reasoning(agents):
    built = agents(["the answer"], review=["VERDICT: HOLDS\nchecked"])
    built["main"].go_stream = lambda prompt: iter([
        {"output": "my private reasoning<execute>print(1)</execute>"},
        {"output": "<solution>the answer</solution>"},
    ])
    task = _run({"review": True})
    prompt = built["review"].prompts[0]
    assert "the answer" in prompt
    assert "private reasoning" not in prompt
    code_file = Config.OUTPUT_PATH / task["id"] / workflow.REVIEW_FOLDER / "analysis-code.txt"
    assert code_file.read_text() == "print(1)"
    assert task["result"].startswith("the answer")
    assert "VERDICT: HOLDS" in task["result"]


def test_each_agent_starts_from_an_empty_namespace(agents, tmp_path):
    agents(["the answer"], preprocess=["STATUS: READY"], review=["VERDICT: HOLDS"])
    _run({"preprocess": True, "review": True}, [tmp_path / "data.csv"])
    assert len(agents.resets) == 3


def test_failed_review_keeps_the_answer_and_says_unreviewed(agents):
    agents(["the answer"], review=[RuntimeError("boom")])
    task = _run({"review": True})
    assert task["status"] == "done"
    assert task["result"].startswith("the answer")
    assert "unreviewed" in task["result"]


def test_stopping_in_review_keeps_the_answer(agents):
    agents(["the answer"], review=[RuntimeError("stopped by user")])
    task = _run({"review": True})
    assert task["result"].startswith("the answer")
    assert "unreviewed" in task["result"]


def test_review_without_a_verdict_is_not_a_pass(agents):
    built = agents(["the answer"], review=["seems fine"])
    task = _run({"review": True})
    assert "no clear verdict" in task["result"]
    assert len(built["main"].prompts) == 1


def test_failing_review_triggers_exactly_one_revision(agents):
    built = agents(["first", "second"], review=["VERDICT: DOES NOT HOLD\nwrong stat"])
    task = _run({"review": True})
    assert len(built["main"].prompts) == 2
    assert len(built["review"].prompts) == 1
    assert "wrong stat" in built["main"].prompts[1]
    assert task["result"].startswith("second")
    assert "first" in task["result"]
    assert "wasn't re-reviewed" in task["result"]


def test_failed_revision_keeps_the_original_and_the_warning(agents):
    agents(["first", RuntimeError("boom")], review=["VERDICT: DOES NOT HOLD\nwrong"])
    task = _run({"review": True})
    assert task["result"].startswith("first")
    assert "did not hold up" in task["result"]


def test_caveats_verdict_is_not_read_as_a_plain_pass():
    assert workflow.review_verdict("VERDICT: HOLDS WITH CAVEATS\nx") == "HOLDS WITH CAVEATS"


def test_assay_runs_after_the_main_agent_on_its_code(agents, tmp_path):
    built = agents(["the answer"], assay=["ASSAY: RAN\n2x found 90%"])
    built["main"].go_stream = lambda prompt: iter([
        {"output": "my private reasoning<execute>print(1)</execute>"},
        {"output": "<solution>the answer</solution>"},
    ])
    task = _run({"assay": True}, [tmp_path / "data.csv"])
    prompt = built["assay"].prompts[0]
    assert "data.csv" in prompt
    assert "the answer" in prompt
    assert "private reasoning" not in prompt
    code_file = Config.OUTPUT_PATH / task["id"] / workflow.ASSAY_FOLDER / "analysis-code.txt"
    assert code_file.read_text() == "print(1)"
    assert task["result"].startswith("the answer")
    assert "**Planted-truth assay**" in task["result"]
    assert "2x found 90%" in task["result"]


def test_assay_is_skipped_without_files(agents):
    built = agents(["the answer"])
    task = _run({"assay": True})
    assert built["assay"].prompts == []
    assert task["result"] == "the answer"
    assert any("assay skipped" in step["text"] for step in task["steps"])


def test_assay_prompt_asks_for_nulls_planted_folds_and_the_limit(tmp_path):
    prompt = workflow.assay_prompt("a request", "an answer", tmp_path / "code.txt", [tmp_path / "data.csv"], tmp_path, "")
    assert "shuffled" in prompt
    assert "pipeline.py" in prompt
    assert "truth.json" in prompt
    for fold in workflow.ASSAY_FOLDS:
        assert f"{fold}x" in prompt
    assert "can't show that any one original hit is real" in prompt


@pytest.mark.parametrize("reply,expected_heading", [
    ("ASSAY: NOT APPLICABLE\nno groups", "doesn't apply"),
    ("ASSAY: COULDN'T REPRODUCE\n3 hits differ", "tested nothing"),
    ("ASSAY: COULDN’T REPRODUCE\n3 hits differ", "tested nothing"),
    ("it went fine", "no clear status"),
])
def test_an_assay_that_tested_nothing_says_so(agents, tmp_path, reply, expected_heading):
    agents(["the answer"], assay=[reply])
    task = _run({"assay": True}, [tmp_path / "data.csv"])
    assert task["result"].startswith("the answer")
    assert expected_heading in task["result"]
    assert "**Planted-truth assay**" not in task["result"]


def test_assay_status_only_counts_at_the_start():
    assert workflow.assay_status("Summary.\nASSAY: RAN") is None
    assert workflow.assay_status("**ASSAY: RAN**\nx") == "RAN"


def test_failed_assay_keeps_the_answer_and_says_untested(agents, tmp_path):
    agents(["the answer"], assay=[RuntimeError("boom")])
    task = _run({"assay": True}, [tmp_path / "data.csv"])
    assert task["status"] == "done"
    assert task["result"].startswith("the answer")
    assert "untested" in task["result"]


def test_review_sees_the_assay_results(agents, tmp_path):
    built = agents(["the answer"], assay=["ASSAY: RAN\n2x found 90%"], review=["VERDICT: HOLDS"])
    _run({"assay": True, "review": True}, [tmp_path / "data.csv"])
    assert "2x found 90%" in built["review"].prompts[0]


def test_stopping_in_the_assay_skips_the_review(agents, tmp_path, monkeypatch):
    saved = []
    monkeypatch.setattr(biomni_runner.hints, "save_recipe_if_verified", lambda *args: saved.append(args))
    built = agents(["the answer"], review=["VERDICT: HOLDS"])

    def stopped_by_the_user(prompt):
        monkeypatch.setattr(registry, "is_cancel_requested", lambda task_id: True)
        raise RuntimeError("stopped by user")
        yield

    built["assay"].go_stream = stopped_by_the_user
    task = _run({"assay": True, "review": True}, [tmp_path / "data.csv"])
    assert built["review"].prompts == []
    assert task["result"].startswith("the answer")
    assert "untested" in task["result"]
    assert "unreviewed" in task["result"]
    assert saved == []


def test_lab_context_reaches_every_agent(agents, tmp_path):
    built = agents(["the answer"], preprocess=["STATUS: READY"], review=["VERDICT: HOLDS"])
    _run({"preprocess": True, "review": True}, [tmp_path / "data.csv"], lab_context="use FDR 0.01")
    assert "use FDR 0.01" in built["preprocess"].prompts[0]
    assert "use FDR 0.01" in built["review"].prompts[0]


def test_submit_adds_lab_context_only_when_switched_on(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "LAB_CONTEXT_PATH", tmp_path / "lab.md")
    workflow.save_lab_context("use FDR 0.01")
    captured = []

    class NoThread:
        def __init__(self, target, args, daemon):
            captured.append(args)

        def start(self):
            pass

    monkeypatch.setattr(biomni_runner.threading, "Thread", NoThread)
    biomni_runner.submit("a prompt", features={"context": False})
    biomni_runner.submit("a prompt", features={"context": True})
    assert "use FDR 0.01" not in captured[0][1]
    assert "use FDR 0.01" in captured[1][1]


def test_lab_context_route_saves_and_limits(client, signed_in, token, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "LAB_CONTEXT_PATH", tmp_path / "user" / "lab.md")
    saved = client.put("/api/lab-context", json={"text": "β notes"}, headers=token)
    assert saved.status_code == 200
    assert client.get("/api/lab-context").get_json()["text"] == "β notes"
    too_long = "x" * (workflow.MAX_LAB_CONTEXT_CHARS + 1)
    refused = client.put("/api/lab-context", json={"text": too_long}, headers=token)
    assert refused.status_code == 400


def test_lab_context_route_needs_the_page_token(client, signed_in):
    assert client.put("/api/lab-context", json={"text": "x"}).status_code in (400, 403)


def _fake_model(monkeypatch, reply):
    calls = []

    def call(source, model, credentials):
        calls.append(source)
        return reply

    monkeypatch.setattr(workflow, "_call_condense_model", call)
    return calls


def test_condense_returns_a_draft_and_saves_nothing(client, signed_in, token, monkeypatch, tmp_path):
    saved_path = tmp_path / "lab.md"
    monkeypatch.setattr(Config, "LAB_CONTEXT_PATH", saved_path)
    signed_in.credentials = object()
    _fake_model(monkeypatch, "use FDR 0.01")
    response = client.post("/api/lab-context/condense", json={"text": "long: FDR 0.01"}, headers=token)
    assert response.status_code == 200
    assert response.get_json() == {"text": "use FDR 0.01", "new_numbers": []}
    assert not saved_path.exists()


def test_condense_flags_numbers_the_source_lacks(client, signed_in, token, monkeypatch):
    signed_in.credentials = object()
    _fake_model(monkeypatch, "use FDR 0.05 at 37 C, 1,000 reads")
    response = client.post("/api/lab-context/condense", json={"text": "FDR 0.01 at 37 C"}, headers=token)
    assert response.get_json()["new_numbers"] == ["0.05", "1,000"]


def test_condense_refuses_empty_and_oversized_text(client, signed_in, token, monkeypatch):
    signed_in.credentials = object()
    calls = _fake_model(monkeypatch, "x")
    assert client.post("/api/lab-context/condense", json={"text": "  "}, headers=token).status_code == 400
    too_long = "x" * (workflow.MAX_CONDENSE_SOURCE_CHARS + 1)
    assert client.post("/api/lab-context/condense", json={"text": too_long}, headers=token).status_code == 400
    assert calls == []


def test_condense_needs_credentials(client, signed_in, token, monkeypatch):
    signed_in.credentials = None
    calls = _fake_model(monkeypatch, "x")
    response = client.post("/api/lab-context/condense", json={"text": "notes"}, headers=token)
    assert response.status_code == 403
    assert calls == []


def test_condense_reports_a_model_failure(client, signed_in, token, monkeypatch):
    signed_in.credentials = object()

    def fails(source, model, credentials):
        raise workflow.CondenseFailed("The model declined")

    monkeypatch.setattr(workflow, "_call_condense_model", fails)
    response = client.post("/api/lab-context/condense", json={"text": "notes"}, headers=token)
    assert response.status_code == 502
    assert "declined" in response.get_json()["error"]


def test_condense_call_uses_the_given_credentials_and_model(monkeypatch):
    from types import SimpleNamespace

    from anthropic.lib import bedrock

    seen = {}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.messages = self

        def create(self, **kwargs):
            seen["request"] = kwargs
            block = SimpleNamespace(type="text", text=" notes ")
            return SimpleNamespace(stop_reason="end_turn", content=[block])

    monkeypatch.setattr(bedrock, "AnthropicBedrockMantle", FakeClient)
    frozen = SimpleNamespace(access_key="a", secret_key="s", token="t")
    credentials = SimpleNamespace(get_frozen_credentials=lambda: frozen)
    assert workflow._call_condense_model("doc", "a-model", credentials) == "notes"
    assert seen["client"]["aws_access_key"] == "a"
    assert seen["request"]["model"] == "a-model"
    assert "doc" in seen["request"]["messages"][0]["content"]


def test_condense_turns_an_unexpected_error_into_a_message(client, signed_in, token, monkeypatch):
    signed_in.credentials = object()

    def fails(source, model, credentials):
        raise ValueError("boom")

    monkeypatch.setattr(workflow, "_call_condense_model", fails)
    response = client.post("/api/lab-context/condense", json={"text": "notes"}, headers=token)
    assert response.status_code == 502
    assert response.get_json()["error"]
