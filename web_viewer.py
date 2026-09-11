#!/usr/bin/env python3
"""Web viewer entry point. The implementation lives in :mod:`ctrlfvuln.web`."""

from ctrlfvuln.config import settings
from ctrlfvuln.web import create_app

app = create_app(settings)

if __name__ == "__main__":
    # Binds to localhost by default: the viewer can clone repositories and
    # launch an editor, so it must not be exposed on a shared interface.
    app.run(host=settings.host, port=settings.port, debug=settings.debug)
