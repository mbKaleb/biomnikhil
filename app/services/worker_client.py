"""The app's side of the agent worker (see worker.py for the protocol and for
why agents run in a process of their own).

A RemoteAgent stands in for an A1 in biomni_runner: _drive_agent hands it the
prompt, and it runs the stage in the worker while this side shows the steps in
the chat, answers the worker's questions for the user, signs its model calls,
and kills it if the user presses Stop. One worker is kept between runs, so A1
builds once; a killed or crashed worker is replaced on the next run.
"""
from __future__ import annotations

import collections
import itertools
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading

from ..config import REPO_ROOT
from .tasks import registry

logger = logging.getLogger(__name__)

# How often a run waiting on the worker looks up to see whether Stop was pressed.
POLL_SECONDS = 0.5
COMMAND = [sys.executable, "-m", "app.services.worker"]

_CLOSED = {"type": "_closed"}


class RemoteError(RuntimeError):
    """An error the worker's agent raised. remote_type is the exception's name
    over there, which _friendly_error matches on."""

    def __init__(self, message: str, remote_type: str) -> None:
        super().__init__(message)
        self.remote_type = remote_type


class WorkerProcess:
    def __init__(self, command: list[str] | None = None) -> None:
        env = dict(os.environ)
        env["BIOMNI_ISOLATED_RUNS"] = "0"  # in the worker, agents run directly
        env["PYTHONUNBUFFERED"] = "1"
        options: dict = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            options["start_new_session"] = True  # so Stop can take its children too
        self.process = subprocess.Popen(
            command or COMMAND, cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, **options,
        )
        self.inbox: queue.Queue = queue.Queue()
        self.recent_errors: collections.deque = collections.deque(maxlen=15)
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_messages, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()

    def _read_messages(self) -> None:
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self.inbox.put(json.loads(line))
            except ValueError:
                logger.warning("worker sent a line that isn't a message: %.200s", line)
        self.inbox.put(_CLOSED)

    def _read_errors(self) -> None:
        """What the worker prints to stderr (its own logging, biomni's output,
        tracebacks) goes to the app's log; the last lines explain a crash."""
        for line in self.process.stderr:
            line = line.rstrip()
            if line:
                self.recent_errors.append(line)
                logger.info("worker: %s", line)

    def send(self, message: dict) -> None:
        line = json.dumps(message) + "\n"
        with self._write_lock:
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass  # the worker is gone; the run finds out when it reads

    def alive(self) -> bool:
        return self.process.poll() is None

    def kill(self) -> None:
        """Ends the worker and anything it started."""
        if self.process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    capture_output=True, check=False,
                )
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            self.process.kill()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("the worker didn't exit after being killed")


_worker: WorkerProcess | None = None
_worker_lock = threading.Lock()
# What the last worker run said about the agent it used, for the Integrity tab.
state = {"built": False, "tools": []}
_run_ids = itertools.count(1)


def current_worker(command: list[str] | None = None) -> WorkerProcess:
    """The running worker, started first if there isn't one."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.alive():
            state["built"] = False
            _worker = WorkerProcess(command)
        return _worker


def stop_worker() -> None:
    global _worker
    with _worker_lock:
        worker, _worker = _worker, None
    state["built"] = False
    if worker is not None:
        worker.kill()


def drop_remote_agent() -> None:
    """Tells the worker to forget its built agent (the data lake was repaired)."""
    with _worker_lock:
        worker = _worker
    state["built"] = False
    if worker is not None and worker.alive():
        worker.send({"type": "reset_agent"})


class RemoteAgent:
    """What biomni_runner uses where it would use an A1 when runs are isolated."""

    llm = None  # the real agent LLM lives in the worker

    def __init__(self, role: str, model: str | None) -> None:
        self.role = role
        self.model = model

    @property
    def module2api(self) -> dict:
        tools = []
        for name in state["tools"]:
            tools.append({"name": name})
        return {"worker": tools}

    def drive(
        self, prompt: str, task_id: str, chat_id: str | None, texts: list[str],
        effort: str | None, stage: str,
    ) -> str:
        worker = current_worker()
        run_id = next(_run_ids)
        worker.send({
            "type": "run", "id": run_id, "role": self.role, "model": self.model,
            "effort": effort, "stage": stage, "prompt": prompt,
        })
        finished = False
        try:
            answer = self._pump(worker, run_id, task_id, chat_id, texts)
            finished = True
            return answer
        except Exception as exc:
            # An error the agent raised inside a healthy worker leaves it fit
            # for the next run; anything else (Stop, a crash) doesn't.
            finished = getattr(exc, "from_worker", False)
            raise
        finally:
            if not finished:
                stop_worker()  # Stop, a crash, or an error here: don't leave it running

    def _pump(self, worker, run_id, task_id, chat_id, texts) -> str:
        from .. import auth
        from . import biomni_runner, chat_store

        while True:
            if registry.is_cancel_requested(task_id):
                raise RuntimeError("stopped by user")
            try:
                message = worker.inbox.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if not worker.alive():
                    raise RuntimeError(_death_message(worker)) from None
                continue
            kind = message.get("type")
            if kind == "_closed":
                raise RuntimeError(_death_message(worker))
            if kind == "creds":
                self._send_credentials(worker, message["req"])
            elif kind == "agent":
                state["built"] = True
                state["tools"] = message.get("tools", [])
                biomni_runner.hidden_tools.clear()
                biomni_runner.hidden_tools.update(message.get("hidden") or {})
            elif message.get("id") != run_id:
                continue  # a leftover from a run that was cut off
            elif kind == "step":
                text = message["text"]
                texts.append(text)
                registry.append_step(task_id, "agent", text)
                if chat_id:  # persist the trace so a page refresh keeps it
                    chat_store.add_message(chat_id, "trace", text)
            elif kind == "ask":
                self._show_question(worker, task_id, chat_id, message["question"])
            elif kind == "done":
                _add_usage(message.get("usage"))
                return message["answer"]
            elif kind == "error":
                _add_usage(message.get("usage"))
                raise _error_from(message, auth, biomni_runner)

    @staticmethod
    def _send_credentials(worker, request: int) -> None:
        from .. import auth

        try:
            credentials = auth.run_credentials()
        except Exception as exc:  # noqa: BLE001 — sign-in problems reach the run as its error
            worker.send({"type": "creds_result", "req": request, "error": str(exc)})
            return
        worker.send({
            "type": "creds_result", "req": request,
            "access_key": credentials.access_key,
            "secret_key": credentials.secret_key,
            "token": credentials.token,
        })

    @staticmethod
    def _show_question(worker, task_id, chat_id, question: str) -> None:
        """Shows the agent's question and passes the answer on, from a thread
        of its own so this side keeps signing the worker's calls meanwhile."""
        from . import chat_store

        registry.ask(task_id, question)
        if chat_id:
            chat_store.add_message(chat_id, "question", question)

        def forward_answer() -> None:
            try:
                answer = registry.wait_for_answer(task_id)
            except RuntimeError:
                return  # stopped: the run is already ending
            if chat_id:
                chat_store.add_message(chat_id, "answer", answer)
            worker.send({"type": "answer", "text": answer})

        threading.Thread(target=forward_answer, daemon=True).start()


def _death_message(worker: WorkerProcess) -> str:
    code = worker.process.poll()
    message = "The agent worker stopped unexpectedly"
    if code is not None:
        message += f" (exit code {code})"
    if worker.recent_errors:
        message += ". Its last output: " + " | ".join(list(worker.recent_errors)[-3:])
    return message


def _error_from(message: dict, auth, biomni_runner) -> Exception:
    text = message.get("message", "")
    if message.get("kind") == "refused":
        error = biomni_runner.ModelRefused(text)
    elif message.get("kind") == "sign_in":
        error = auth.SignInRequired(text)
    else:
        error = RemoteError(text, message.get("error_type") or "RuntimeError")
    error.from_worker = True
    return error


def _add_usage(usage: dict | None) -> None:
    """Adds a stage's token counts from the worker to the run's, which the app
    keeps for the trace line and the Integrity tab."""
    from . import biomni_runner

    for key, amount in ((usage or {}).get("run") or {}).items():
        biomni_runner._run_usage[key] += amount
    for name, counts in ((usage or {}).get("stages") or {}).items():
        mine = biomni_runner._stage_usage.setdefault(
            name, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0},
        )
        for key, amount in counts.items():
            mine[key] += amount
