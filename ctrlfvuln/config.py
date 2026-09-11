"""Central configuration, resolved once from the environment and optional .env file.

Every tunable the CLI and the web viewer share lives here so that neither module
hardcodes a database filename, an editor path, or a rate-limit delay.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

from decouple import AutoConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# AutoConfig tolerates a missing .env file; a bare Config(repository=None) raises
# TypeError on every lookup, which is how the previous version crashed when the
# token was neither passed on the command line nor present in a .env file.
_env = AutoConfig(search_path=str(PROJECT_ROOT))

#: Commands tried, in order, when no editor is configured explicitly.
EDITOR_CANDIDATES = ("code", "code-insiders", "codium", "vscodium", "subl", "zed")


def resolve_editor(configured: str | None) -> str | None:
    """Return a runnable editor command, or None when none can be found.

    A configured value is returned even if it is not on PATH so that an explicit
    setting produces a real error message instead of being silently ignored.
    """
    if configured:
        return shutil.which(configured) or configured
    for candidate in EDITOR_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


@dataclass(frozen=True)
class Settings:
    """Immutable settings bundle. Use :meth:`from_env` to build, ``replace`` to override."""

    db_path: Path
    projects_dir: Path
    env_path: Path
    github_token: str | None
    editor_command: str | None
    host: str
    port: int
    debug: bool
    log_level: str
    secret_key: str | None
    page_size: int
    count_cap: int
    preview_max_bytes: int
    http_timeout: float
    augment_delay: float
    search_page_delay: float
    rate_limit_delay: float
    clone_depth: int

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=Path(
                _env("CTRLF_DB_PATH", default=str(PROJECT_ROOT / "ctrl_f_vuln.db"))
            ).expanduser(),
            projects_dir=Path(
                _env("CTRLF_PROJECTS_DIR", default=str(PROJECT_ROOT / "projects"))
            ).expanduser(),
            env_path=PROJECT_ROOT / ".env",
            github_token=_env("GITHUB_TOKEN", default=None) or None,
            editor_command=resolve_editor(_env("CTRLF_EDITOR", default=None) or None),
            host=_env("CTRLF_HOST", default="127.0.0.1"),
            port=_env("CTRLF_PORT", default=5000, cast=int),
            debug=_env("CTRLF_DEBUG", default=False, cast=bool),
            log_level=_env("CTRLF_LOG_LEVEL", default="INFO").upper(),
            secret_key=_env("CTRLF_SECRET_KEY", default=None) or None,
            page_size=_env("CTRLF_PAGE_SIZE", default=100, cast=int),
            count_cap=_env("CTRLF_COUNT_CAP", default=10_000, cast=int),
            preview_max_bytes=_env("CTRLF_PREVIEW_MAX_BYTES", default=512_000, cast=int),
            http_timeout=_env("CTRLF_HTTP_TIMEOUT", default=20.0, cast=float),
            augment_delay=_env("CTRLF_AUGMENT_DELAY", default=0.75, cast=float),
            search_page_delay=_env("CTRLF_SEARCH_PAGE_DELAY", default=1.0, cast=float),
            rate_limit_delay=_env("CTRLF_RATE_LIMIT_DELAY", default=60.0, cast=float),
            clone_depth=_env("CTRLF_CLONE_DEPTH", default=1, cast=int),
        )

    def with_overrides(self, **kwargs) -> Settings:
        """Return a copy with the given fields replaced (used by tests)."""
        return replace(self, **kwargs)


settings = Settings.from_env()

ENV_TEMPLATE = """\
# GitHub API token used for code search, repository augmentation and file previews.
GITHUB_TOKEN=your_github_token_here

# Optional overrides (defaults shown).
# CTRLF_DB_PATH=ctrl_f_vuln.db
# CTRLF_PROJECTS_DIR=projects
# CTRLF_EDITOR=code
# CTRLF_HOST=127.0.0.1
# CTRLF_PORT=5000
# CTRLF_DEBUG=False
# CTRLF_LOG_LEVEL=INFO
# CTRLF_PAGE_SIZE=100
# CTRLF_AUGMENT_DELAY=0.75
# CTRLF_CLONE_DEPTH=1
"""
