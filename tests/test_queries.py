"""Filtering, sorting, paging and the checked-flag writes."""

from __future__ import annotations

import pytest

from ctrlfvuln.db import connect
from ctrlfvuln.queries import (
    FileFilters,
    count_files,
    count_files_capped,
    fetch_files,
    fetch_queries_for_files,
    fetch_repo,
    fetch_repo_files,
    like_pattern,
    normalize_sort,
    project_summary,
    set_checked,
    set_repo_checked,
)
from tests.conftest import COMMA_QUERY


@pytest.fixture
def conn(seeded):
    with connect(seeded) as connection:
        yield connection


def _ids(rows):
    return [row["id"] for row in rows]


def test_unchecked_only_by_default(conn):
    filters = FileFilters(project="php-search").normalized()
    rows = fetch_files(conn, filters, limit=50)
    assert 101 not in _ids(rows)  # 101 is checked
    assert count_files(conn, filters) == len(rows)


def test_show_checked_includes_everything(conn):
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    assert set(_ids(fetch_files(conn, filters, limit=50))) == {100, 101, 102, 103, 104}


def test_other_project_files_are_excluded(conn):
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    assert 105 not in _ids(fetch_files(conn, filters, limit=50))
    filters_other = FileFilters(project="other", show_checked=True).normalized()
    assert _ids(fetch_files(conn, filters_other, limit=50)) == [105]


def test_min_stars_zero_keeps_repos_with_unknown_star_counts(conn):
    """Repo 12 has stargazers_count = -1; a 'stars >= 0' filter used to hide it."""
    filters = FileFilters(project="php-search", min_stars=0).normalized()
    assert 104 in _ids(fetch_files(conn, filters, limit=50))


def test_min_stars_filters_when_set(conn):
    filters = FileFilters(project="php-search", min_stars=600).normalized()
    assert fetch_files(conn, filters, limit=50) == []
    assert count_files(conn, filters) == 0

    filters = FileFilters(project="php-search", min_stars=500).normalized()
    assert 104 not in _ids(fetch_files(conn, filters, limit=50))


def test_search_matches_name_path_and_repo(conn):
    def matching(term):
        filters = FileFilters(
            project="php-search", show_checked=True, search=term
        ).normalized()
        return _ids(fetch_files(conn, filters, limit=50))

    assert matching("helper") == [102]
    assert 100 in matching("src/")
    assert set(matching("bob/")) == {102, 103}


def test_search_escapes_like_wildcards(conn):
    """A literal % in the term must not behave as a wildcard."""
    filters = FileFilters(project="php-search", show_checked=True, search="100%").normalized()
    assert _ids(fetch_files(conn, filters, limit=50)) == [103]

    # Underscore is likewise literal, so this matches query_100%.php only.
    filters = FileFilters(project="php-search", show_checked=True, search="query_").normalized()
    assert _ids(fetch_files(conn, filters, limit=50)) == [103]


def test_like_pattern_escaping():
    assert like_pattern("100%") == "%100\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("back\\slash") == "%back\\\\slash%"


def test_unknown_sort_and_direction_fall_back():
    assert normalize_sort("nonsense", "sideways") == ("stars", "desc")
    assert normalize_sort("name", "asc") == ("name", "asc")


def test_sort_by_name_ascending(conn):
    filters = FileFilters(
        project="php-search", show_checked=True, sort="name", direction="asc"
    ).normalized()
    names = [row["name"] for row in fetch_files(conn, filters, limit=50)]
    assert names == sorted(names)


def test_injection_attempt_in_sort_is_ignored(conn):
    filters = FileFilters(
        project="php-search", show_checked=True, sort="name; DROP TABLE Files"
    ).normalized()
    assert filters.sort == "stars"
    assert fetch_files(conn, filters, limit=50)  # Still returns rows, nothing dropped.
    assert conn.execute("SELECT COUNT(*) FROM Files").fetchone()[0] == 6


def test_paging_does_not_repeat_rows_when_sort_values_tie(conn):
    """Repos 10 and 11 both have 500 stars; paging must still be stable."""
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    page_size = 2
    seen: list[int] = []
    for page in range(3):
        rows = fetch_files(
            conn, filters, limit=page_size, offset=page * page_size
        )
        seen.extend(_ids(rows))
    assert len(seen) == len(set(seen)) == 5


def test_capped_count_is_exact_below_the_cap(conn):
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    assert count_files_capped(conn, filters, cap=100) == (5, False)


def test_capped_count_reports_the_cap_when_exceeded(conn):
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    assert count_files_capped(conn, filters, cap=3) == (3, True)


def test_capped_count_matches_the_exact_count(conn):
    """The cheap count must not disagree with the exact one below the cap."""
    for show_checked in (True, False):
        filters = FileFilters(
            project="php-search", show_checked=show_checked
        ).normalized()
        assert count_files_capped(conn, filters, cap=1000)[0] == count_files(
            conn, filters
        )


def test_a_file_in_two_searches_is_counted_once(conn):
    """File 100 is linked to two searches in the same project."""
    filters = FileFilters(project="php-search", show_checked=True).normalized()
    assert count_files(conn, filters) == 5
    assert len(fetch_files(conn, filters, limit=50)) == 5


def test_project_summary_counts(conn):
    summary = project_summary(conn, "php-search")
    assert summary == {"files": 5, "checked": 1, "repos": 3}


def test_queries_with_commas_are_not_split(conn):
    """Regression: GROUP_CONCAT's comma separator corrupted such queries."""
    grouped = fetch_queries_for_files(conn, [100])
    assert COMMA_QUERY in grouped[100]
    assert len(grouped[100]) == 2


def test_fetch_queries_for_no_files(conn):
    assert fetch_queries_for_files(conn, []) == {}


def test_set_checked_round_trip(conn):
    assert set_checked(conn, [100, 102], True) == 2
    rows = conn.execute("SELECT checked FROM Files WHERE id IN (100, 102)").fetchall()
    assert [row["checked"] for row in rows] == [1, 1]

    assert set_checked(conn, [100], False) == 1
    assert conn.execute("SELECT checked FROM Files WHERE id = 100").fetchone()["checked"] == 0
    assert set_checked(conn, [], True) == 0


def test_set_repo_checked(conn):
    assert set_repo_checked(conn, 11, True) == 2
    assert set_repo_checked(conn, 11, False) == 2


def test_fetch_repo_and_files(conn):
    repo = fetch_repo(conn, 10)
    assert repo["full_name"] == "alice/shop"
    assert repo["project_name"] in {"php-search", "other"}
    assert fetch_repo(conn, 999) is None
    assert len(fetch_repo_files(conn, 10)) == 3


def test_query_args_round_trip_every_filter():
    filters = FileFilters(
        project="p", min_stars=5, show_checked=True, search="x",
        sort="name", direction="asc",
    ).normalized()
    args = filters.as_query_args()
    assert args == {
        "project": "p",
        "min_stars": 5,
        "show_checked": "on",
        "q": "x",
        "sort": "name",
        "direction": "asc",
    }
