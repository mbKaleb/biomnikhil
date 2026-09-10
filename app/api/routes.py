"""API layer: JSON REST for submitting tasks, SSE for watching them run."""
from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, request
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


@bp.delete("/chats/<chat_id>")
def delete_chat(chat_id: str):
    if not chat_store.delete_chat(chat_id):
        return jsonify(error="unknown chat"), 404
    return jsonify(ok=True)


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


@bp.get("/tasks/<task_id>")
def get_task(task_id: str):
    snap = registry.get(task_id)
    if snap is None:
        return jsonify(error="unknown task"), 404
    return jsonify(snap)


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
                    }
                )
                return
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )