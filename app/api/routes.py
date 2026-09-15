"""API layer: JSON REST for submitting tasks, SSE for watching them run."""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

from flask import Blueprint, Response, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from ..config import Config
from ..services import biomni_runner, chat_store, datalake
from ..services.tasks import registry

bp = Blueprint("api", __name__)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


@bp.get("/health")
def health():
    return jsonify(
        ok=True,
        provider=Config.PROVIDER,
        model=Config.MODEL or "(provider default)",
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

    task_id = biomni_runner.submit(prompt, chat_id=chat_id)
    return jsonify(task_id=task_id, chat_id=chat_id), 202


@bp.get("/datalake")
def datalake_status():
    return jsonify(datalake.status())


@bp.post("/datalake/download")
def datalake_download():
    started = datalake.start_download()
    return jsonify(started=started, **datalake.status()), 202


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
        return send_from_directory(task_dir, relpath, as_attachment=as_attachment)
    except FileNotFoundError:
        abort(404)


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