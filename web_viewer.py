#!/usr/bin/env python3
"""Web viewer entry point. The implementation lives in :mod:`ctrlfvuln.web`.

Run directly for the development server, or point a WSGI server at
``web_viewer:app`` (the app is built on first attribute access, so importing
this module has no side effects).
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import sys
from pathlib import Path

from ctrlfvuln import logging_setup
from ctrlfvuln.config import settings as default_settings
from ctrlfvuln.web import create_app

log = logging.getLogger(__name__)

MAX_PORT = 65535


def is_loopback(host: str) -> bool:
    """True when ``host`` only accepts connections from this machine."""
    if not host:
        # An empty host makes Werkzeug listen on every interface.
        return False
    if host.lower() in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname we cannot classify; assume it is reachable.
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_viewer.py",
        description="Serve the ctrl-f-vuln triage interface.",
        epilog=(
            "Defaults come from CTRLF_HOST, CTRLF_PORT, CTRLF_DEBUG and "
            "CTRLF_DB_PATH in the environment or .env."
        ),
    )
    parser.add_argument(
        "--host",
        "--interface",
        dest="host",
        metavar="ADDRESS",
        help=(
            "Interface to bind. Use 0.0.0.0 for every IPv4 interface or :: for "
            f"every interface (default: {default_settings.host})."
        ),
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        metavar="PORT",
        help=f"Port to listen on; 0 picks a free one (default: {default_settings.port}).",
    )
    parser.add_argument("--db", metavar="PATH", help="SQLite database path.")
    debug = parser.add_mutually_exclusive_group()
    debug.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        default=None,
        help="Enable the Flask reloader and debugger (localhost only).",
    )
    debug.add_argument(
        "--no-debug", dest="debug", action="store_false", help="Disable debug mode."
    )
    parser.add_argument(
        "--allow-unsafe-debug",
        action="store_true",
        help=argparse.SUPPRESS,  # Escape hatch; see _check_exposure.
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging."
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _check_exposure(host: str, debug: bool, allow_unsafe_debug: bool) -> str | None:
    """Warn about a non-loopback bind. Returns an error message if it must not proceed.

    The viewer has no authentication and can clone repositories and launch an
    editor on this machine, so who can reach it matters.
    """
    if is_loopback(host):
        return None

    if debug and not allow_unsafe_debug:
        return (
            f"Refusing to run in debug mode on {host}: the Werkzeug debugger lets "
            "anyone who can reach this port execute code on this machine. Drop "
            "--debug, bind to 127.0.0.1, or pass --allow-unsafe-debug if you "
            "really mean it."
        )

    log.warning(
        "Listening on %s, which is reachable from outside this machine. The "
        "viewer has no authentication and can clone repositories and open an "
        "editor here, so restrict access with a firewall or use an SSH tunnel "
        "instead.",
        host,
    )
    if debug:
        log.warning(
            "Debug mode is on for a non-local interface; the debugger permits "
            "remote code execution."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    settings = default_settings
    overrides: dict[str, object] = {}
    if args.db:
        overrides["db_path"] = Path(args.db).expanduser()
    if args.verbose:
        overrides["log_level"] = "DEBUG"
    if overrides:
        settings = settings.with_overrides(**overrides)

    logging_setup.configure(settings.log_level)

    host = args.host if args.host is not None else settings.host
    port = args.port if args.port is not None else settings.port
    debug = settings.debug if args.debug is None else args.debug

    if not 0 <= port <= MAX_PORT:
        log.error("Port %d is out of range (0-%d).", port, MAX_PORT)
        return 2

    problem = _check_exposure(host, debug, args.allow_unsafe_debug)
    if problem:
        log.error("%s", problem)
        return 2

    display_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    log.info("Serving ctrl-f-vuln on http://%s:%d/ (database: %s)",
             display_host, port, settings.db_path)

    create_app(settings).run(host=host, port=port, debug=debug)
    return 0


def __getattr__(name: str):
    """Build the WSGI app on demand so importing this module stays side-effect free."""
    if name == "app":
        application = create_app(default_settings)
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    sys.exit(main())
