"""Command-line interface for collecting and inspecting search results."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

from . import logging_setup
from .config import ENV_TEMPLATE, Settings
from .config import settings as default_settings
from .db import (
    STARS_UNAVAILABLE,
    STARS_UNKNOWN,
    connect,
    initialize_database,
    list_project_names,
    migrate,
    project_id_for,
)
from .github import GitHubClient, GitHubError
from .search import run_search

log = logging.getLogger(__name__)

COMMANDS = ("search", "list", "resume", "config", "augment", "stats", "migrate")
#: Commands that ignore the project argument entirely.
PROJECTLESS = frozenset({"config", "migrate"})
AUGMENT_BATCH = 200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search_github.py",
        description=(
            "Search GitHub for code samples to identify vulnerabilities in open "
            "source projects."
        ),
    )
    parser.add_argument(
        "project", type=str, help="Project the command applies to (ignored by config/migrate)"
    )
    parser.add_argument("command", type=str, choices=COMMANDS, help="Command to execute")
    parser.add_argument(
        "--query", type=str, help="GitHub code search query. Required by 'search'."
    )
    parser.add_argument(
        "--token",
        type=str,
        help="GitHub API token. Falls back to GITHUB_TOKEN from the environment/.env.",
    )
    parser.add_argument(
        "--db", type=str, help="SQLite database path (default: CTRLF_DB_PATH)."
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        help="For 'augment': update every repository, not just this project's.",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="For 'list': cap the number of rows shown."
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Only log warnings and errors."
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Allow `search_github.py config` without the otherwise-required project.
    if len(argv) == 1 and argv[0] in PROJECTLESS:
        argv = ["-", argv[0]]
    return build_parser().parse_args(argv)


def _resolve_settings(args: argparse.Namespace) -> Settings:
    overrides: dict[str, object] = {}
    if args.db:
        overrides["db_path"] = Path(args.db).expanduser()
    if args.token:
        overrides["github_token"] = args.token
    if args.verbose:
        overrides["log_level"] = "DEBUG"
    elif args.quiet:
        overrides["log_level"] = "WARNING"
    return default_settings.with_overrides(**overrides) if overrides else default_settings


def _client(settings: Settings) -> GitHubClient:
    return GitHubClient(
        settings.github_token,
        timeout=settings.http_timeout,
        rate_limit_delay=settings.rate_limit_delay,
    )


def _ensure_project(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT OR IGNORE INTO Projects (name) VALUES (?)", (name,))
    project_id = project_id_for(conn, name)
    assert project_id is not None
    return project_id


def command_search(args: argparse.Namespace, settings: Settings) -> int:
    with connect(settings.db_path) as conn:
        project_id = _ensure_project(conn, args.project)
        conn.execute(
            """
            INSERT INTO Searches (project_id, query, date, finished, size_cursor, size_step)
            VALUES (?, ?, ?, 0, 0, 100)
            """,
            (project_id, args.query, datetime.now(timezone.utc).isoformat()),
        )
        search_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        log.info("Search #%d started for project %r: %s", search_id, args.project, args.query)
        return _sweep(conn, settings, search_id, args.query, 0, 100)


def command_resume(args: argparse.Namespace, settings: Settings) -> int:
    with connect(settings.db_path) as conn:
        project_id = project_id_for(conn, args.project)
        if project_id is None:
            log.error("Project %r not found.", args.project)
            return 1
        row = conn.execute(
            """
            SELECT id, query, size_cursor, size_step
            FROM Searches
            WHERE project_id = ? AND finished = 0
            ORDER BY id DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            log.info("No unfinished search for project %r; nothing to resume.", args.project)
            return 0

        log.info(
            "Resuming search #%d (%s) from size %d with step %d",
            row["id"],
            row["query"],
            row["size_cursor"],
            row["size_step"],
        )
        return _sweep(
            conn,
            settings,
            row["id"],
            row["query"],
            row["size_cursor"],
            row["size_step"] or 100,
        )


def _sweep(
    conn: sqlite3.Connection,
    settings: Settings,
    search_id: int,
    query: str,
    size_cursor: int,
    size_step: int,
) -> int:
    """Run a sweep, leaving the search resumable if it is interrupted."""
    try:
        outcome = run_search(
            conn,
            _client(settings),
            search_id=search_id,
            query=query,
            size_cursor=size_cursor,
            size_step=size_step,
            page_delay=settings.search_page_delay,
            window_delay=settings.search_page_delay,
        )
    except KeyboardInterrupt:
        conn.commit()
        log.warning(
            "Interrupted. Resume with: search_github.py <project> resume",
        )
        return 130
    except GitHubError as exc:
        conn.commit()
        log.error("Search stopped: %s", exc)
        return 1

    conn.execute("UPDATE Searches SET finished = 1 WHERE id = ?", (search_id,))
    conn.commit()
    log.info(
        "Search complete: %d windows, %d new files, %d already known, %d skipped",
        outcome.windows,
        outcome.ingested.new_files,
        outcome.ingested.known_files,
        outcome.ingested.skipped,
    )
    return 0


def command_augment(args: argparse.Namespace, settings: Settings) -> int:
    """Fill in stargazer counts for repositories that have none yet."""
    client = _client(settings)
    scoped = not args.all_projects
    updated = unavailable = 0

    with connect(settings.db_path) as conn:
        if scoped and project_id_for(conn, args.project) is None:
            log.error(
                "Project %r not found. Use --all-projects to augment everything.",
                args.project,
            )
            return 1

        if scoped:
            batch_sql = """
                SELECT DISTINCT r.id AS id, r.full_name AS full_name
                FROM Repositories r
                JOIN Files f ON f.repository_id = r.id
                JOIN FileSearches fs ON fs.file_id = f.id
                JOIN Searches s ON s.id = fs.search_id
                JOIN Projects p ON p.id = s.project_id
                WHERE r.stargazers_count = ? AND p.name = ?
                ORDER BY r.id LIMIT ?
            """
            params: tuple = (STARS_UNKNOWN, args.project, AUGMENT_BATCH)
        else:
            batch_sql = """
                SELECT id, full_name FROM Repositories
                WHERE stargazers_count = ?
                ORDER BY id LIMIT ?
            """
            params = (STARS_UNKNOWN, AUGMENT_BATCH)

        try:
            while True:
                repos = conn.execute(batch_sql, params).fetchall()
                if not repos:
                    break
                for repo in repos:
                    full_name = repo["full_name"]
                    if not full_name:
                        # No name to query: mark it so the batch cannot loop forever.
                        conn.execute(
                            "UPDATE Repositories SET stargazers_count = ? WHERE id = ?",
                            (STARS_UNAVAILABLE, repo["id"]),
                        )
                        conn.commit()
                        unavailable += 1
                        continue

                    data = client.get_repo(full_name)
                    if data is None:
                        # A deleted repo kept its -1 forever in the old version, so
                        # the batch query returned it again on every pass.
                        conn.execute(
                            "UPDATE Repositories SET stargazers_count = ? WHERE id = ?",
                            (STARS_UNAVAILABLE, repo["id"]),
                        )
                        unavailable += 1
                        log.info("%s is gone; marked unavailable", full_name)
                    else:
                        stars = data.get("stargazers_count")
                        conn.execute(
                            "UPDATE Repositories SET stargazers_count = ? WHERE id = ?",
                            (STARS_UNAVAILABLE if stars is None else stars, repo["id"]),
                        )
                        updated += 1
                        log.info("%s: %s stars", full_name, stars)
                    conn.commit()
                    sleep(settings.augment_delay)
        except KeyboardInterrupt:
            conn.commit()
            log.warning("Interrupted after %d updates; re-run augment to continue.", updated)
            return 130
        except GitHubError as exc:
            conn.commit()
            log.error("Augmentation stopped: %s", exc)
            return 1

    log.info("Augmentation complete: %d updated, %d unavailable", updated, unavailable)
    return 0


def command_list(args: argparse.Namespace, settings: Settings) -> int:
    with connect(settings.db_path) as conn:
        if project_id_for(conn, args.project) is None:
            log.error("Project %r not found.", args.project)
            return 1
        sql = """
            SELECT DISTINCT r.id, r.full_name, r.html_url, r.stargazers_count AS stars
            FROM Repositories r
            JOIN Files f ON f.repository_id = r.id
            JOIN FileSearches fs ON fs.file_id = f.id
            JOIN Searches s ON s.id = fs.search_id
            JOIN Projects p ON p.id = s.project_id
            WHERE p.name = ?
            ORDER BY r.stargazers_count DESC, r.full_name
        """
        params: list = [args.project]
        if args.limit and args.limit > 0:
            sql += " LIMIT ?"
            params.append(args.limit)
        repos = conn.execute(sql, params).fetchall()

    if not repos:
        print(f"No repositories found for project {args.project!r}.")
        return 0
    print(f"Repositories for project {args.project!r}: {len(repos)}")
    for repo in repos:
        print(f"{_format_stars(repo['stars']):>8}  {repo['full_name']}  {repo['html_url']}")
    return 0


def _format_stars(stars: int | None) -> str:
    if stars is None or stars == STARS_UNKNOWN:
        return "?"
    if stars == STARS_UNAVAILABLE:
        return "n/a"
    return str(stars)


def command_stats(args: argparse.Namespace, settings: Settings) -> int:
    with connect(settings.db_path) as conn:
        project_id = project_id_for(conn, args.project)
        if project_id is None:
            print(f"Project {args.project!r} not found.")
            known = list_project_names(conn)
            if known:
                print("Known projects: " + ", ".join(known))
            return 1

        searches = conn.execute(
            "SELECT COUNT(*) AS total, SUM(finished = 0) AS unfinished"
            " FROM Searches WHERE project_id = ?",
            (project_id,),
        ).fetchone()

        totals = conn.execute(
            """
            SELECT COUNT(DISTINCT f.id) AS files,
                   COUNT(DISTINCT r.id) AS repos,
                   COUNT(DISTINCT CASE WHEN f.checked = 1 THEN f.id END) AS checked,
                   COUNT(DISTINCT CASE WHEN r.stargazers_count < 0 THEN r.id END) AS unknown_stars
            FROM Files f
            JOIN Repositories r ON r.id = f.repository_id
            JOIN FileSearches fs ON fs.file_id = f.id
            JOIN Searches s ON s.id = fs.search_id
            WHERE s.project_id = ?
            """,
            (project_id,),
        ).fetchone()

        top = conn.execute(
            """
            SELECT r.full_name, r.stargazers_count AS stars, r.html_url
            FROM Repositories r
            JOIN Files f ON f.repository_id = r.id
            JOIN FileSearches fs ON fs.file_id = f.id
            JOIN Searches s ON s.id = fs.search_id
            WHERE s.project_id = ? AND r.stargazers_count >= 0
            GROUP BY r.id
            ORDER BY r.stargazers_count DESC
            LIMIT 10
            """,
            (project_id,),
        ).fetchall()

    files = totals["files"] or 0
    checked = totals["checked"] or 0
    remaining = files - checked
    percent = (checked / files * 100) if files else 0.0

    print(f"Project: {args.project}")
    print(f"  Searches:       {searches['total'] or 0} ({searches['unfinished'] or 0} unfinished)")
    unknown_stars = totals["unknown_stars"] or 0
    print(f"  Repositories:   {totals['repos'] or 0} ({unknown_stars} without star counts)")
    print(f"  Files:          {files}")
    print(f"  Checked:        {checked} ({percent:.1f}%)")
    print(f"  Remaining:      {remaining}")
    if totals["unknown_stars"]:
        print("  Run 'augment' to fetch the missing star counts.")

    if top:
        print("\nTop repositories by stars:")
        for index, repo in enumerate(top, 1):
            print(f"  {index:2}. {repo['stars']:>7}  {repo['full_name']}  {repo['html_url']}")
    return 0


def command_config(args: argparse.Namespace, settings: Settings) -> int:
    env_path = settings.env_path
    if env_path.exists():
        print(f"Config file {env_path} already exists; leaving it untouched.")
        return 0
    env_path.write_text(ENV_TEMPLATE, encoding="utf-8")
    print(f"Wrote template config to {env_path}. Add your GitHub token to it.")
    return 0


def command_migrate(args: argparse.Namespace, settings: Settings) -> int:
    result = migrate(settings.db_path)
    created = result["indexes_created"]
    print(f"Database: {settings.db_path}")
    print(f"  Journal mode:   {result['journal_mode']}")
    if created:
        print(f"  Indexes created: {', '.join(created)}")
    else:
        print("  Indexes already present.")
    return 0


_HANDLERS = {
    "search": command_search,
    "resume": command_resume,
    "augment": command_augment,
    "list": command_list,
    "stats": command_stats,
    "config": command_config,
    "migrate": command_migrate,
}

_NEEDS_TOKEN = frozenset({"search", "resume", "augment"})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = _resolve_settings(args)
    logging_setup.configure(settings.log_level)

    if args.command == "config":
        return command_config(args, settings)

    if args.command == "search" and not args.query:
        log.error("The search command requires --query.")
        return 2

    if args.command in _NEEDS_TOKEN and not settings.github_token:
        log.error(
            "The %s command needs a GitHub token: pass --token or set GITHUB_TOKEN "
            "in .env (run 'config' to generate a template).",
            args.command,
        )
        return 2

    initialize_database(settings.db_path)

    try:
        return _HANDLERS[args.command](args, settings)
    except KeyboardInterrupt:
        log.warning("Interrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
