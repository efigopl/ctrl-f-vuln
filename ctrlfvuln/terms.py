"""Reduce a GitHub code-search query to the literal terms worth highlighting.

The viewer highlights these inside previewed files, so qualifiers such as
``language:php`` or the ``size:0..100`` window the search appends must be
dropped -- they never appear in the file body.
"""

from __future__ import annotations

import re
import shlex

QUALIFIER_RE = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*:", re.ASCII)
BOOLEAN_WORDS = frozenset({"AND", "OR", "NOT"})
MIN_TERM_LENGTH = 2


def extract_terms(query: str | None, *, min_length: int = MIN_TERM_LENGTH) -> list[str]:
    """Return the highlightable literals in ``query``, order preserved, deduped."""
    if not query:
        return []

    try:
        # Non-POSIX mode keeps backslashes intact, which matters for the
        # regex-style queries GitHub accepts; quotes are stripped below instead.
        tokens = shlex.split(query, posix=False)
    except ValueError:
        # Unbalanced quotes in a stored query should not break the page.
        tokens = query.split()

    terms: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token or QUALIFIER_RE.match(token) or token.upper() in BOOLEAN_WORDS:
            continue
        # GitHub's /regex/ form: keep the body, it is usually a literal fragment.
        if len(token) > 2 and token.startswith("/") and token.endswith("/"):
            token = token[1:-1]
        token = token.strip("\"'")
        if len(token) < min_length:
            continue
        key = token.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(token)
    return terms


def extract_terms_from_queries(queries: list[str] | None) -> list[str]:
    """Flatten :func:`extract_terms` across several stored queries."""
    terms: list[str] = []
    seen: set[str] = set()
    for query in queries or []:
        for term in extract_terms(query):
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                terms.append(term)
    return terms
