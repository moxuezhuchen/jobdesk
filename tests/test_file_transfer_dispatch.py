"""Thread-affinity regression tests for the Files-page callback boundary."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QThread  # noqa: E402

from jobdesk_app.gui.pages.file_transfer_dispatch import GuiThreadDispatcher  # noqa: E402


def test_dispatcher_delivers_worker_post_on_owner_qt_thread(qtbot):
    """A callback posted from a real Python worker runs on the GUI thread."""
    dispatcher = GuiThreadDispatcher()
    owner_ident = threading.get_ident()
    owner_qthread = QThread.currentThread()
    callback_idents: list[int] = []
    callback_threads: list[QThread] = []
    callback_done = threading.Event()

    def callback() -> None:
        callback_idents.append(threading.get_ident())
        callback_threads.append(QThread.currentThread())
        callback_done.set()

    worker = threading.Thread(target=lambda: dispatcher.post(callback), name="files-dispatch-test")
    worker.start()
    qtbot.waitUntil(callback_done.is_set, timeout=2000)
    worker.join(timeout=2)

    assert callback_idents == [owner_ident]
    assert callback_threads[0] is owner_qthread
    dispatcher.deleteLater()
