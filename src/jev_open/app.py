from __future__ import annotations

from .bootstrap import build_application


def run() -> int:
    """Application entry point.

    # TODO: Re-launch with runas when the broker is not elevated.
    # TODO: Enter safe mode after an unclean shutdown.
    # TODO: Start Qt only after configuration and state-store validation.
    """
    application = build_application()
    return application.run()
