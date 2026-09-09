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

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status,
            "steps": list(self.steps),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
        }


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, prompt: str) -> Task:
        task = Task(id=uuid.uuid4().hex[:12], prompt=prompt)
        with self._lock:
            self._tasks[task.id] = task
        return task

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

    def finish(self, task_id: str, result: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "done"
                task.result = result

    def fail(self, task_id: str, error: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.status = "error"
                task.error = error


registry = TaskRegistry()
