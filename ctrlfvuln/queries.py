"""Shared SQL for the triage views.

The count query and the row query previously existed as two near-identical
string literals that had to be edited in lockstep; both are now derived from one
FROM/WHERE fragment built here.

Shape matters more than it looks. Joining Files to FileSearches/Searches/Projects
fans a file out into one row per matching search, which forced a
``GROUP BY f.id`` (and ``COUNT(DISTINCT f.id)``) and with it two temporary
B-trees per page. Testing the project membership with EXISTS instead keeps one
row per file, so the planner can walk the star index in order and stop at LIMIT.
On a 60k-file database that took a page from ~53ms to ~0.4ms.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

#: User-selectable sort keys mapped to columns. Acts as the allow-list that keeps
#: a URL parameter out of the SQL text.
SORT_COLUMNS: dict[str, str] = {
    "stars": "r.stargazers_count",
    "name": "f.name",
    "path": "f.path",
    "repo": "r.full_name",
    "checked": "f.checked",
    "score": "f.score",
}
DEFAULT_SORT = "stars"
DEFAULT_DIRECTION = "desc"
_DIRECTIONS = {"asc": "ASC", "desc": "DESC"}

#: Default ceiling for the paging count. Counting every match costs a full pass
#: over the project's files; past a few thousand the exact number tells the user
#: nothing that "10,000+" does not, so the count is bounded instead.
DEFAULT_COUNT_CAP = 10_000

_FILE_SOURCE = """
    FROM Files f
    JOIN Repositories r ON r.id = f.repository_id
"""

_PROJECT_MEMBERSHIP = """
    EXISTS (
        SELECT 1
        FROM FileSearches fs
        JOIN Searches s ON s.id = fs.search_id
        JOIN Projects p ON p.id = s.project_id
        WHERE fs.file_id = f.id AND p.name = ?
    )
"""

_ROW_COLUMNS = """
    f.id            AS id,
    f.name          AS name,
    f.path          AS path,
    f.html_url      AS file_url,
    f.git_url       AS git_url,
    f.checked       AS checked,
    f.score         AS score,
    r.id            AS repo_id,
    r.full_name     AS repo_name,
    r.html_url      AS repo_url,
    r.stargazers_count AS stars
"""


def like_pattern(term: str) -> str:
    """Build a LIKE pattern with wildcards in ``term`` escaped."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def normalize_sort(sort: str | None, direction: str | None) -> tuple[str, str]:
    """Coerce user input to a known sort key and direction."""
    key = sort if sort in SORT_COLUMNS else DEFAULT_SORT
    dir_key = (direction or "").lower()
    if dir_key not in _DIRECTIONS:
        dir_key = DEFAULT_DIRECTION
    return key, dir_key


@dataclass(frozen=True)
class FileFilters:
    """The state of the triage toolbar."""

    project: str
    min_stars: int = 0
    show_checked: bool = False
    search: str = ""
    sort: str = DEFAULT_SORT
    direction: str = DEFAULT_DIRECTION

    def normalized(self) -> FileFilters:
        sort, direction = normalize_sort(self.sort, self.direction)
        return FileFilters(
            project=self.project,
            min_stars=max(0, self.min_stars),
            show_checked=self.show_checked,
            search=(self.search or "").strip(),
            sort=sort,
            direction=direction,
        )

    def as_query_args(self) -> dict[str, object]:
        """Parameters to carry across links so no filter is lost on navigation."""
        args: dict[str, object] = {"project": self.project}
        if self.min_stars:
            args["min_stars"] = self.min_stars
        if self.show_checked:
            args["show_checked"] = "on"
        if self.search:
            args["q"] = self.search
        if self.sort != DEFAULT_SORT:
            args["sort"] = self.sort
        if self.direction != DEFAULT_DIRECTION:
            args["direction"] = self.direction
        return args


@dataclass
class _Where:
    sql: str
    params: list[object] = field(default_factory=list)


def _build_where(filters: FileFilters) -> _Where:
    clauses = [_PROJECT_MEMBERSHIP]
    params: list[object] = [filters.project]

    # A min_stars of 0 must not filter on the column at all: un-augmented repos
    # carry -1, so comparing ">= 0" silently hid every repo awaiting `augment`.
    if filters.min_stars > 0:
        clauses.append("r.stargazers_count >= ?")
        params.append(filters.min_stars)

    if not filters.show_checked:
        clauses.append("f.checked = 0")

    if filters.search:
        clauses.append(
            "(f.name LIKE ? ESCAPE '\\' OR f.path LIKE ? ESCAPE '\\'"
            " OR r.full_name LIKE ? ESCAPE '\\')"
        )
        pattern = like_pattern(filters.search)
        params.extend([pattern, pattern, pattern])

    return _Where(" WHERE " + " AND ".join(clauses), params)


def count_files(conn: sqlite3.Connection, filters: FileFilters) -> int:
    """Exact number of matching files. Costs a full pass over the project."""
    where = _build_where(filters)
    sql = "SELECT COUNT(*)" + _FILE_SOURCE + where.sql
    return conn.execute(sql, where.params).fetchone()[0]


def count_files_capped(
    conn: sqlite3.Connection, filters: FileFilters, *, cap: int = DEFAULT_COUNT_CAP
) -> tuple[int, bool]:
    """Count matches, giving up past ``cap``.

    Returns ``(count, capped)``. When ``capped`` is true the real total is
    higher than ``count`` and the UI says so rather than implying precision.
    """
    if cap <= 0:
        return count_files(conn, filters), False
    where = _build_where(filters)
    sql = "SELECT COUNT(*) FROM (SELECT f.id" + _FILE_SOURCE + where.sql + " LIMIT ?)"
    total = conn.execute(sql, [*where.params, cap + 1]).fetchone()[0]
    return (cap, True) if total > cap else (total, False)


def fetch_files(
    conn: sqlite3.Connection,
    filters: FileFilters,
    *,
    limit: int,
    offset: int = 0,
) -> list[dict]:
    where = _build_where(filters)
    column = SORT_COLUMNS[filters.sort]
    direction = _DIRECTIONS[filters.direction]
    # f.id is a tiebreaker, not decoration: ordering by stargazers alone made
    # LIMIT/OFFSET paging repeat or skip rows whenever counts tied.
    sql = (
        f"SELECT {_ROW_COLUMNS}"
        + _FILE_SOURCE
        + where.sql
        + f" ORDER BY {column} {direction}, f.id ASC LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, [*where.params, limit, offset]).fetchall()
    return [dict(row) for row in rows]


def project_summary(conn: sqlite3.Connection, project: str) -> dict[str, int]:
    """Totals for the project header, independent of the current filters.

    ``id IN (subquery)`` rather than a join: the subquery walks only this
    project's index entries and IN de-duplicates files that several searches
    found, leaving one DISTINCT instead of three. Measured at roughly half the
    cost of the equivalent join on a 60k-file database.
    """
    row = conn.execute(
        """
        SELECT COUNT(*) AS files,
               SUM(checked) AS checked,
               COUNT(DISTINCT repository_id) AS repos
        FROM Files
        WHERE id IN (
            SELECT fs.file_id
            FROM FileSearches fs
            JOIN Searches s ON s.id = fs.search_id
            WHERE s.project_id = (SELECT id FROM Projects WHERE name = ?)
        )
        """,
        (project,),
    ).fetchone()
    return {
        "files": row["files"] or 0,
        "checked": row["checked"] or 0,
        "repos": row["repos"] or 0,
    }


def fetch_file(conn: sqlite3.Connection, file_id: int) -> dict | None:
    row = conn.execute(
        f"""
        SELECT {_ROW_COLUMNS}
        FROM Files f
        JOIN Repositories r ON r.id = f.repository_id
        WHERE f.id = ?
        """,
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_repo(conn: sqlite3.Connection, repo_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT r.id, r.full_name, r.html_url, r.description, r.owner_login,
               r.stargazers_count AS stars,
               (SELECT p.name
                  FROM Files f2
                  JOIN FileSearches fs2 ON fs2.file_id = f2.id
                  JOIN Searches s2 ON s2.id = fs2.search_id
                  JOIN Projects p ON p.id = s2.project_id
                 WHERE f2.repository_id = r.id
                 LIMIT 1) AS project_name
        FROM Repositories r
        WHERE r.id = ?
        """,
        (repo_id,),
    ).fetchone()
    return dict(row) if row else None


def fetch_repo_files(conn: sqlite3.Connection, repo_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT f.id, f.name, f.path, f.html_url AS file_url, f.git_url,
               f.checked, f.score
        FROM Files f
        WHERE f.repository_id = ?
        ORDER BY f.path, f.name, f.id
        """,
        (repo_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_queries_for_files(
    conn: sqlite3.Connection, file_ids: list[int]
) -> dict[int, list[str]]:
    """Map file id -> the search queries that surfaced it.

    Done as its own query rather than GROUP_CONCAT because SQLite cannot combine
    DISTINCT with a custom separator, and the default comma corrupted any query
    that itself contained one.
    """
    if not file_ids:
        return {}

    grouped: dict[int, list[str]] = {}
    chunk_size = 500  # Stay well inside SQLITE_MAX_VARIABLE_NUMBER.
    for start in range(0, len(file_ids), chunk_size):
        chunk = file_ids[start : start + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""
            SELECT fs.file_id AS file_id, s.query AS query
            FROM FileSearches fs
            JOIN Searches s ON s.id = fs.search_id
            WHERE fs.file_id IN ({placeholders})
            ORDER BY s.id
            """,
            chunk,
        ).fetchall()
        for row in rows:
            bucket = grouped.setdefault(row["file_id"], [])
            if row["query"] not in bucket:
                bucket.append(row["query"])
    return grouped


def set_checked(conn: sqlite3.Connection, file_ids: list[int], checked: bool) -> int:
    """Set the checked flag on the given files. Returns rows affected."""
    if not file_ids:
        return 0
    total = 0
    chunk_size = 500
    for start in range(0, len(file_ids), chunk_size):
        chunk = file_ids[start : start + chunk_size]
        placeholders = ",".join("?" * len(chunk))
        cursor = conn.execute(
            f"UPDATE Files SET checked = ? WHERE id IN ({placeholders})",
            [1 if checked else 0, *chunk],
        )
        total += cursor.rowcount
    return total


def set_repo_checked(conn: sqlite3.Connection, repo_id: int, checked: bool) -> int:
    cursor = conn.execute(
        "UPDATE Files SET checked = ? WHERE repository_id = ?",
        (1 if checked else 0, repo_id),
    )
    return cursor.rowcount
