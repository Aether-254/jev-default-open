class JevOpenError(Exception):
    """Base exception for expected module failures."""


class DeadlineExceeded(JevOpenError):
    """A request exceeded its end-to-end decision deadline."""


class NoCompatibleAction(JevOpenError):
    """No compatible Open Action exists for a target."""


class ProviderUnavailable(JevOpenError):
    """An IM context provider cannot resolve context for this request."""


class LaunchFailed(JevOpenError):
    """The selected Open Action could not be invoked."""
