"""The size-window sweep.

These use a fake client that answers ``size:a..b`` queries against a synthetic
universe of files, so the windowing algorithm is exercised without network
access. Every fake caps its call count, turning a non-terminating sweep into a
failure rather than a hung test run.
"""

from __future__ import annotations

import re

import pytest

from ctrlfvuln.db import connect
from ctrlfvuln.search import RESULT_CAP, run_search
from tests.test_ingest import make_item

RANGE_RE = re.compile(r"size:(\d+)\.\.(\d+)")
ABOVE_RE = re.compile(r"size:>(\d+)")


class UniverseClient:
    """Answers code-search queries over ``{size: file_count}``."""

    def __init__(self, sizes: dict[int, int], *, max_calls: int = 200):
        self.sizes = sizes
        self.max_calls = max_calls
        self.calls: list[tuple[str, int]] = []
        counter = 0
        self.by_size: dict[int, list[dict]] = {}
        for size, count in sorted(sizes.items()):
            bucket = []
            for _ in range(count):
                counter += 1
                bucket.append(make_item(counter, repo_id=500 + (counter % 3)))
            self.by_size[size] = bucket

    def _matching(self, query: str) -> list[dict]:
        window = RANGE_RE.search(query)
        if window:
            low, high = int(window.group(1)), int(window.group(2))
            return [
                item
                for size, bucket in self.by_size.items()
                if low <= size <= high
                for item in bucket
            ]
        above = ABOVE_RE.search(query)
        if above:
            threshold = int(above.group(1))
            return [
                item
                for size, bucket in self.by_size.items()
                if size > threshold
                for item in bucket
            ]
        raise AssertionError(f"query carried no size qualifier: {query!r}")

    def search_code(self, query: str, *, page: int = 1, per_page: int = 100) -> dict:
        self.calls.append((query, page))
        if len(self.calls) > self.max_calls:
            raise AssertionError(
                f"sweep did not terminate: {len(self.calls)} API calls"
            )
        matching = self._matching(query)
        start = (page - 1) * per_page
        return {
            "total_count": len(matching),
            "items": matching[start : start + per_page],
        }

    @property
    def windows(self) -> list[tuple[int, int]]:
        found = []
        for query, page in self.calls:
            match = RANGE_RE.search(query)
            if match and page == 1:
                found.append((int(match.group(1)), int(match.group(2))))
        return found


@pytest.fixture
def search_conn(db_path):
    with connect(db_path) as conn:
        conn.execute("INSERT INTO Projects (id, name) VALUES (1, 'p')")
        conn.execute(
            "INSERT INTO Searches (id, project_id, query, date) VALUES (1, 1, 'q', 'now')"
        )
        yield conn


def _run(conn, client, **kwargs):
    return run_search(
        conn,
        client,
        search_id=1,
        query="needle",
        page_delay=0,
        window_delay=0,
        **kwargs,
    )


def test_small_universe_is_swept_completely(search_conn):
    client = UniverseClient({size: 1 for size in range(50)})
    outcome = _run(search_conn, client)

    assert outcome.completed is True
    assert outcome.ingested.new_files == 50
    assert search_conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 50


def test_windows_never_re_query_the_boundary_size(search_conn):
    """The old sweep advanced by step, so each window's last size was queried twice."""
    client = UniverseClient({size: 1 for size in range(250)})
    _run(search_conn, client)

    windows = client.windows
    for (_, previous_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert next_start != previous_end


def test_every_file_is_ingested_exactly_once(search_conn):
    client = UniverseClient({size: 3 for size in range(0, 120, 4)})
    outcome = _run(search_conn, client)

    total = sum(len(bucket) for bucket in client.by_size.values())
    assert outcome.ingested.new_files == total
    assert search_conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == total


def test_a_crowded_window_is_narrowed(search_conn):
    """1200 results at one size force the step down without oscillating forever."""
    client = UniverseClient({10: 1200})
    outcome = _run(search_conn, client)

    assert outcome.completed is True
    # Only the first RESULT_CAP results are reachable through the API.
    assert search_conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == RESULT_CAP


def test_gaps_in_the_size_distribution_are_skipped(search_conn):
    """An empty window widens aggressively instead of crawling one step at a time."""
    client = UniverseClient({0: 5, 9000: 5}, max_calls=80)
    outcome = _run(search_conn, client)

    assert outcome.completed is True
    assert outcome.ingested.new_files == 10


def test_progress_is_persisted_for_resume(search_conn):
    client = UniverseClient({size: 1 for size in range(30)})
    _run(search_conn, client)

    row = search_conn.execute(
        "SELECT size_cursor, size_step FROM Searches WHERE id = 1"
    ).fetchone()
    assert row["size_cursor"] > 0
    assert row["size_step"] > 0


def test_resuming_from_a_cursor_skips_earlier_windows(search_conn):
    client = UniverseClient({size: 1 for size in range(50)})
    _run(search_conn, client, size_cursor=40, size_step=10)

    assert all(start >= 40 for start, _ in client.windows)
    # Only the files at sizes 40..49 are picked up.
    assert search_conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 10


def test_an_empty_universe_terminates_immediately(search_conn):
    client = UniverseClient({}, max_calls=6)
    outcome = _run(search_conn, client)

    assert outcome.completed is True
    assert outcome.ingested.total == 0


def test_pagination_stops_at_the_api_page_limit(search_conn):
    client = UniverseClient({5: 950})
    _run(search_conn, client)

    pages = [page for query, page in client.calls if RANGE_RE.search(query)]
    assert max(pages) <= 10
