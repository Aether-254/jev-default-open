"""Environment boundary for child processes started by Jev.

Jev needs its own API credentials to call the decision service.  Candidate
applications and the native transport host do not need those credentials, so
they receive a copy of the environment with known secret-bearing variables
removed.  The values are never inspected or logged.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

_CREDENTIAL_ENV_NAMES = frozenset(
    {
        "TYPESAFE_API_KEY",
        "TYPESAFE_BASE_URL",
        "TYPESAFE_MODEL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NPM_TOKEN",
    }
)


def sanitized_child_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a copy suitable for an opener or native host child process."""
    environment = dict(os.environ if source is None else source)
    for name in tuple(environment):
        upper_name = name.upper()
        if (
            upper_name in _CREDENTIAL_ENV_NAMES
            or upper_name.endswith(
                ("_API_KEY", "_ACCESS_TOKEN", "_TOKEN", "_SECRET", "_PASSWORD")
            )
        ):
            environment.pop(name, None)
    return environment
