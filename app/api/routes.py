"""API layer: JSON REST for submitting tasks, SSE for watching them run."""
from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile

from flask import Blueprint, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from .. import auth
from ..config import Config
from ..services import biomni_runner, chat_store, datalake
from ..services.tasks import registry

bp = Blueprint("api", __name__)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@bp.post("/client-error")
def client_error():
    """Uncaught errors from the page (app.js reports them), so problems in the
    desktop app window, which has no console, end up in the server log."""
    body = request.get_json(silent=True) or {}
    logging.getLogger("biomnikhil.page").warning(
        "page error at %s: %s", str(body.get("where", "?"))[:200], str(body.get("message", ""))[:2000],
    )
    return "", 204


@bp.get("/health")
def health():
    return jsonify(
        ok=True,
        model=biomni_runner.get_model() or "(default)",
        data_path=str(Config.DATA_PATH),
    )


@bp.post("/chat")
def chat():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify(error="prompt is required"), 400

    # Optional list of previously uploaded filenames to attach to the task.
    attached = []
    for name in body.get("files") or []:
        safe = secure_filename(str(name))
        path = Config.UPLOAD_PATH / safe
        if safe and path.is_file():
            attached.append(str(path))
    display_prompt = prompt
    if attached:
        listing = "\n".join(f"- {p}" for p in attached)
        prompt = f"{prompt}\n\nThe user attached these files (local paths):\n{listing}"

    # Attach the run to a chat: use the given one, or start a new chat titled
    # from the prompt so history always has a home.
    chat_id = (body.get("chat_id") or "").strip() or None
    if chat_id and chat_store.get_chat(chat_id) is None:
        return jsonify(error="unknown chat"), 404
    if chat_id is None:
        title = display_prompt[:60] + ("…" if len(display_prompt) > 60 else "")
        chat_id = chat_store.create_chat(title)["id"]
    chat_store.add_message(chat_id, "user", display_prompt)

    task_id = biomni_runner.submit(prompt, chat_id=chat_id, user=auth.current_user())
    return jsonify(task_id=task_id, chat_id=chat_id), 202


def models_payload() -> dict:
    """Models the UI selector offers, plus the one currently in effect; the
    index page embeds it so the selects render with the page."""
    from ..providers import default_model, group_models, list_models, refresh_models

    user = auth.current_user()
    if user is not None:
        refresh_models(user.credentials)
    info = list_models()
    # What the next run will actually use — same fallback as resolve().
    current = biomni_runner.get_model() or default_model()
    # Always make the active model selectable, even if the list doesn't
    # include it (e.g. a model pinned with BIOMNI_MODEL).
    if current and current not in {m["id"] for m in info["models"]}:
        info["models"] = [{"id": current}, *info["models"]]
    # Family + version, for the UI's two selects.
    info["families"] = group_models(info["models"])
    return {"current": current, **info}


@bp.get("/models")
def list_models_route():
    """Same payload as embedded in the page."""
    return jsonify(models_payload())


@bp.post("/model")
def set_model_route():
    body = request.get_json(silent=True) or {}
    model = (body.get("model") or "").strip()
    if not model:
        return jsonify(error="model is required"), 400
    current = biomni_runner.set_model(model)
    return jsonify(current=current)


@bp.get("/datalake")
def datalake_status():
    return jsonify(datalake.status())


@bp.post("/datalake/download")
def datalake_download():
    started = datalake.start_download()
    return jsonify(started=started, **datalake.status()), 202


def _check(label: str, status: str, detail: str = "") -> dict:
    """One row on the Integrity tab. status: pass | warn | fail | pending
    (actively in progress — spinner, fast polling) | info (a normal resting
    state that isn't a pass/fail, e.g. "builds on the first run")."""
    return {"label": label, "status": status, "detail": detail}


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n} B"


def _datalake_card() -> dict:
    st = datalake.status()
    state = st["state"]
    # expected_bytes is only a rough estimate for the progress bar, so it's
    # shown while downloading but not once the manifest says it's complete.
    size = f"{_fmt_bytes(st['bytes'])} of ~{_fmt_bytes(st['expected_bytes'])}, {st['files']:,} files"
    try:
        from biomni.env_desc import data_lake_dict

        manifest = f"all {len(data_lake_dict)} manifest files present"
    except Exception:  # noqa: BLE001 — biomni not importable
        manifest = "complete"
    done = f"{manifest} — {_fmt_bytes(st['bytes'])}, {st['files']:,} files"
    download = {
        "ready": _check("Downloaded", "pass", done),
        "invalid": _check("Downloaded", "pass", done),
        "downloading": _check("Downloaded", "pending", f"downloading — {st['percent']}% ({size})"),
        "partial": _check("Downloaded", "warn", f"partial — {size}"),
        "missing": _check("Downloaded", "fail", "not downloaded yet"),
        "error": _check("Downloaded", "fail", st["error"] or "download failed"),
    }[state]
    if state in ("ready", "invalid"):
        if not st["validated"]:
            verify = _check("Files verified", "pending", "checking parquet footers, json/pkl parse, empty files…")
        elif st["valid"]:
            verify = _check("Files verified", "pass", "every file opened cleanly")
        else:
            n = len(st["invalid_files"])
            verify = _check("Files verified", "fail", f"{n} file(s) failed — Repair re-downloads them")
    else:
        verify = _check("Files verified", "pending", "runs once the download is complete")
    return {
        "checks": [download, verify],
        "state": state,
        "path": st["path"],
        "invalid_files": st["invalid_files"][:50],
        "validation_error": st["validation_error"],
        "can_download": state in ("missing", "partial", "error", "invalid"),
    }


def _agent_card() -> dict:
    from ..providers import default_model, list_models

    model = biomni_runner.get_model() or default_model()
    user = auth.current_user()
    listed = list_models()
    checks = [
        _check("Credentials", "pass", f"Cognito credentials for {user.email}")
        if user else _check("Credentials", "fail", "not signed in to Cognito"),
        _check("Model", "pass", f"{model} on Bedrock")
        if model in {m["id"] for m in listed["models"]} else
        _check("Model", "warn", f"{model} isn't in Bedrock's model list — runs may fail"),
        _check("Model list", "pass", f"{len(listed['models'])} Claude models listed by Bedrock")
        if listed["live"] else
        _check("Model list", "warn", "built-in list — Bedrock's model list couldn't be read"
               + (f" ({listed['error']})" if listed["error"] else " yet")),
    ]

    agent = biomni_runner.agent_status()
    if agent["built"]:
        checks.append(_check("Agent", "pass", "loaded"))
    elif agent["building"]:
        checks.append(_check("Agent", "pending", "building — loading tools and the data lake index…"))
    else:
        checks.append(_check("Agent", "info", "not loaded yet — builds on the first run"))

    active = registry.active()
    running = [t for t in active if t["status"] == "running"]
    queued = [t for t in active if t["status"] == "queued"]
    detail = ("idle" if not active else
              f"{len(running)} running, {len(queued)} queued (runs execute one at a time)")
    checks.append(_check("Runs", "pass", detail))
    return {"checks": checks, "model": model, "active": active}


def _harness_card() -> dict:
    from ..services import hints

    items = hints.harness_inventory()
    broken = [i for i in items if i["error"]]
    recipes = sum(1 for i in items if i["kind"] == "recipes")
    probes = sum(1 for i in items if i["kind"] == "capabilities")
    checks = [
        _check("Saved knowledge", "pass",
               f"{recipes} verified recipe(s), {probes} capability probe(s)" if items
               else "nothing saved yet — recipes are saved after a run produces real output"),
        _check("Files parse", "fail" if broken else "pass",
               f"{len(broken)} broken: " + ", ".join(i["name"] for i in broken) if broken
               else "every recipe is valid Python, every probe valid JSON"),
    ]
    return {"checks": checks, "items": items, "path": str(Config.HARNESS_PATH)}


@bp.get("/integrity")
def integrity():
    """Pass/fail health of the data lake, the agent, and its harness (saved
    recipes + capability probes) — the Integrity tab. Never blocks on the
    network or on a running build/run."""
    return jsonify(datalake=_datalake_card(), agent=_agent_card(), harness=_harness_card())


@bp.get("/harness/<kind>/<name>")
def harness_file(kind: str, name: str):
    from ..services import hints

    text = hints.read_harness_file(kind, name)
    if text is None:
        abort(404)
    return Response(text, mimetype="text/plain")


@bp.get("/chats")
def list_chats():
    return jsonify(chats=chat_store.list_chats())


@bp.post("/chats")
def create_chat():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "New chat").strip() or "New chat"
    return jsonify(chat=chat_store.create_chat(title)), 201


@bp.get("/chats/<chat_id>")
def get_chat(chat_id: str):
    chat = chat_store.get_chat(chat_id)
    if chat is None:
        return jsonify(error="unknown chat"), 404
    return jsonify(chat)


@bp.patch("/chats/<chat_id>")
def rename_chat(chat_id: str):
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    if not title:
        return jsonify(error="title is required"), 400
    if not chat_store.rename_chat(chat_id, title):
        return jsonify(error="unknown chat"), 404
    return jsonify(ok=True)


@bp.get("/chats/<chat_id>/active")
def chat_active_task(chat_id: str):
    return jsonify(task_id=registry.active_for_chat(chat_id))


@bp.delete("/chats/<chat_id>")
def delete_chat(chat_id: str):
    if not chat_store.delete_chat(chat_id):
        return jsonify(error="unknown chat"), 404
    return jsonify(ok=True)


@bp.post("/chats/<chat_id>/rewind")
def rewind_chat(chat_id: str):
    """Destructively delete a user message and everything after it, so the
    caller can resubmit that prompt as a fresh run. Refuses while a task is
    still active for this chat — rewinding out from under a live run would
    orphan it (it'd keep writing trace/result messages nothing points at)."""
    body = request.get_json(silent=True) or {}
    try:
        message_id = int(body.get("message_id"))
    except (TypeError, ValueError):
        return jsonify(error="message_id is required"), 400

    if chat_store.get_chat(chat_id) is None:
        return jsonify(error="unknown chat"), 404
    if registry.active_for_chat(chat_id):
        return jsonify(error="a task is still running in this chat — stop it first"), 409

    deleted = chat_store.rewind_chat(chat_id, message_id)
    if deleted == 0:
        return jsonify(error="unknown message"), 404
    return jsonify(ok=True, deleted=deleted)


@bp.get("/files")
def list_files():
    Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (
            {"name": p.name, "size": p.stat().st_size}
            for p in Config.UPLOAD_PATH.iterdir()
            if p.is_file()
        ),
        key=lambda f: f["name"].lower(),
    )
    return jsonify(files=files)


@bp.post("/files")
def upload_files():
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify(error="no files provided"), 400
    Config.UPLOAD_PATH.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in uploads:
        name = secure_filename(f.filename or "")
        if not name:
            continue
        f.save(Config.UPLOAD_PATH / name)
        saved.append(name)
    if not saved:
        return jsonify(error="no valid filenames"), 400
    return jsonify(saved=saved), 201


@bp.delete("/files/<name>")
def delete_file(name: str):
    safe = secure_filename(name)
    path = Config.UPLOAD_PATH / safe
    if not safe or not path.is_file():
        return jsonify(error="unknown file"), 404
    path.unlink()
    return jsonify(ok=True)


@bp.get("/tasks/<task_id>")
def get_task(task_id: str):
    snap = registry.get(task_id)
    if snap is None:
        return jsonify(error="unknown task"), 404
    return jsonify(snap)


@bp.post("/tasks/<task_id>/cancel")
def cancel_task(task_id: str):
    if not registry.request_cancel(task_id):
        return jsonify(error="unknown or finished task"), 404
    return jsonify(ok=True), 202


_SCRIPTABLE_EXTS = {"html", "htm", "xhtml", "svg", "svgz", "xml", "xsl", "js", "mjs"}


@bp.get("/tasks/<task_id>/outputs/<path:relpath>")
def get_output_file(task_id: str, relpath: str):
    """Serve a file a run wrote to its output folder. Reads straight from
    disk (not the in-memory task registry) so files a run produced are
    still reachable after a server restart, as long as chat history —
    which is what actually points the UI at them — survives in SQLite.

    ?dl=1 forces a download; otherwise the browser renders it inline when
    it can (images, PDFs, text/CSV), which is what "view" wants.
    """
    task_id = secure_filename(task_id)
    task_dir = Config.OUTPUT_PATH / task_id
    if not task_dir.is_dir():
        abort(404)
    # send_from_directory safe-joins relpath against task_dir, rejecting
    # any ../ escape attempt — this is the actual traversal guard.
    as_attachment = request.args.get("dl") == "1"
    try:
        response = send_from_directory(task_dir, relpath, as_attachment=as_attachment)
    except FileNotFoundError:
        abort(404)
    # Agent-written files are untrusted: an .html/.svg served inline on this
    # origin could script /api/* as the app. CSP sandbox gives such a
    # document an opaque origin (scripts still run, for interactive plots,
    # but can't act as the app). Only for scriptable types — Chrome refuses
    # to render PDFs under a sandbox CSP. nosniff stops any other file being
    # sniffed into HTML.
    if relpath.lower().rsplit(".", 1)[-1] in _SCRIPTABLE_EXTS:
        response.headers["Content-Security-Policy"] = "sandbox allow-scripts"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.post("/outputs/zip")
def zip_output_files():
    """Bundle a caller-chosen set of output files into one zip. Takes
    {task_id, path} pairs rather than a chat id — the "outputs" tray
    aggregates files across every run in a chat, potentially several
    different task_ids, and the browser already has that list in memory
    (built from chat.messages), so there's no need to re-derive it here.
    """
    body = request.get_json(silent=True) or {}
    entries = body.get("files") or []
    if not entries:
        return jsonify(error="no files given"), 400

    buf = io.BytesIO()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = set()
        for entry in entries:
            task_id = secure_filename(str(entry.get("task_id") or ""))
            rel = str(entry.get("path") or "")
            if not task_id or not rel:
                continue
            task_dir = Config.OUTPUT_PATH / task_id
            # Resolve-and-check is the same traversal guard send_from_directory
            # uses internally, done by hand since we're reading raw bytes here.
            candidate = (task_dir / rel).resolve()
            try:
                candidate.relative_to(task_dir.resolve())
            except ValueError:
                continue  # rel escaped its task dir — skip, don't error the whole batch
            if not candidate.is_file():
                continue
            arcname = f"{task_id}/{rel}"
            if arcname in seen:
                continue
            seen.add(arcname)
            zf.write(candidate, arcname=arcname)
            added += 1

    if added == 0:
        return jsonify(error="none of the requested files exist"), 404

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="outputs.zip",
    )


@bp.get("/tasks/<task_id>/stream")
def stream_task(task_id: str):
    """Server-sent events: pushes new steps as they appear, then the final
    status. Polls the registry — simple and works fine under waitress."""

    def generate():
        seen = 0
        while True:
            snap = registry.get(task_id)
            if snap is None:
                yield _sse({"error": "unknown task"})
                return
            steps = snap["steps"]
            for step in steps[seen:]:
                yield _sse({"step": step})
            seen = len(steps)
            if snap["status"] in ("done", "error"):
                yield _sse(
                    {
                        "status": snap["status"],
                        "result": snap.get("result"),
                        "error": snap.get("error"),
                        "files": snap.get("files") or [],
                    }
                )
                return
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )