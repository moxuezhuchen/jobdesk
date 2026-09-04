"""Regression tests for the GUI entry point's early startup feedback."""

import sys
from types import ModuleType

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_startup_feedback_processes_paint_events(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication, QProgressBar

    from jobdesk_app.gui.app import _close_startup_feedback, _show_startup_feedback

    app = QApplication.instance()
    process_events_calls = 0
    original_process_events = QApplication.processEvents

    def record_process_events(self, *args, **kwargs):
        nonlocal process_events_calls
        process_events_calls += 1
        return original_process_events(*args, **kwargs)

    monkeypatch.setattr(QApplication, "processEvents", record_process_events)
    startup = _show_startup_feedback(app)
    qtbot.addWidget(startup)
    try:
        assert process_events_calls == 1
        assert startup.isVisible()
        assert startup.objectName() == "JobDeskStartupWindow"
        assert startup.findChild(QProgressBar, "JobDeskStartupProgress").minimum() == 0
        assert startup.findChild(QProgressBar, "JobDeskStartupProgress").maximum() == 0
    finally:
        _close_startup_feedback(startup)
        app.processEvents()


def test_main_processes_startup_paint_before_heavy_imports_and_window(qtbot, monkeypatch):
    from PySide6 import QtWidgets
    from PySide6.QtWidgets import QApplication

    from jobdesk_app.gui import app as app_module

    events = []
    qt_app = QApplication.instance()

    def installed_exception_hook(*_args):
        pass

    class RecordingModule(ModuleType):
        def __init__(self, name, attributes):
            super().__init__(name)
            self._attributes = attributes

        def __getattr__(self, name):
            if name not in self._attributes:
                raise AttributeError(name)
            events.append(f"import:{name}")
            return self._attributes[name]

    class FakeSessionPool:
        def __init__(self, *_args):
            events.append("construct:SessionPool")

    def fake_create_application(*, session_pool):
        events.append("construct:ApplicationContainer")
        return object()

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class FakeApp:
        def __init__(self):
            self.aboutToQuit = FakeSignal()

        def setStyle(self, _style):
            pass

        def setOrganizationName(self, _name):
            pass

        def setApplicationName(self, _name):
            pass

        def setFont(self, _font):
            pass

        def processEvents(self):
            events.append("processEvents")
            qt_app.processEvents()

        def primaryScreen(self):
            return qt_app.primaryScreen()

        def exec(self):
            assert sys.excepthook is installed_exception_hook
            return 0

    fake_app = FakeApp()

    class FakeApplication:
        def __new__(cls, _argv):
            return fake_app

        @staticmethod
        def instance():
            # pytest-qt consults this while monkeypatches are still active.
            return qt_app

    class FakeMainWindow:
        def __init__(self, **_kwargs):
            events.append("construct:MainWindow")

        def _make_exception_hook(self):
            return installed_exception_hook

        def shutdown(self):
            pass

        def show(self):
            events.append("show:MainWindow")

    monkeypatch.setattr(QtWidgets, "QApplication", FakeApplication)
    monkeypatch.setattr(app_module.sys, "exit", lambda _code: None)
    monkeypatch.setitem(
        sys.modules,
        "jobdesk_app.bootstrap",
        RecordingModule(
            "jobdesk_app.bootstrap",
            {
                "GuiSettingsStore": object,
                "RunMonitor": object,
                "SessionPool": FakeSessionPool,
                "create_application": fake_create_application,
                "create_sftp_client": lambda *_args: None,
                "create_ssh_client": lambda *_args: None,
            },
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "jobdesk_app.gui.main_window",
        RecordingModule("jobdesk_app.gui.main_window", {"MainWindow": FakeMainWindow}),
    )
    monkeypatch.setitem(
        sys.modules,
        "jobdesk_app.gui.session",
        RecordingModule(
            "jobdesk_app.gui.session",
            {
                "create_sftp_client": lambda: None,
                "create_ssh_client": lambda: None,
            },
        ),
    )

    previous_exception_hook = sys.excepthook
    try:
        app_module.main()
    finally:
        sys.excepthook = previous_exception_hook
        qt_app.processEvents()

    first_paint = events.index("processEvents")
    assert first_paint < events.index("import:SessionPool")
    assert first_paint < events.index("import:MainWindow")
    assert first_paint < events.index("construct:MainWindow")
    assert sys.excepthook is previous_exception_hook
