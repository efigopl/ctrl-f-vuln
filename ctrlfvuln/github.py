"""Thin, rate-limit-aware GitHub REST client.

Shared by the CLI (code search, star augmentation) and the web viewer (file
preview) so the retry and rate-limit rules exist in exactly one place.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
MAX_ATTEMPTS = 5


class GitHubError(RuntimeError):
    """Any non-recoverable GitHub API failure."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class QueryTooBroad(GitHubError):
    """HTTP 422 from the search endpoint: the result window must be narrowed."""


class GitHubClient:
    """Minimal wrapper over ``requests`` with retry and rate-limit backoff."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 20.0,
        rate_limit_delay: float = 60.0,
        session: requests.Session | None = None,
    ):
        self.token = token
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.session = session or requests.Session()

    def headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _retry_delay(response: requests.Response) -> float | None:
        """Seconds to wait before retrying, or None if this is not a rate limit."""
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass
        if response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            if reset:
                try:
                    return max(1.0, float(reset) - time.time() + 1.0)
                except ValueError:
                    pass
        # 403 without rate-limit headers is usually the secondary (abuse) limit.
        if response.status_code in (403, 429):
            return None if response.status_code == 403 else 60.0
        return None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        accept: str = "application/vnd.github+json",
        allow_404: bool = False,
    ) -> requests.Response | None:
        """Perform a request, retrying transient failures and rate limits.

        Returns None when ``allow_404`` and the resource is absent.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    headers=self.headers(accept),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                delay = min(self.rate_limit_delay, 2.0**attempt)
                log.warning(
                    "Network error on %s (attempt %d/%d): %s; retrying in %.0fs",
                    url,
                    attempt,
                    MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)
                continue

            if response.status_code == 200:
                return response
            if response.status_code == 404 and allow_404:
                return None
            if response.status_code == 422:
                raise QueryTooBroad(
                    f"GitHub rejected the query: {response.text[:200]}", status=422
                )
            if response.status_code in (403, 429):
                delay = self._retry_delay(response) or self.rate_limit_delay
                log.warning(
                    "Rate limited on %s (HTTP %d); sleeping %.0fs",
                    url,
                    response.status_code,
                    delay,
                )
                time.sleep(delay)
                continue
            if 500 <= response.status_code < 600:
                delay = min(self.rate_limit_delay, 2.0**attempt)
                log.warning(
                    "GitHub server error %d on %s; retrying in %.0fs",
                    response.status_code,
                    url,
                    delay,
                )
                time.sleep(delay)
                continue

            raise GitHubError(
                f"GitHub API error {response.status_code} for {url}: "
                f"{response.text[:200]}",
                status=response.status_code,
            )

        raise GitHubError(f"Gave up on {url} after {MAX_ATTEMPTS} attempts: {last_error}")

    def search_code(self, query: str, *, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        """Run a code search. Raises :class:`QueryTooBroad` on HTTP 422."""
        response = self.request(
            "GET",
            f"{API_ROOT}/search/code",
            params={"q": query, "per_page": per_page, "page": page},
        )
        assert response is not None  # allow_404 is False, so 200 or raise.
        return response.json()

    def get_repo(self, full_name: str) -> dict[str, Any] | None:
        """Fetch repository metadata, or None when it no longer exists."""
        response = self.request(
            "GET", f"{API_ROOT}/repos/{full_name}", allow_404=True
        )
        return response.json() if response is not None else None

    def get_blob_text(
        self, git_url: str, *, max_bytes: int = 512_000
    ) -> tuple[str, bool]:
        """Return ``(text, truncated)`` for a blob URL stored on a file row.

        Blobs are addressed by sha, so the content matches what the search saw
        even if the branch has moved on since.
        """
        response = self.request("GET", git_url, allow_404=True)
        if response is None:
            raise GitHubError("File no longer exists on GitHub", status=404)

        payload = response.json()
        encoding = payload.get("encoding")
        content = payload.get("content") or ""

        if encoding == "base64":
            raw = base64.b64decode(content)
        elif encoding in (None, "utf-8", "none"):
            raw = content.encode("utf-8", errors="replace")
        else:
            raise GitHubError(f"Unsupported blob encoding: {encoding}")

        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        return raw.decode("utf-8", errors="replace"), truncated

    def get_raw_text(self, url: str, *, max_bytes: int = 512_000) -> tuple[str, bool]:
        """Fetch a contents URL as raw bytes. Fallback for rows without a blob URL."""
        response = self.request(
            "GET", url, accept="application/vnd.github.raw", allow_404=True
        )
        if response is None:
            raise GitHubError("File no longer exists on GitHub", status=404)
        raw = response.content
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        return raw.decode("utf-8", errors="replace"), truncated
