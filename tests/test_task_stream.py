"""What the task stream tells the Task tab: the run's step cap, and whether the
user stopped it."""
import json

import pytest

from app.services import biomni_runner
from app.services.tasks import registry


@pytest.fixture
def running_task():
    """A task in the running state, finished afterwards: a task left running
    blocks other tests, such as Settings maintenance."""
    task = registry.create("a prompt")
    registry.mark_running(task.id)
    yield task
    registry.finish(task.id, "")


def _stream_events(client, task_id):
    response = client.get(f"/api/tasks/{task_id}/stream")
    events = []
    for line in response.get_data(as_text=True).splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_stream_sends_the_step_cap_once(client, signed_in):
    task = registry.create("a prompt")
    registry.set_max_steps(task.id, biomni_runner.MAX_STEPS)
    registry.finish(task.id, "the answer")
    events = _stream_events(client, task.id)
    cap_events = []
    for event in events:
        if "max_steps" in event:
            cap_events.append(event)
    assert cap_events == [{"max_steps": biomni_runner.MAX_STEPS}]


def test_stream_marks_a_stopped_run_as_cancelled(client, signed_in):
    task = registry.create("a prompt")
    assert registry.request_cancel(task.id)
    registry.fail(task.id, "Stopped by user.")
    final_event = _stream_events(client, task.id)[-1]
    assert final_event["status"] == "error"
    assert final_event["cancelled"] is True


def test_stream_does_not_mark_an_ordinary_failure_as_cancelled(client, signed_in):
    task = registry.create("a prompt")
    registry.fail(task.id, "it broke")
    final_event = _stream_events(client, task.id)[-1]
    assert final_event["cancelled"] is False


def test_a_reply_reaches_the_waiting_run(client, signed_in, token, running_task):
    task = running_task
    registry.ask(task.id, "Which gene?")
    assert registry.get(task.id)["question"] == "Which gene?"

    response = client.post(f"/api/tasks/{task.id}/answer", json={"text": " TP53 "}, headers=token)
    assert response.status_code == 200
    assert registry.wait_for_answer(task.id) == "TP53"
    assert registry.get(task.id)["question"] is None

    kinds = []
    for step in registry.get(task.id)["steps"]:
        kinds.append(step["kind"])
    assert kinds == ["question", "answer"]


def test_a_reply_is_refused_when_nothing_was_asked(client, signed_in, token, running_task):
    task = running_task
    response = client.post(f"/api/tasks/{task.id}/answer", json={"text": "TP53"}, headers=token)
    assert response.status_code == 409
    registry.ask(task.id, "Which gene?")
    response = client.post(f"/api/tasks/{task.id}/answer", json={"text": "  "}, headers=token)
    assert response.status_code == 400


def test_stop_ends_the_wait_for_a_reply(running_task):
    import threading

    task = running_task
    registry.ask(task.id, "Which gene?")
    threading.Timer(0.2, registry.request_cancel, args=[task.id]).start()
    with pytest.raises(RuntimeError, match="stopped by user"):
        registry.wait_for_answer(task.id)
    assert registry.get(task.id)["question"] is None
