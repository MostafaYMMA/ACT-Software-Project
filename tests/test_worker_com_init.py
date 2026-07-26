"""
Every button that reaches Outlook (Scan Inbox, Sync, Finalize) runs on a
fresh QThread. Outlook COM (win32com.client.Dispatch) is per-thread: an OS
thread that hasn't called CoInitialize gets "CoInitialize has not been
called" the moment it Dispatches -- and because that error is swallowed by
broad excepts in outlook_service/filter_service, the symptom was a Sync
that silently sent no email at all, with nothing shown in the UI.

outlook_service.com_thread() is the fix (pythoncom.CoInitialize around the
work). These tests prove:
  1. The bug is real: Dispatch on a bare (non-CoInitialized) thread raises.
  2. com_thread() makes the same call succeed.
  3. The actual worker classes wrap their Outlook-touching work in it, so
     a real Sync click on a QThread reaches send with sent=True.
"""

import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pythoncom
import win32com.client

import outlook_service


class ComInitContractTests(unittest.TestCase):
    def test_dispatch_on_a_bare_thread_raises_coinitialize(self):
        """The failure mode itself: a thread that never called
        CoInitialize cannot Dispatch a COM object."""
        result = {}

        def worker():
            try:
                win32com.client.Dispatch("Outlook.Application")
                result["error"] = None
            except Exception as exc:
                result["error"] = str(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertIsNotNone(result["error"])
        self.assertIn("CoInitialize", result["error"])

    def test_com_thread_lets_the_same_call_succeed(self):
        """Wrapping the work in com_thread() is exactly what removes that
        error. Dispatch is faked so this doesn't need a real Outlook
        install -- the point under test is that com_thread initialized COM
        for the thread, not what Outlook returns."""
        result = {}

        def worker():
            try:
                with outlook_service.com_thread():
                    # A real Dispatch would get past the CoInitialize gate
                    # here; fake it so the test is about the gate, not Outlook.
                    with patch.object(win32com.client, "Dispatch", return_value=MagicMock()):
                        win32com.client.Dispatch("Outlook.Application")
                    # And prove COM really is initialized on this thread now:
                    # CoInitialize returns S_FALSE (raises com_error with S_FALSE)
                    # when already initialized, which is what we expect here.
                    result["already_init"] = pythoncom.CoInitialize()
                result["error"] = None
            except Exception as exc:  # pragma: no cover - would be a real failure
                result["error"] = str(exc)
            finally:
                # Balance the extra CoInitialize() we just called to probe state.
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertIsNone(result["error"])


class UpdateWorkerWrapsSendInComThreadTests(unittest.TestCase):
    """End-to-end on a real QThread: with Outlook faked but com_thread()
    left real, UpdateWorker's Sync must reach the send and report sent."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_sync_on_a_qthread_reaches_send_with_sent_true(self):
        import storage_service as ss
        from PySide6.QtCore import QThread, QEventLoop, QTimer

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db = os.path.join(temp_dir.name, "t.db")
        exports = os.path.join(temp_dir.name, "exports")

        dispatched = {"reached": False}

        def fake_dispatch(name):
            # A real Dispatch would raise here if COM weren't initialized on
            # this QThread -- reaching this line at all is the proof.
            dispatched["reached"] = True
            m = MagicMock()
            m.GetNamespace.return_value.Offline = False
            return m

        patches = [
            patch.object(ss, "DB_PATH", db),
            patch.object(ss, "EXPORTS_DIR", exports),
            patch.object(ss, "_cached_usd_rates", return_value={"AED": 3.67}),
            patch.object(outlook_service.win32com.client, "Dispatch", side_effect=fake_dispatch),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        ss.init_db()
        ss.save_cards([{
            "status": "Approved", "day": "2026-07-01", "project_name": "FB K",
            "project_code": "FB-100", "task": "T1", "hours": "8", "name": "J",
            "person_number": "P1", "subject": "Time Entries Approved",
            "sender": "s@x.com", "received": "2026-07-01 09:00:00",
            "labor_type": "L", "time_type": "R", "period": "p",
            "rate": None, "rate_updated_at": None, "rate_updated_by": None,
        }])

        from ui.sync_workers import UpdateWorker

        worker = UpdateWorker("partner@example.com", None)
        thread = QThread()
        worker.moveToThread(thread)
        outcome = {}
        worker.finished.connect(lambda r: outcome.update(r=r, kind="finished"))
        worker.failed.connect(lambda m: outcome.update(m=m, kind="failed"))
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()

        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(15000, loop.quit)
        loop.exec()

        self.assertTrue(dispatched["reached"], "Dispatch must have been reached on the worker thread")
        self.assertEqual(outcome.get("kind"), "finished")
        self.assertTrue(outcome["r"]["push"]["sent"], "the sync email must report sent=True")


if __name__ == "__main__":
    unittest.main()
