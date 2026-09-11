"""Size-windowed GitHub code search.

The code-search endpoint exposes at most 1000 results per query, so a broad
query is swept as a series of ``size:a..b`` windows each narrow enough to fit
under that ceiling. Progress is persisted per window so an interrupted run
resumes instead of starting over.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from time import sleep

from .github import GitHubClient, QueryTooBroad
from .ingest import IngestResult, ingest_items

log = logging.getLogger(__name__)

#: Hard ceiling on results the API will page through for one query.
RESULT_CAP = 1000
#: Target ceiling per window, leaving headroom below ``RESULT_CAP``.
SAFE_WINDOW = 900
#: Below this many hits a window is considered wastefully narrow.
GROW_THRESHOLD = 100
#: ``page * per_page`` may not exceed ``RESULT_CAP``.
MAX_PAGES = 10
MIN_STEP = 1
MAX_STEP = 1_000_000


def grow_step(step: int, *, aggressive: bool = False) -> int:
    """Widen a window. Aggressive growth skips gaps in the size distribution."""
    factor = 2.0 if aggressive else 1.5
    return min(MAX_STEP, max(step + 1, int(round(step * factor))))


@dataclass
class SearchOutcome:
    ingested: IngestResult
    windows: int
    size_cursor: int
    size_step: int
    completed: bool


def _save_progress(
    conn: sqlite3.Connection, search_id: int, cursor: int, step: int
) -> None:
    conn.execute(
        "UPDATE Searches SET size_cursor = ?, size_step = ? WHERE id = ?",
        (cursor, step, search_id),
    )
    conn.commit()


def run_search(
    conn: sqlite3.Connection,
    client: GitHubClient,
    *,
    search_id: int,
    query: str,
    size_cursor: int = 0,
    size_step: int = 100,
    per_page: int = 100,
    page_delay: float = 1.0,
    window_delay: float = 1.0,
) -> SearchOutcome:
    """Sweep ``query`` across size windows, ingesting every result found."""
    cursor = max(0, size_cursor)
    step = max(MIN_STEP, size_step)
    totals = IngestResult()
    windows = 0

    while True:
        window_end = cursor + step
        windowed_query = f"{query} size:{cursor}..{window_end}"

        try:
            payload = client.search_code(windowed_query, page=1, per_page=per_page)
        except QueryTooBroad:
            if step > MIN_STEP:
                step = max(MIN_STEP, step // 2)
                log.info("Query rejected as too broad; narrowing window to %d", step)
                continue
            log.warning(
                "Size %d alone is rejected by the API; skipping to %d",
                cursor,
                cursor + 1,
            )
            cursor += 1
            _save_progress(conn, search_id, cursor, step)
            continue

        total = payload.get("total_count", 0)

        if total == 0:
            probe = client.search_code(f"{query} size:>{window_end}", page=1, per_page=1)
            if probe.get("total_count", 0) == 0:
                log.info("No results above size %d; sweep complete", window_end)
                _save_progress(conn, search_id, cursor, step)
                return SearchOutcome(totals, windows, cursor, step, completed=True)
            # Empty window but more above: widen fast to jump the gap.
            cursor = window_end + 1
            step = grow_step(step, aggressive=True)
            _save_progress(conn, search_id, cursor, step)
            log.info("Empty window; advancing to size %d (step %d)", cursor, step)
            continue

        if total > SAFE_WINDOW and step > MIN_STEP:
            step = max(MIN_STEP, step // 2)
            log.info(
                "Window %d..%d holds %d results; narrowing step to %d",
                cursor,
                window_end,
                total,
                step,
            )
            continue

        if total > RESULT_CAP:
            log.warning(
                "Size %d has %d results but only %d are reachable; some will be missed",
                cursor,
                total,
                RESULT_CAP,
            )

        log.info("Window %d..%d: %d results", cursor, window_end, total)
        windows += 1

        page_total = ingest_items(conn, search_id, payload.get("items") or [])
        conn.commit()
        totals += page_total
        fetched = page_total.total + page_total.skipped

        last_page = min(MAX_PAGES, math.ceil(min(total, RESULT_CAP) / per_page))
        for page in range(2, last_page + 1):
            sleep(page_delay)
            try:
                payload = client.search_code(
                    windowed_query, page=page, per_page=per_page
                )
            except QueryTooBroad:
                log.warning("Page %d rejected for window %d..%d", page, cursor, window_end)
                break
            items = payload.get("items") or []
            if not items:
                break
            page_total = ingest_items(conn, search_id, items)
            conn.commit()
            totals += page_total
            fetched += page_total.total + page_total.skipped
            log.info(
                "  page %d/%d: %d fetched (%d new so far)",
                page,
                last_page,
                len(items),
                totals.new_files,
            )

        cursor = window_end + 1
        if total < GROW_THRESHOLD:
            step = grow_step(step)
        _save_progress(conn, search_id, cursor, step)
        log.info(
            "Window done: %d fetched, cursor now %d (step %d)", fetched, cursor, step
        )
        sleep(window_delay)
