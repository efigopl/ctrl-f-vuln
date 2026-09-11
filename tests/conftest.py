"""Shared fixtures: a temporary database with predictable contents."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctrlfvuln.config import settings as base_settings  # noqa: E402
from ctrlfvuln.db import connect, initialize_database  # noqa: E402
from ctrlfvuln.web import create_app  # noqa: E402

#: A query containing a comma, which the old GROUP_CONCAT display split apart.
COMMA_QUERY = 'mysql_query( language:php size:0..100, extension:php'


@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "test.db"
    initialize_database(path)
    return path


@pytest.fixture
def seeded(db_path) -> Path:
    """Two projects, three repositories and six files with known state."""
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO Projects (id, name) VALUES (?, ?)",
            [(1, "php-search"), (2, "other")],
        )
        conn.executemany(
            """INSERT INTO Searches (id, project_id, query, date, finished)
               VALUES (?, ?, ?, ?, ?)""",
            [
                (1, 1, "mysql_query(", "2026-01-01T00:00:00", 1),
                (2, 1, COMMA_QUERY, "2026-01-02T00:00:00", 0),
                (3, 2, "eval(", "2026-01-03T00:00:00", 1),
            ],
        )
        conn.executemany(
            """INSERT INTO Repositories
               (id, name, full_name, owner_login, html_url, description,
                url, stargazers_count, private, fork, node_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
            [
                (10, "shop", "alice/shop", "alice", "https://github.com/alice/shop",
                 "A shop", "https://api.github.com/repos/alice/shop", 500, "n10"),
                (11, "utils", "bob/utils", "bob", "https://github.com/bob/utils",
                 None, "https://api.github.com/repos/bob/utils", 500, "n11"),
                # Star count never fetched: must still be visible at min_stars=0.
                (12, "legacy", "carol/legacy", "carol",
                 "https://github.com/carol/legacy", None,
                 "https://api.github.com/repos/carol/legacy", -1, "n12"),
            ],
        )
        conn.executemany(
            """INSERT INTO Files
               (id, repository_id, name, path, sha, url, git_url, html_url, score, checked)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (100, 10, "db.php", "src/db.php", "sha100",
                 "https://api.github.com/repositories/10/contents/src/db.php",
                 "https://api.github.com/repositories/10/git/blobs/sha100",
                 "https://github.com/alice/shop/blob/main/src/db.php", 1.0, 0),
                (101, 10, "index.php", "public/index.php", "sha101",
                 "https://api.github.com/repositories/10/contents/public/index.php",
                 "https://api.github.com/repositories/10/git/blobs/sha101",
                 "https://github.com/alice/shop/blob/main/public/index.php", 0.9, 1),
                (102, 11, "helper.php", "lib/helper.php", "sha102",
                 "https://api.github.com/repositories/11/contents/lib/helper.php",
                 "https://api.github.com/repositories/11/git/blobs/sha102",
                 "https://github.com/bob/utils/blob/main/lib/helper.php", 0.8, 0),
                (103, 11, "query_100%.php", "lib/query_100%.php", "sha103",
                 "https://api.github.com/repositories/11/contents/lib/q.php",
                 "https://api.github.com/repositories/11/git/blobs/sha103",
                 "https://github.com/bob/utils/blob/main/lib/q.php", 0.7, 0),
                (104, 12, "old.php", "old.php", "sha104",
                 "https://api.github.com/repositories/12/contents/old.php",
                 "https://api.github.com/repositories/12/git/blobs/sha104",
                 "https://github.com/carol/legacy/blob/main/old.php", 0.6, 0),
                # Belongs to the second project only.
                (105, 10, "eval.php", "src/eval.php", "sha105",
                 "https://api.github.com/repositories/10/contents/src/eval.php",
                 "https://api.github.com/repositories/10/git/blobs/sha105",
                 "https://github.com/alice/shop/blob/main/src/eval.php", 0.5, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO FileSearches (file_id, search_id) VALUES (?, ?)",
            [
                (100, 1), (100, 2),  # surfaced by two queries, one with a comma
                (101, 1),
                (102, 1),
                (103, 2),
                (104, 1),
                (105, 3),
            ],
        )
    return db_path


@pytest.fixture
def settings(seeded, tmp_path):
    return base_settings.with_overrides(
        db_path=seeded,
        projects_dir=tmp_path / "projects",
        env_path=tmp_path / ".env",
        github_token=None,
        editor_command=None,
        page_size=100,
        secret_key="test-secret",
        augment_delay=0.0,
        search_page_delay=0.0,
        rate_limit_delay=0.0,
    )


@pytest.fixture
def empty_settings(db_path, tmp_path):
    """Settings pointing at an initialized but empty database."""
    return base_settings.with_overrides(
        db_path=db_path,
        projects_dir=tmp_path / "projects",
        env_path=tmp_path / ".env",
        github_token=None,
        editor_command=None,
        secret_key="test-secret",
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def legacy_db(tmp_path) -> Path:
    """A database in the pre-0.2 shape: no indexes, no progress columns."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE Projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE Searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            date TEXT NOT NULL,
            finished BOOLEAN NOT NULL DEFAULT 0
        );
        CREATE TABLE Repositories (
            id INTEGER PRIMARY KEY,
            node_id TEXT, name TEXT, full_name TEXT, private BOOLEAN,
            owner_login TEXT, owner_id INTEGER, html_url TEXT,
            description TEXT, fork BOOLEAN, url TEXT
        );
        CREATE TABLE Files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repository_id INTEGER NOT NULL,
            name TEXT, path TEXT, sha TEXT, url TEXT, git_url TEXT,
            html_url TEXT, score REAL,
            checked BOOLEAN NOT NULL DEFAULT 0
        );
        CREATE TABLE FileSearches (
            file_id INTEGER NOT NULL,
            search_id INTEGER NOT NULL,
            PRIMARY KEY (file_id, search_id)
        );
        INSERT INTO Projects (name) VALUES ('legacy-project');
        """
    )
    conn.commit()
    conn.close()
    return path
