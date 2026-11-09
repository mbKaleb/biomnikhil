"""The agent worker: a child process that builds Biomni's A1 and runs agent
stages for the app (see worker_client for the app's side).

Why a process: A1, its code namespace, the REPL's swapped sys.stdout and the
usage counters are all process-global, and code the agent runs can hang in
native code where no thread can be stopped. In its own process a run can be
killed outright, and runs can later go side by side.

The two sides talk in JSON lines, one message per line, over the child's
stdin and stdout. Messages to the worker:

    run          {id, role, model, effort, stage, prompt}
    answer       {text}                  the user's reply to an ask
    creds_result {req, access_key, secret_key, token} or {req, error}
    cancel       {}
    reset_agent  {}                      drop the built agent (data lake repaired)

Messages from the worker:

    ready        {}
    agent        {tools, hidden}         the agent a run uses is built
    step         {id, text}              one agent message
    ask          {id, question}          agent code called ask_user
    creds        {req}                   a model call needs the user's credentials
    done         {id, answer, usage}
    error        {id, kind, error_type, message, usage}

Run it with `python -m app.services.worker` from the repo root.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import sys
import threading

CREDENTIALS_TIMEOUT_SECONDS = 120


def encode(message: dict) -> str:
    """One protocol line. ASCII only, so no stream encoding can break it."""
    return json.dumps(message) + "\n"


class Channel:
    """The worker's end of the pipe. Takes the real stdin and stdout for the
    protocol and points the process's own at a null file and at stderr, so
    nothing the agent's code or biomni prints, or reads, can touch it."""

    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        self._in = os.fdopen(os.dup(0), "r", encoding="utf-8", newline="\n")
        self._out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
        os.dup2(2, 1)

    def send(self, message: dict) -> None:
        line = encode(message)
        with self._write_lock:
            self._out.write(line)
            self._out.flush()

    def lines(self):
        for line in self._in:
            line = line.strip()
            if line:
                yield json.loads(line)


class Worker:
    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        self.jobs: queue.Queue = queue.Queue()
        self.answers: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self._credential_waiters: dict[int, queue.Queue] = {}
        self._request_numbers = itertools.count(1)

    # ---- reading what the app sends ----------------------------------------

    def read_forever(self) -> None:
        """Sorts the app's messages to whoever waits for them. If the pipe
        closes the app is gone, so the worker goes with it."""
        try:
            for message in self.channel.lines():
                self._dispatch(message)
        except Exception:  # noqa: BLE001 — a broken pipe and a bad line both end it
            pass
        os._exit(0)

    def _dispatch(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "run":
            self.cancelled.clear()
            self.jobs.put(message)
        elif kind == "answer":
            self.answers.put(message.get("text", ""))
        elif kind == "creds_result":
            waiter = self._credential_waiters.get(message.get("req"))
            if waiter is not None:
                waiter.put(message)
        elif kind == "cancel":
            self.cancelled.set()
        elif kind == "reset_agent":
            from . import biomni_runner

            biomni_runner.drop_agent()
        elif kind == "shutdown":
            os._exit(0)

    # ---- what the agent's code and model calls ask the app for -------------

    def credentials(self):
        """Stands in for auth.run_credentials: the signed-in user lives in the
        app, which signs the credentials over the pipe, fresh for each model
        call, since a run can outlast them."""
        from botocore.credentials import ReadOnlyCredentials

        from .. import auth

        request = next(self._request_numbers)
        waiter: queue.Queue = queue.Queue(maxsize=1)
        self._credential_waiters[request] = waiter
        try:
            self.channel.send({"type": "creds", "req": request})
            try:
                reply = waiter.get(timeout=CREDENTIALS_TIMEOUT_SECONDS)
            except queue.Empty:
                raise RuntimeError("the app didn't answer a credentials request") from None
        finally:
            self._credential_waiters.pop(request, None)
        if "error" in reply:
            raise auth.SignInRequired(reply["error"])
        return ReadOnlyCredentials(reply["access_key"], reply["secret_key"], reply["token"])

    def ask_function(self, run_id):
        """The ask the run's ask_user() calls: the question goes to the app,
        which shows it, and the reply comes back as an "answer" message."""
        def ask(question: str) -> str:
            self.channel.send({"type": "ask", "id": run_id, "question": question})
            while True:
                try:
                    return self.answers.get(timeout=1)
                except queue.Empty:
                    if self.cancelled.is_set():
                        raise RuntimeError("stopped by user") from None

        return ask

    # ---- running stages -----------------------------------------------------

    def serve(self) -> None:
        from .. import auth

        auth.run_credentials = self.credentials
        threading.Thread(target=self.read_forever, daemon=True).start()
        self.channel.send({"type": "ready"})
        while True:
            job = self.jobs.get()
            self.run(job)

    def run(self, job: dict) -> None:
        from . import biomni_runner

        run_id = job["id"]

        def usage() -> dict:
            stages = {}
            for name, counts in biomni_runner._stage_usage.items():
                stages[name] = dict(counts)
            return {"run": dict(biomni_runner._run_usage), "stages": stages}

        try:
            biomni_runner.reset_usage()
            biomni_runner.set_model(job.get("model"))
            agent = biomni_runner._get_agent()
            if job["role"] != "main":
                agent = biomni_runner._get_stage_agent(job["role"], job.get("model"))
            tools = []
            for module_tools in (agent.module2api or {}).values():
                for tool in module_tools:
                    tools.append(tool["name"])
            self.channel.send({
                "type": "agent", "tools": sorted(tools),
                "hidden": dict(biomni_runner.hidden_tools),
            })

            def emit(text: str) -> None:
                self.channel.send({"type": "step", "id": run_id, "text": text})

            answer = biomni_runner._drive(
                agent, job["prompt"], [], job.get("effort"), job.get("stage") or job["role"],
                emit, self.cancelled.is_set, biomni_runner._wait_on_user(self.ask_function(run_id)),
            )
        except BaseException as exc:  # noqa: BLE001 — every failure goes back to the app
            self.channel.send({
                "type": "error", "id": run_id, "kind": error_kind(exc),
                "error_type": type(exc).__name__, "message": str(exc), "usage": usage(),
            })
            return
        self.channel.send({"type": "done", "id": run_id, "answer": answer, "usage": usage()})


def error_kind(exc: BaseException) -> str:
    """How the app should re-raise an error from the worker: the few kinds it
    treats specially (see biomni_runner._friendly_error)."""
    from .. import auth
    from . import biomni_runner

    if isinstance(exc, biomni_runner.ModelRefused):
        return "refused"
    if isinstance(exc, auth.SignInRequired):
        return "sign_in"
    return "other"


def main() -> None:
    Worker(Channel()).serve()


if __name__ == "__main__":
    main()
