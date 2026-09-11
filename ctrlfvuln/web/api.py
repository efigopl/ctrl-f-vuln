"""JSON endpoints backing the no-reload triage interactions."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from ..db import connect
from ..github import GitHubClient, GitHubError
from ..queries import (
    fetch_file,
    fetch_queries_for_files,
    fetch_repo,
    project_summary,
    set_checked,
    set_repo_checked,
)
from ..terms import extract_terms_from_queries

api = Blueprint("api", __name__)
log = logging.getLogger(__name__)

MAX_BULK_IDS = 1000


def _settings():
    return current_app.config["SETTINGS"]


def _wants_checked(payload: dict) -> bool:
    value = payload.get("checked", True)
    if isinstance(value, str):
        return value.lower() not in ("0", "false", "no", "off")
    return bool(value)


def _summary_for(project: str | None) -> dict | None:
    if not project:
        return None
    with connect(_settings().db_path) as conn:
        return project_summary(conn, project)


@api.route("/files/<int:file_id>/checked", methods=["POST"])
def set_file_checked(file_id: int):
    payload = request.get_json(silent=True) or {}
    checked = _wants_checked(payload)
    with connect(_settings().db_path) as conn:
        if fetch_file(conn, file_id) is None:
            return jsonify(error="File not found"), 404
        set_checked(conn, [file_id], checked)
    return jsonify(
        id=file_id,
        checked=checked,
        summary=_summary_for(payload.get("project")),
    )


@api.route("/files/checked", methods=["POST"])
def set_files_checked():
    """Bulk toggle, used by the table's selection actions."""
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("ids") or []
    if not isinstance(raw_ids, list):
        return jsonify(error="'ids' must be a list"), 400
    if len(raw_ids) > MAX_BULK_IDS:
        return jsonify(error=f"Too many ids (max {MAX_BULK_IDS})"), 400

    ids: list[int] = []
    for value in raw_ids:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            return jsonify(error=f"Invalid file id: {value!r}"), 400

    checked = _wants_checked(payload)
    with connect(_settings().db_path) as conn:
        changed = set_checked(conn, ids, checked)
    return jsonify(
        changed=changed,
        ids=ids,
        checked=checked,
        summary=_summary_for(payload.get("project")),
    )


@api.route("/repos/<int:repo_id>/checked", methods=["POST"])
def set_repository_checked(repo_id: int):
    payload = request.get_json(silent=True) or {}
    checked = _wants_checked(payload)
    with connect(_settings().db_path) as conn:
        if fetch_repo(conn, repo_id) is None:
            return jsonify(error="Repository not found"), 404
        changed = set_repo_checked(conn, repo_id, checked)
    return jsonify(
        repo_id=repo_id,
        changed=changed,
        checked=checked,
        summary=_summary_for(payload.get("project")),
    )


@api.route("/files/<int:file_id>/preview", methods=["GET"])
def preview_file(file_id: int):
    """Return a file's contents plus the literals worth highlighting in it."""
    settings = _settings()
    with connect(settings.db_path) as conn:
        record = fetch_file(conn, file_id)
        if record is None:
            return jsonify(error="File not found"), 404
        queries = fetch_queries_for_files(conn, [file_id]).get(file_id, [])

    terms = extract_terms_from_queries(queries)
    client = GitHubClient(
        settings.github_token,
        timeout=settings.http_timeout,
        rate_limit_delay=0,  # Fail fast; a viewer request must not block for a minute.
    )

    try:
        if record.get("git_url"):
            content, truncated = client.get_blob_text(
                record["git_url"], max_bytes=settings.preview_max_bytes
            )
        elif record.get("url"):
            content, truncated = client.get_raw_text(
                record["url"], max_bytes=settings.preview_max_bytes
            )
        else:
            return jsonify(error="This row has no content URL to fetch."), 422
    except GitHubError as exc:
        status = 404 if exc.status == 404 else 502
        message = str(exc)
        if exc.status in (401, 403):
            message = (
                "GitHub refused the request. Set GITHUB_TOKEN in .env to raise the "
                "preview rate limit."
            )
        log.info("Preview failed for file %d: %s", file_id, exc)
        return jsonify(error=message), status

    return jsonify(
        id=file_id,
        name=record["name"],
        path=record["path"],
        repo_id=record["repo_id"],
        repo_name=record["repo_name"],
        file_url=record["file_url"],
        checked=bool(record["checked"]),
        content=content,
        truncated=truncated,
        terms=terms,
        queries=queries,
    )
