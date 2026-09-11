"""Server-rendered pages."""

from __future__ import annotations

import logging
import math
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..clones import (
    CloneError,
    clone_dir,
    clone_repository,
    find_existing_clone,
    open_in_editor,
)
from ..db import connect, list_project_names
from ..queries import (
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    SORT_COLUMNS,
    FileFilters,
    count_files_capped,
    fetch_files,
    fetch_queries_for_files,
    fetch_repo,
    fetch_repo_files,
    project_summary,
    set_checked,
    set_repo_checked,
)
from ..terms import extract_terms_from_queries

pages = Blueprint("pages", __name__)
log = logging.getLogger(__name__)

#: Columns that read better descending on first click.
_NUMERIC_SORTS = frozenset({"stars", "score", "checked"})


def _settings():
    return current_app.config["SETTINGS"]


def int_arg(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Read an int query parameter, falling back instead of raising on junk."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.debug("Ignoring non-numeric %s=%r", name, raw)
        return default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _safe_redirect(target: str | None, fallback: str) -> str:
    """Only follow a redirect target that stays on this host."""
    if not target:
        return fallback
    parts = urlsplit(target)
    if parts.netloc and parts.netloc != request.host:
        return fallback
    return target


def _sort_links(filters: FileFilters) -> dict[str, dict]:
    """Per-column sort URLs; clicking the active column flips its direction."""
    links = {}
    base = filters.as_query_args()
    for key in SORT_COLUMNS:
        active = filters.sort == key
        if active:
            direction = "asc" if filters.direction == "desc" else "desc"
        else:
            direction = "desc" if key in _NUMERIC_SORTS else "asc"
        args = {**base, "sort": key, "direction": direction}
        links[key] = {
            "url": url_for("pages.index", **args),
            "active": active,
            "direction": filters.direction if active else None,
        }
    return links


def _page_url(filters: FileFilters, page: int) -> str:
    args = filters.as_query_args()
    if page > 1:
        args["page"] = page
    return url_for("pages.index", **args)


def _pager(filters: FileFilters, page: int, total_pages: int, window: int = 7) -> dict:
    """Pagination URLs that carry every active filter.

    Built here rather than in the template because the old template rebuilt the
    query string by hand and silently dropped the 'show checked' toggle.
    """
    half = window // 2
    start = max(1, min(page - half, total_pages - window + 1))
    end = min(total_pages, start + window - 1)
    return {
        "first": _page_url(filters, 1),
        "prev": _page_url(filters, page - 1) if page > 1 else None,
        "next": _page_url(filters, page + 1) if page < total_pages else None,
        "last": _page_url(filters, total_pages),
        "numbers": [
            {"number": number, "url": _page_url(filters, number), "current": number == page}
            for number in range(start, end + 1)
        ],
    }


@pages.route("/", methods=["GET"])
def index():
    settings = _settings()
    with connect(settings.db_path) as conn:
        projects = list_project_names(conn)
        requested = request.args.get("project")
        project = requested or (projects[0] if projects else None)

        if requested and requested not in projects:
            if projects:
                flash(
                    f"Project {requested!r} does not exist; showing {projects[0]!r}.",
                    "warning",
                )
                project = projects[0]
            else:
                project = None

        if not project:
            return render_template(
                "index.html",
                projects=[],
                project=None,
                files=[],
                filters=FileFilters(project=""),
                summary={"files": 0, "checked": 0, "repos": 0},
                total=0,
                total_capped=False,
                page=1,
                total_pages=1,
                per_page=settings.page_size,
                sort_links={},
                page_args={},
                pager=None,
            )

        filters = FileFilters(
            project=project,
            # Bounded because SQLite rejects integers wider than 64 bits.
            min_stars=int_arg("min_stars", 0, minimum=0, maximum=1_000_000_000),
            show_checked=request.args.get("show_checked") == "on",
            search=request.args.get("q", ""),
            sort=request.args.get("sort", DEFAULT_SORT),
            direction=request.args.get("direction", DEFAULT_DIRECTION),
        ).normalized()

        total, total_capped = count_files_capped(conn, filters, cap=settings.count_cap)
        per_page = max(1, settings.page_size)
        total_pages = max(1, math.ceil(total / per_page))
        page = int_arg("page", 1, minimum=1, maximum=total_pages)
        files = fetch_files(
            conn, filters, limit=per_page, offset=(page - 1) * per_page
        )
        summary = project_summary(conn, project)

    return render_template(
        "index.html",
        projects=projects,
        project=project,
        files=files,
        filters=filters,
        summary=summary,
        total=total,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        total_capped=total_capped,
        sort_links=_sort_links(filters),
        page_args=filters.as_query_args(),
        pager=_pager(filters, page, total_pages),
    )


@pages.route("/repo/<int:repo_id>", methods=["GET"])
def repo_details(repo_id: int):
    settings = _settings()
    with connect(settings.db_path) as conn:
        repo = fetch_repo(conn, repo_id)
        if repo is None:
            abort(404)
        files = fetch_repo_files(conn, repo_id)
        queries = fetch_queries_for_files(conn, [row["id"] for row in files])

    for row in files:
        row["queries"] = queries.get(row["id"], [])
        row["terms"] = extract_terms_from_queries(row["queries"])

    project = request.args.get("project") or repo["project_name"] or "default"
    clone_path = find_existing_clone(settings.projects_dir, project, repo["full_name"])
    try:
        target_path = clone_dir(settings.projects_dir, project, repo["full_name"])
    except CloneError:
        target_path = None

    return render_template(
        "repo_details.html",
        repo=repo,
        files=files,
        project=project,
        checked_count=sum(1 for row in files if row["checked"]),
        clone_path=clone_path,
        target_path=target_path,
        back_args={"project": project} if project else {},
    )


@pages.route("/check/<int:file_id>", methods=["POST"])
def mark_checked(file_id: int):
    """No-JavaScript fallback for the row toggle."""
    checked = request.form.get("checked", "1") != "0"
    with connect(_settings().db_path) as conn:
        changed = set_checked(conn, [file_id], checked)
    if not changed:
        abort(404)
    flash(
        f"File marked as {'checked' if checked else 'unchecked'}.",
        "success",
    )
    return redirect(_safe_redirect(request.referrer, url_for("pages.index")))


@pages.route("/repo/<int:repo_id>/check_all", methods=["POST"])
def check_all_files(repo_id: int):
    checked = request.form.get("checked", "1") != "0"
    with connect(_settings().db_path) as conn:
        if fetch_repo(conn, repo_id) is None:
            abort(404)
        changed = set_repo_checked(conn, repo_id, checked)
    flash(
        f"{changed} file(s) marked as {'checked' if checked else 'unchecked'}.",
        "success",
    )
    return redirect(url_for("pages.repo_details", repo_id=repo_id))


def _repo_for_action(repo_id: int) -> dict:
    with connect(_settings().db_path) as conn:
        repo = fetch_repo(conn, repo_id)
    if repo is None:
        abort(404)
    return repo


@pages.route("/repo/<int:repo_id>/clone", methods=["POST"])
def clone_repo(repo_id: int):
    settings = _settings()
    repo = _repo_for_action(repo_id)
    project = request.form.get("project") or repo["project_name"] or "default"
    try:
        target = clone_dir(settings.projects_dir, project, repo["full_name"])
        clone_repository(repo["html_url"], target, depth=settings.clone_depth)
        flash(f"Cloned {repo['full_name']} into {target}", "success")
        if request.form.get("open"):
            open_in_editor(settings.editor_command, target)
            flash(f"Opening {target} in your editor.", "info")
    except CloneError as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("pages.repo_details", repo_id=repo_id, project=project)
    )


@pages.route("/repo/<int:repo_id>/open", methods=["POST"])
def open_repo(repo_id: int):
    settings = _settings()
    repo = _repo_for_action(repo_id)
    project = request.form.get("project") or repo["project_name"] or "default"
    try:
        target = find_existing_clone(settings.projects_dir, project, repo["full_name"])
        if target is None:
            target = clone_dir(settings.projects_dir, project, repo["full_name"])
            clone_repository(repo["html_url"], target, depth=settings.clone_depth)
            flash(f"Cloned {repo['full_name']} into {target}", "success")
        open_in_editor(settings.editor_command, target)
        flash(f"Opening {target} in your editor.", "info")
    except CloneError as exc:
        flash(str(exc), "error")
    return redirect(
        url_for("pages.repo_details", repo_id=repo_id, project=project)
    )
