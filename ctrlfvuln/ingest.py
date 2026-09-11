"""Persisting GitHub code-search results.

Ingestion is idempotent: re-running a query re-links existing rows rather than
duplicating them, which is what makes ``resume`` safe.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

log = logging.getLogger(__name__)

_REPO_INSERT = """
    INSERT OR IGNORE INTO Repositories
        (id, node_id, name, full_name, private, owner_login, owner_id,
         html_url, description, fork, url)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_FILE_INSERT = """
    INSERT INTO Files
        (repository_id, name, path, sha, url, git_url, html_url, score, checked)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
"""

_FILE_LOOKUP = """
    SELECT id FROM Files
    WHERE repository_id = ? AND name = ? AND path = ? AND sha = ?
"""


@dataclass
class IngestResult:
    new_files: int = 0
    known_files: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.new_files + self.known_files

    def __iadd__(self, other: IngestResult) -> IngestResult:
        self.new_files += other.new_files
        self.known_files += other.known_files
        self.skipped += other.skipped
        return self


def _repo_row(repo: dict) -> tuple | None:
    """Flatten a repository payload, or None if required fields are missing."""
    repo_id = repo.get("id")
    if repo_id is None:
        return None
    owner = repo.get("owner") or {}
    return (
        repo_id,
        repo.get("node_id"),
        repo.get("name"),
        repo.get("full_name"),
        int(bool(repo.get("private"))),
        owner.get("login"),
        owner.get("id"),
        repo.get("html_url"),
        repo.get("description"),
        int(bool(repo.get("fork"))),
        repo.get("url"),
    )


def ingest_items(
    conn: sqlite3.Connection, search_id: int, items: list[dict]
) -> IngestResult:
    """Store one page of search results and link them to ``search_id``.

    A malformed item is logged and skipped; it must not abort a search that may
    already have been running for hours.
    """
    result = IngestResult()
    if not items:
        return result

    repo_rows: dict[int, tuple] = {}
    for item in items:
        row = _repo_row(item.get("repository") or {})
        if row is not None:
            repo_rows.setdefault(row[0], row)
    if repo_rows:
        conn.executemany(_REPO_INSERT, list(repo_rows.values()))

    links: list[tuple[int, int]] = []
    for item in items:
        repo = item.get("repository") or {}
        repo_id = repo.get("id")
        name, path, sha = item.get("name"), item.get("path"), item.get("sha")
        if repo_id is None or not path or not sha:
            log.warning("Skipping malformed search result: %r", item.get("html_url"))
            result.skipped += 1
            continue

        existing = conn.execute(_FILE_LOOKUP, (repo_id, name, path, sha)).fetchone()
        if existing:
            file_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
            result.known_files += 1
        else:
            cursor = conn.execute(
                _FILE_INSERT,
                (
                    repo_id,
                    name,
                    path,
                    sha,
                    item.get("url"),
                    item.get("git_url"),
                    item.get("html_url"),
                    item.get("score") or 0,
                ),
            )
            file_id = cursor.lastrowid
            result.new_files += 1
        links.append((file_id, search_id))

    if links:
        conn.executemany(
            "INSERT OR IGNORE INTO FileSearches (file_id, search_id) VALUES (?, ?)",
            links,
        )
    return result
