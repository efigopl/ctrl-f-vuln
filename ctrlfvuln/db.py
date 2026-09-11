"""SQLite schema, migrations and connection handling.

All database access goes through :func:`connect`, which guarantees the
connection is closed and the transaction resolved even when the caller raises.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

#: ``stargazers_count`` sentinel: never fetched yet.
STARS_UNKNOWN = -1
#: ``stargazers_count`` sentinel: fetch failed permanently (repo deleted or blocked).
STARS_UNAVAILABLE = -2

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS Projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Searches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        query TEXT NOT NULL,
        date TEXT NOT NULL,
        finished BOOLEAN NOT NULL DEFAULT 0,
        size_cursor INTEGER NOT NULL DEFAULT 0,
        size_step INTEGER NOT NULL DEFAULT 100,
        FOREIGN KEY (project_id) REFERENCES Projects(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Repositories (
        id INTEGER PRIMARY KEY,
        node_id TEXT,
        name TEXT,
        full_name TEXT,
        private BOOLEAN,
        owner_login TEXT,
        owner_id INTEGER,
        html_url TEXT,
        description TEXT,
        fork BOOLEAN,
        url TEXT,
        stargazers_count INTEGER DEFAULT -1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        repository_id INTEGER NOT NULL,
        name TEXT,
        path TEXT,
        sha TEXT,
        url TEXT,
        git_url TEXT,
        html_url TEXT,
        score REAL,
        checked BOOLEAN NOT NULL DEFAULT 0,
        FOREIGN KEY (repository_id) REFERENCES Repositories(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS FileSearches (
        file_id INTEGER NOT NULL,
        search_id INTEGER NOT NULL,
        PRIMARY KEY (file_id, search_id),
        FOREIGN KEY (file_id) REFERENCES Files(id) ON DELETE CASCADE,
        FOREIGN KEY (search_id) REFERENCES Searches(id) ON DELETE CASCADE
    )
    """,
)

# Foreign-key columns get no index automatically in SQLite, so every join in the
# viewer degraded into a scan as the dataset grew. FileSearches(file_id) is
# already covered by the leading column of its primary key, hence absent here.
INDEX_STATEMENTS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_files_repository_id ON Files(repository_id)",
    "CREATE INDEX IF NOT EXISTS idx_files_checked ON Files(checked)",
    # Covers the per-result duplicate lookup that runs once per ingested search hit.
    "CREATE INDEX IF NOT EXISTS idx_files_dedupe ON Files(repository_id, name, path, sha)",
    # Covering: lets the project -> files walk behind the progress header run
    # entirely out of the index instead of touching FileSearches rows.
    "CREATE INDEX IF NOT EXISTS idx_filesearches_search_file"
    " ON FileSearches(search_id, file_id)",
    "CREATE INDEX IF NOT EXISTS idx_searches_project_id ON Searches(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_repositories_stargazers ON Repositories(stargazers_count)",
    "CREATE INDEX IF NOT EXISTS idx_repositories_full_name ON Repositories(full_name)",
)


@contextmanager
def connect(db_path: str | Path, *, timeout: float = 30.0) -> Iterator[sqlite3.Connection]:
    """Yield a connection that commits on success and rolls back on error.

    ``busy_timeout`` matters because the viewer is normally read while a search
    is still writing; without it concurrent access raises "database is locked".
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def initialize_database(db_path: str | Path) -> bool:
    """Create the schema and indexes if needed. Returns True if the file was new.

    Idempotent: safe to run against a populated database, which is what makes
    the index migration deployable without a dump/restore.
    """
    path = Path(db_path)
    is_new = not path.exists()
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        # Write-ahead logging lets the web viewer read while a search writes.
        conn.execute("PRAGMA journal_mode = WAL")
        # Columns first: an index cannot be built on a column that is still missing.
        _backfill_columns(conn)
        _apply_indexes(conn)

    if is_new:
        log.info("Created new SQLite database at %s", path)
    else:
        log.debug("Using existing SQLite database at %s", path)
    return is_new


def _apply_indexes(conn: sqlite3.Connection) -> list[str]:
    """Create any missing index. Returns the names that did not exist before."""
    existing = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    created = []
    for statement in INDEX_STATEMENTS:
        name = statement.split("IF NOT EXISTS", 1)[1].split(" ON ", 1)[0].strip()
        if name not in existing:
            created.append(name)
        conn.execute(statement)
    return created


def _backfill_columns(conn: sqlite3.Connection) -> None:
    """Add columns that older databases predate."""
    if "stargazers_count" not in _table_columns(conn, "Repositories"):
        conn.execute(
            "ALTER TABLE Repositories ADD COLUMN stargazers_count INTEGER DEFAULT -1"
        )
    # Progress columns let an interrupted search resume instead of restarting.
    search_columns = _table_columns(conn, "Searches")
    if "size_cursor" not in search_columns:
        conn.execute(
            "ALTER TABLE Searches ADD COLUMN size_cursor INTEGER NOT NULL DEFAULT 0"
        )
    if "size_step" not in search_columns:
        conn.execute(
            "ALTER TABLE Searches ADD COLUMN size_step INTEGER NOT NULL DEFAULT 100"
        )


def migrate(db_path: str | Path) -> dict[str, object]:
    """Apply indexes/pragmas to an existing database and report what changed."""
    with connect(db_path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        journal_mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        _backfill_columns(conn)
        created = _apply_indexes(conn)
        conn.execute("ANALYZE")
    return {"indexes_created": created, "journal_mode": journal_mode}


def project_id_for(conn: sqlite3.Connection, name: str) -> int | None:
    row = conn.execute("SELECT id FROM Projects WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def list_project_names(conn: sqlite3.Connection) -> list[str]:
    return [row["name"] for row in conn.execute("SELECT name FROM Projects ORDER BY name")]
