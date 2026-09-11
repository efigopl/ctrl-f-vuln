"""Storing search results."""

from __future__ import annotations

from ctrlfvuln.db import connect
from ctrlfvuln.ingest import ingest_items


def make_item(index: int, repo_id: int = 500) -> dict:
    return {
        "name": f"f{index}.php",
        "path": f"src/f{index}.php",
        "sha": f"sha{index}",
        "url": f"https://api.github.com/repositories/{repo_id}/contents/src/f{index}.php",
        "git_url": f"https://api.github.com/repositories/{repo_id}/git/blobs/sha{index}",
        "html_url": f"https://github.com/owner/repo/blob/main/src/f{index}.php",
        "score": 1.0,
        "repository": {
            "id": repo_id,
            "node_id": f"node{repo_id}",
            "name": "repo",
            "full_name": f"owner{repo_id}/repo",
            "private": False,
            "owner": {"login": f"owner{repo_id}", "id": repo_id},
            "html_url": f"https://github.com/owner{repo_id}/repo",
            "description": "desc",
            "fork": False,
            "url": f"https://api.github.com/repos/owner{repo_id}/repo",
        },
    }


def _prepare(conn) -> int:
    conn.execute("INSERT INTO Projects (id, name) VALUES (1, 'p')")
    conn.execute(
        "INSERT INTO Searches (id, project_id, query, date) VALUES (1, 1, 'q', 'now')"
    )
    return 1


def test_ingest_stores_repos_files_and_links(db_path):
    with connect(db_path) as conn:
        search_id = _prepare(conn)
        result = ingest_items(conn, search_id, [make_item(1), make_item(2)])

        assert (result.new_files, result.known_files, result.skipped) == (2, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM Repositories").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM FileSearches").fetchone()[0] == 2


def test_reingesting_the_same_items_creates_no_duplicates(db_path):
    """Makes 'resume' safe: a replayed window re-links instead of duplicating."""
    with connect(db_path) as conn:
        search_id = _prepare(conn)
        items = [make_item(1), make_item(2)]
        ingest_items(conn, search_id, items)
        second = ingest_items(conn, search_id, items)

        assert (second.new_files, second.known_files) == (0, 2)
        assert conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM FileSearches").fetchone()[0] == 2


def test_reingest_preserves_an_augmented_star_count(db_path):
    """A second sighting of a repo must not reset the work 'augment' did."""
    with connect(db_path) as conn:
        search_id = _prepare(conn)
        ingest_items(conn, search_id, [make_item(1)])
        conn.execute("UPDATE Repositories SET stargazers_count = 4242 WHERE id = 500")

        ingest_items(conn, search_id, [make_item(2)])

        stars = conn.execute(
            "SELECT stargazers_count FROM Repositories WHERE id = 500"
        ).fetchone()[0]
    assert stars == 4242


def test_the_same_file_links_to_several_searches(db_path):
    with connect(db_path) as conn:
        _prepare(conn)
        conn.execute(
            "INSERT INTO Searches (id, project_id, query, date) VALUES (2, 1, 'q2', 'now')"
        )
        ingest_items(conn, 1, [make_item(1)])
        ingest_items(conn, 2, [make_item(1)])

        assert conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM FileSearches").fetchone()[0] == 2


def test_malformed_items_are_skipped_not_fatal(db_path):
    """A bad payload used to raise KeyError and abandon a running search."""
    with connect(db_path) as conn:
        search_id = _prepare(conn)
        broken = [
            {},                                        # nothing at all
            {"repository": {}, "path": "a", "sha": "b"},  # no repo id
            {"repository": {"id": 9}, "sha": "b"},      # no path
            make_item(7),                               # the good one
        ]
        result = ingest_items(conn, search_id, broken)

        assert result.new_files == 1
        assert result.skipped == 3
        assert conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 1


def test_empty_page_is_a_noop(db_path):
    with connect(db_path) as conn:
        search_id = _prepare(conn)
        result = ingest_items(conn, search_id, [])
    assert result.total == 0
