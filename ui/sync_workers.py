"""
The QThread workers behind the Update and Finalize buttons.

Lifted out of ui/Pages/History.py unchanged so a second page (the Current
Sheet) can put an Update button on screen without a second copy of the
logic -- there is exactly one implementation of "what Update does", and
both pages drive it. History.py imports these; it no longer defines them.

Every worker emits EITHER finished or failed, never neither: a page
disables its buttons when one starts and only re-enables them in those two
handlers, so an exception escaping run() would leave Update and Finalize
greyed out for the rest of the session (and the QThread never quit, since
quit is wired to those same signals). Same shape as the SharePoint workers
in History.py.
"""

from PySide6.QtCore import QObject, Signal

from sync_service import (
    update_with_other_user, finalize_month, local_update, local_finalize,
)


class UpdateWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, recipient_email, project_type):
        super().__init__()
        self.recipient_email = recipient_email
        self.project_type = project_type

    def run(self):
        try:
            result = update_with_other_user(
                self.recipient_email, project_type=self.project_type,
                progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class LocalUpdateWorker(QObject):
    """Sync-off equivalent of UpdateWorker: no recipient email, no
    pull/push, just a local inbox scan (sync_service.local_update) plus a
    top-up of the active export sheet.

    project_type is threaded through deliberately. Without it
    rebuild_active_export runs unfiltered, so with the Sync switch OFF the
    Food & Beverage / Hospitality toggle silently does nothing -- the
    division is chosen on screen and then ignored. This is the one
    behavioural change main made to its copy of this class, carried over
    here when the two were merged into this single shared definition."""
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, project_type=None):
        super().__init__()
        self.project_type = project_type

    def run(self):
        try:
            result = local_update(
                project_type=self.project_type, progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class FinalizeWorker(QObject):
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, recipient_email, start_date, end_date, project_type):
        super().__init__()
        self.recipient_email = recipient_email
        self.start_date = start_date
        self.end_date = end_date
        self.project_type = project_type

    def run(self):
        try:
            result = finalize_month(
                self.recipient_email, self.start_date, self.end_date,
                project_type=self.project_type, progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class LocalFinalizeWorker(QObject):
    """Sync-off equivalent of FinalizeWorker: no recipient email, no
    pull/notify, just a local scan + closing out the active export sheet
    (sync_service.local_finalize). As with FinalizeWorker there is no
    output_path -- the file closed is whichever one Update has been
    filling."""
    progress = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, start_date, end_date, project_type):
        super().__init__()
        self.start_date = start_date
        self.end_date = end_date
        self.project_type = project_type

    def run(self):
        try:
            result = local_finalize(
                self.start_date, self.end_date,
                project_type=self.project_type, progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)
