"""Schema creation, index migration and connection handling."""

from __future__ import annotations

import sqlite3

import pytest

from ctrlfvuln.db import INDEX_STATEMENTS, connect, initialize_database, migrate


def _index_names(path) -> set[str]:
    with connect(path) as conn:
        return {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }


def _expected_index_names() -> set[str]:
    return {
        statement.split("IF NOT EXISTS", 1)[1].split(" ON ", 1)[0].strip()
        for statement in INDEX_STATEMENTS
    }


def test_initialize_creates_tables_and_indexes(tmp_path):
    path = tmp_path / "fresh.db"
    assert initialize_database(path) is True

    with connect(path) as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"Projects", "Searches", "Repositories", "Files", "FileSearches"} <= tables
    assert _expected_index_names() <= _index_names(path)


def test_initialize_is_idempotent(tmp_path):
    path = tmp_path / "twice.db"
    initialize_database(path)
    assert initialize_database(path) is False  # Second run reports "not new".
    assert _expected_index_names() <= _index_names(path)


def test_migrate_upgrades_a_legacy_database(legacy_db):
    """An existing database gains indexes and progress columns in place."""
    before = _index_names(legacy_db)
    assert not (_expected_index_names() & before)

    result = migrate(legacy_db)

    assert _expected_index_names() <= _index_names(legacy_db)
    assert set(result["indexes_created"]) == _expected_index_names()

    with connect(legacy_db) as conn:
        repo_columns = {row["name"] for row in conn.execute("PRAGMA table_info(Repositories)")}
        search_columns = {row["name"] for row in conn.execute("PRAGMA table_info(Searches)")}
        # Existing rows survive the migration.
        assert conn.execute("SELECT COUNT(*) FROM Projects").fetchone()[0] == 1
    assert "stargazers_count" in repo_columns
    assert {"size_cursor", "size_step"} <= search_columns


def test_migrate_twice_creates_nothing_new(legacy_db):
    migrate(legacy_db)
    assert migrate(legacy_db)["indexes_created"] == []


def test_connect_rolls_back_on_error(db_path):
    with pytest.raises(RuntimeError):
        with connect(db_path) as conn:
            conn.execute("INSERT INTO Projects (name) VALUES ('doomed')")
            raise RuntimeError("boom")

    with connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM Projects WHERE name = 'doomed'"
        ).fetchone()[0]
    assert count == 0


def test_connect_closes_the_connection_after_an_error(db_path):
    captured = {}
    with pytest.raises(ValueError):
        with connect(db_path) as conn:
            captured["conn"] = conn
            raise ValueError("boom")

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


def test_foreign_keys_are_enforced(db_path):
    with pytest.raises(sqlite3.IntegrityError):
        with connect(db_path) as conn:
            conn.execute(
                "INSERT INTO Searches (project_id, query, date) VALUES (999, 'q', 'now')"
            )
