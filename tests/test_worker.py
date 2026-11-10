"""The agent worker, for real: a subprocess running a stand-in agent, driven
through worker_client the way biomni_runner drives it. Covers the pipe (steps,
questions, credentials, usage) and what happens when a run is stopped or the
worker dies."""
import sys
import textwrap
import threading
import time

import pytest

from app import auth
from app.config import REPO_ROOT
from app.services import biomni_runner, chat_store, worker_client
from app.services.tasks import registry

WORKER_SCRIPT = """
import os, sys, time
sys.path.insert(0, {root!r})
from app.services import biomni_runner, worker


class Agent:
    llm = object()
    module2api = {{"module": [{{"name": "tool_a"}}]}}

    def go_stream(self, prompt):
        biomni_runner._record_usage({{"input_tokens": 100, "output_tokens": 10}})
        if prompt.startswith("ask"):
            from biomni.tool import support_tools
            reply = support_tools._persistent_namespace["ask_user"]("which gene?")
            yield {{"output": "<solution>the gene is " + reply + "</solution>"}}
        elif prompt.startswith("creds"):
            from app import auth
            key = auth.run_credentials().access_key
            yield {{"output": "<solution>key " + key + "</solution>"}}
        elif prompt.startswith("hang"):
            yield {{"output": "<execute>sleep forever</execute>"}}
            time.sleep(120)
        elif prompt.startswith("note"):
            biomni_runner._run_watch["notice"]("Still waiting for the model's reply (30s so far).")
            yield {{"output": "<solution>done</solution>"}}
        elif prompt.startswith("crash"):
            yield {{"output": "<execute>about to die</execute>"}}
            os._exit(3)
        elif prompt.startswith("fail"):
            raise RuntimeError("boom")
        else:
            yield {{"output": "<execute>print(1)</execute>"}}
            yield {{"output": "<solution>done " + str(os.getpid()) + "</solution>"}}


biomni_runner._get_agent = lambda: Agent()
biomni_runner._get_stage_agent = lambda role, model: Agent()
worker.main()
"""


class SignedInUser:
    signed_out = False

    class credentials:
        @staticmethod
        def get_frozen_credentials():
            from botocore.credentials import ReadOnlyCredentials

            return ReadOnlyCredentials("AKIDWORKERTEST", "secret", "token")


@pytest.fixture
def worker_command(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(WORKER_SCRIPT.format(root=str(REPO_ROOT)))
    monkeypatch.setattr(worker_client, "COMMAND", [sys.executable, str(script)])
    biomni_runner.reset_usage()
    yield
    worker_client.stop_worker()
    auth.set_run_user(None)


def _drive(prompt, chat_id=None, role="main", seen=None):
    """Runs one stage through the worker; returns (task id, answer or error).
    `seen` gets the task id as soon as it exists, for tests that act mid-run."""
    task = registry.create(prompt, chat_id=chat_id)
    if seen is not None:
        seen.append(task.id)
    registry.mark_running(task.id)
    agent = worker_client.RemoteAgent(role, None)
    texts = []
    try:
        return task.id, biomni_runner._drive_agent(agent, prompt, task.id, chat_id, texts, "high")
    except Exception as exc:  # noqa: BLE001
        return task.id, exc


def _wait_for(seen, condition, seconds=30):
    """The id of the task `seen` holds once `condition(id)` is true for it."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if seen and condition(seen[0]):
            return seen[0]
        time.sleep(0.1)
    return None


def test_steps_answer_and_usage_come_back(worker_command):
    chat_id = chat_store.create_chat("worker")["id"]
    task_id, answer = _drive("plain", chat_id)
    assert answer.startswith("done ")
    steps = registry.get(task_id)["steps"]
    assert [s["text"] for s in steps if s["kind"] == "agent"][0] == "<execute>print(1)</execute>"
    traces = [m["content"] for m in chat_store.get_chat(chat_id)["messages"] if m["role"] == "trace"]
    assert "<execute>print(1)</execute>" in traces
    assert biomni_runner._run_usage["calls"] == 1
    assert biomni_runner._run_usage["input_tokens"] == 100
    assert biomni_runner._stage_usage["main"]["output_tokens"] == 10
    assert worker_client.state == {"built": True, "tools": ["tool_a"]}


def test_a_note_from_the_worker_lands_in_the_trace(worker_command):
    chat_id = chat_store.create_chat("worker")["id"]
    task_id, answer = _drive("note", chat_id)
    assert answer == "done"
    line = "Still waiting for the model's reply (30s so far)."
    system_lines = [s["text"] for s in registry.get(task_id)["steps"] if s["kind"] == "system"]
    assert system_lines == [line]
    traces = [m["content"] for m in chat_store.get_chat(chat_id)["messages"] if m["role"] == "trace"]
    assert line in traces


def test_the_worker_is_reused_between_runs(worker_command):
    _task, first = _drive("plain")
    _task, second = _drive("plain")
    assert first == second  # the answer carries the worker's process id


def test_a_question_reaches_the_user_and_the_answer_reaches_the_agent(worker_command):
    chat_id = chat_store.create_chat("worker")["id"]
    result = {}
    seen = []

    def run():
        result["task_id"], result["answer"] = _drive("ask", chat_id, seen=seen)

    thread = threading.Thread(target=run)
    thread.start()
    task_id = _wait_for(seen, lambda id: registry.get(id)["question"])
    assert task_id, "the question never reached the app"
    assert registry.get(task_id)["question"] == "which gene?"
    assert registry.answer(task_id, "TP53")
    thread.join(30)
    assert result["answer"] == "the gene is TP53"
    roles = [m["role"] for m in chat_store.get_chat(chat_id)["messages"]]
    assert "question" in roles and "answer" in roles


def test_model_calls_are_signed_with_the_signed_in_users_credentials(worker_command):
    auth.set_run_user(SignedInUser())
    _task, answer = _drive("creds")
    assert answer == "key AKIDWORKERTEST"


def test_a_signed_out_user_fails_the_run_with_the_sign_in_message(worker_command):
    auth.set_run_user(None)
    _task, error = _drive("creds")
    assert isinstance(error, auth.SignInRequired)


def test_an_agent_error_comes_back_and_the_worker_survives(worker_command):
    _task, before = _drive("plain")
    _task, error = _drive("fail")
    assert isinstance(error, worker_client.RemoteError)
    assert str(error) == "boom"
    assert error.remote_type == "RuntimeError"
    _task, after = _drive("plain")
    assert after == before  # the same process answered both


def test_stop_kills_a_run_that_cannot_be_interrupted(worker_command):
    outcome = {}
    seen = []

    def run():
        outcome["task_id"], outcome["result"] = _drive("hang", seen=seen)

    thread = threading.Thread(target=run)
    thread.start()
    started = _wait_for(seen, lambda id: registry.get(id)["steps"])
    assert started, "the run never started"
    worker = worker_client._worker
    registry.request_cancel(started)
    thread.join(15)
    assert not thread.is_alive(), "Stop didn't end the run"
    assert "stopped by user" in str(outcome["result"])
    assert not worker.alive()
    assert worker_client._worker is None


def test_a_crashed_worker_is_reported_and_replaced(worker_command):
    _task, error = _drive("crash")
    assert "stopped unexpectedly" in str(error)
    assert "exit code 3" in str(error)
    _task, answer = _drive("plain")
    assert answer.startswith("done ")


def test_flag_off_keeps_agents_in_the_app(monkeypatch):
    monkeypatch.setattr(biomni_runner.Config, "ISOLATED_RUNS", False)
    assert biomni_runner.agent_status()["built"] is False
    monkeypatch.setattr(biomni_runner.Config, "ISOLATED_RUNS", True)
    agent = biomni_runner._get_agent()
    assert isinstance(agent, worker_client.RemoteAgent)
    assert isinstance(biomni_runner._get_stage_agent("review", "m"), worker_client.RemoteAgent)
