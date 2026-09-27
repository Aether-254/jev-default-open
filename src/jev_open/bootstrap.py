from __future__ import annotations

from .broker import BrokerApplication


def build_application() -> BrokerApplication:
    """Composition root for concrete adapters.

    Keep dependency construction here so modules accept dependencies instead of
    creating them internally.

    # TODO: Load AppConfig and environment-only Jev credentials.
    # TODO: Construct SQLCipherStateStore with a DPAPI-unwrapped key.
    # TODO: Register QQ, WeChat, and fixture ChatContextProvider adapters.
    # TODO: Construct the native hook controller and action catalog.
    # TODO: Construct the Qt UI adapter.
    """
    raise NotImplementedError("TODO: compose application adapters")
