"""What the task stream tells the Task tab: the run's step cap, and whether the
user stopped it."""
import json

from app.services import biomni_runner
from app.services.tasks import registry


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
