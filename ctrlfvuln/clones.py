"""Local clone locations and editor launching.

Clone directories are namespaced by owner. The previous layout used only the
repository's short name, so ``alice/utils`` and ``bob/utils`` resolved to the
same directory -- the second repo appeared "already cloned" and opened the
first one's code.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
CLONE_TIMEOUT = 600.0


class CloneError(RuntimeError):
    """A clone or editor launch could not be completed."""


def safe_component(value: str) -> str:
    """Validate one path segment that came from GitHub metadata."""
    if not value or value in (".", "..") or not _SAFE_COMPONENT.match(value):
        raise CloneError(f"Refusing to use unsafe path component: {value!r}")
    return value


def sanitize_component(value: str) -> str:
    """Coerce a user-chosen name (a project) into a safe single segment."""
    cleaned = _UNSAFE_CHARS.sub("_", (value or "").strip())
    cleaned = cleaned.strip(".") or "unnamed"
    return safe_component(cleaned)


def split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, name = (full_name or "").partition("/")
    if not owner or not name:
        raise CloneError(f"Malformed repository name: {full_name!r}")
    return safe_component(owner), safe_component(name)


def clone_dir(projects_dir: Path | str, project_name: str, full_name: str) -> Path:
    """Canonical clone location: ``<projects>/<project>/<owner>/<repo>``."""
    owner, name = split_full_name(full_name)
    return Path(projects_dir) / sanitize_component(project_name) / owner / name


def legacy_clone_dir(projects_dir: Path | str, project_name: str, full_name: str) -> Path:
    """Pre-0.2 location, kept so existing clones are still detected."""
    return Path(projects_dir) / project_name / (full_name or "").split("/")[-1]


def find_existing_clone(
    projects_dir: Path | str, project_name: str, full_name: str
) -> Path | None:
    """Return the directory holding an existing clone, checking both layouts."""
    candidates = []
    try:
        candidates.append(clone_dir(projects_dir, project_name, full_name))
    except CloneError:
        pass
    candidates.append(legacy_clone_dir(projects_dir, project_name, full_name))
    for candidate in candidates:
        if (candidate / ".git").is_dir():
            return candidate
    return None


def clone_repository(html_url: str, target: Path, *, depth: int = 1) -> Path:
    """Clone ``html_url`` into ``target``. A shallow clone by default.

    Triage only ever reads the current working tree, so fetching full history
    is wasted time and disk for most repositories.
    """
    if (target / ".git").is_dir():
        log.debug("Clone already present at %s", target)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone"]
    if depth and depth > 0:
        command += ["--depth", str(depth), "--single-branch"]
    command += [html_url, str(target)]

    log.info("Cloning %s into %s", html_url, target)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CloneError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"Clone timed out after {CLONE_TIMEOUT:.0f}s") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise CloneError(detail[-1] if detail else f"git exited {result.returncode}")
    return target


def open_in_editor(editor_command: str | None, target: Path) -> None:
    """Launch the configured editor on ``target`` without blocking the request."""
    if not editor_command:
        raise CloneError(
            "No editor configured. Set CTRLF_EDITOR in .env to your editor command "
            "(for example 'code')."
        )
    try:
        subprocess.Popen(
            [editor_command, str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise CloneError(
            f"Editor command {editor_command!r} was not found on PATH."
        ) from exc
    except OSError as exc:
        raise CloneError(f"Could not launch {editor_command!r}: {exc}") from exc
