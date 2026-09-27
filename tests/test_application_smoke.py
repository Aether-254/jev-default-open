"""Synthetic startup checks; never enable hooks or inspect an IM window."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from jev_open.bootstrap import build_application
from jev_open.config import AppConfig
from jev_open.demo import run_demo
from jev_open.ui.qt import QtUserInterfaceModule


def test_composition_opens_encrypted_state_but_does_not_activate_providers_or_hooks(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = AppConfig(state_directory=tmp_path / "fresh-state")
    broker = build_application(config)
    assert not broker.enabled
    assert not broker.interception.is_active()
    assert not broker.context._providers
    assert broker.decision._cache_store is broker.state
    assert (config.state_directory / "state.db").read_bytes()[:16] != b"SQLite format 3\0"


def test_complete_offline_demo_closes_without_hooks_network_or_real_launch(
    tmp_path, monkeypatch
):
    application = QApplication.instance() or QApplication([])
    instances = []
    original = QtUserInterfaceModule.bind

    def bind(self, **kwargs):
        instances.append(self)
        return original(self, **kwargs)

    monkeypatch.setattr(QtUserInterfaceModule, "bind", bind)
    observed = []

    def inspect_and_close():
        if instances and instances[0]._window is not None:
            window = instances[0]._window
            observed.append(len(window.rows))
            window.cancel_all()
        application.quit()

    QTimer.singleShot(1600, inspect_and_close)
    assert run_demo(AppConfig(state_directory=tmp_path)) == 0
    assert observed == [2]
    assert not instances[0]._broker.enabled
