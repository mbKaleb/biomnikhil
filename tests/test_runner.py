"""biomni_runner._run around a stand-in agent: what a run leaves in the task
registry and the chat, and its guardrails."""
import pytest

from app.config import Config
from app.services import biomni_runner, chat_store
from app.services.tasks import registry


class ScriptedAgent:
    """Yields the given outputs from go_stream, like A1 does."""

    llm = None  # stands in for the real agent LLM the effort is scoped to

    module2api = {}

    def __init__(self, outputs, writes=None):
        self.outputs = outputs
        self.writes = writes

    def go_stream(self, prompt):
        for output in self.outputs:
            if self.writes is not None:
                self.writes.write_text("a,b\n1,2\n")
            yield {"output": output}


def _run(monkeypatch, agent):
    monkeypatch.setattr(biomni_runner, "_get_agent", lambda: agent)
    chat_id = chat_store.create_chat("test")["id"]
    task = registry.create("a prompt", chat_id=chat_id)
    output_dir = Config.OUTPUT_PATH / task.id
    output_dir.mkdir(parents=True)
    agent.writes = output_dir / "table.csv" if agent.writes is True else None
    biomni_runner._run(task.id, "a prompt", chat_id, "a prompt", output_dir, user=None)
    return registry.get(task.id), chat_store.get_chat(chat_id)


def test_finished_run_records_answer_and_files(monkeypatch):
    agent = ScriptedAgent(["<execute>x</execute>", "<solution>the answer</solution>"], writes=True)
    task, chat = _run(monkeypatch, agent)
    assert task["status"] == "done"
    assert task["result"] == "the answer"
    assert [f["path"] for f in task["files"]] == ["table.csv"]
    last = chat["messages"][-1]
    assert last["role"] == "assistant"
    assert last["content"] == "the answer"


def test_repeating_agent_is_stopped(monkeypatch):
    agent = ScriptedAgent(["<execute>same</execute>"] * 5)
    task, chat = _run(monkeypatch, agent)
    assert task["status"] == "error"
    assert "repeated the same response" in task["error"]
    assert chat["messages"][-1]["content"].startswith("[error]")


def test_runaway_agent_is_stopped(monkeypatch):
    outputs = []
    for i in range(biomni_runner.MAX_STEPS + 10):
        outputs.append(f"<execute>step {i}</execute>")
    task, _chat = _run(monkeypatch, ScriptedAgent(outputs))
    assert task["status"] == "error"
    assert f"exceeded {biomni_runner.MAX_STEPS} steps" in task["error"]


def test_run_slot_is_released_after_a_failure(monkeypatch):
    _run(monkeypatch, ScriptedAgent(["<execute>same</execute>"] * 5))
    assert not biomni_runner._run_lock.locked()


@pytest.mark.parametrize("model, rejects", [
    ("anthropic.claude-opus-4-7", True),
    ("anthropic.claude-opus-4-5", False),
    ("anthropic.claude-sonnet-5", True),
    ("anthropic.claude-haiku-4-5", False),
    ("anthropic.claude-fable-5-1", True),
    ("not-a-claude-id", False),
])
def test_rejects_sampling_params(model, rejects):
    from app.providers import rejects_sampling_params

    assert rejects_sampling_params(model) is rejects
