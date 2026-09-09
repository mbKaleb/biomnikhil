"""API layer: JSON REST for submitting tasks, SSE for watching them run."""
from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, request

from ..config import Config
from ..services import biomni_runner
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
    task_id = biomni_runner.submit(prompt)
    return jsonify(task_id=task_id), 202


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
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
