"""Thread-safe in-memory task registry. Good enough for a single-machine
lab deployment; swap for SQLite later if tasks need to survive restarts.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    prompt: str
    status: str = "queued"  # queued | running | done | error
    steps: list[dict] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    cancel_requested: bool = False
    chat_id: str | None = None
    files: list[dict] = field(default_factory=list)  # output files this run produced

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "steps": list(self.steps),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "files": list(self.files),
        }


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, prompt: str, chat_id: str | None = None) -> Task:
        task = Task(id=uuid.uuid4().hex[:12], prompt=prompt, chat_id=chat_id)
        with self._lock:
            self._tasks[task.id] = task
        return task

    def active_for_chat(self, chat_id: str) -> str | None:
        """Id of the queued/running task attached to this chat, if any —
        lets a reloaded page re-attach to an in-flight run."""
        with self._lock:
            for task in reversed(list(self._tasks.values())):
                if task.chat_id == chat_id and task.status in ("queued", "running"):
                    return task.id
        return None

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.snapshot() if task else None

    def append_step(self, task_id: str, kind: str, text: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.steps.append({"t": time.time(), "kind": kind, "text": text})

    def mark_running(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "running"

    def finish(self, task_id: str, result: str, files: list[dict] | None = None) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "done"
                task.result = result
                task.files = files or []

    def request_cancel(self, task_id: str) -> bool:
        """Flag a queued/running task for cancellation; the worker checks the
        flag between steps. Returns False for unknown or finished tasks."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status in ("done", "error"):
                return False
            task.cancel_requested = True
            return True

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.cancel_requested)

    def fail(self, task_id: str, error: str, files: list[dict] | None = None) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "error"
                task.error = error
                task.files = files or []


registry = TaskRegistry()
