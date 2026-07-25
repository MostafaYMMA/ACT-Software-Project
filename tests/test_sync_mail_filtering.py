"""
services/filter_service.py's timecard/expense matching must never match
this app's own private cross-device sync mail (see
services/outlook_service.py's _SUBJECT_PATTERN) -- its payload attachment
is a real .xlsx whose "Status" column literally contains the words
"Approved"/"Pending"/"Rejected" and whose headers contain "Project", which
would otherwise trip the loose timecard attachment-keyword match and the
three-term expense match respectively.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import filter_service


class _ExplodingAttachments:
    """Raises if anything tries to enumerate attachments -- proof the
    sync-mail guard exits before ever opening one."""
    @property
    def Count(self):
        raise AssertionError("attachments were opened for a sync-mail item")


class _EmptyAttachments:
    Count = 0


class _FakeMailItem:
    def __init__(self, subject, body="", attachments=None):
        self.Subject = subject
        self.Body = body
        self.UnRead = False
        self.SenderName = "someone@example.com"
        self.ReceivedTime = "2026-07-25"
        self.Class = 43
        self.Attachments = attachments if attachments is not None else _EmptyAttachments()


_SYNC_SUBJECTS = [
    "ACT-SYNC v1 | snapshot | 81ee9cd425a9 | seq=1",
    "ACT-SYNC v1 | rate | 81ee9cd425a9 | seq=4",
    "ACT-SYNC v1 | finalize | 81ee9cd425a9 | seq=9",
    "act-sync v1 | snapshot | 81ee9cd425a9 | seq=1",  # case-insensitive
]


class SyncMailFilteringTests(unittest.TestCase):
    def test_is_sync_mail_subject_recognizes_real_subjects(self):
        for subject in _SYNC_SUBJECTS:
            with self.subTest(subject=subject):
                self.assertTrue(filter_service.is_sync_mail_subject(subject))

    def test_is_sync_mail_subject_rejects_real_timecard_subjects(self):
        for subject in ("Time Card Status - Approved", "FW: FYI: Approved Timecard", ""):
            with self.subTest(subject=subject):
                self.assertFalse(filter_service.is_sync_mail_subject(subject))

    def test_process_email_never_matches_sync_mail_even_with_a_matching_attachment(self):
        """The attachment carries the literal word "Approved" (as it would
        for a real sync payload) -- if the guard didn't fire first, this
        would trip the loose keyword match. ExplodingAttachments proves the
        guard exits before Attachments is ever touched at all."""
        counters = filter_service.Counters()
        item = _FakeMailItem(
            "ACT-SYNC v1 | snapshot | 81ee9cd425a9 | seq=1",
            body="This is an automated sync message from the ACT timecard app.",
            attachments=_ExplodingAttachments(),
        )
        result = filter_service.process_email(item, temp_dir=".", counters=counters)
        self.assertIsNone(result)
        self.assertEqual(counters.total_emails, 0)

    def test_process_email_expense_never_matches_sync_mail(self):
        item = _FakeMailItem(
            "ACT-SYNC v1 | snapshot | 81ee9cd425a9 | seq=1",
            attachments=_ExplodingAttachments(),
        )
        result = filter_service.process_email_expense(item, temp_dir=".")
        self.assertIsNone(result)

    def test_process_email_still_matches_a_real_timecard_email(self):
        """The guard must not swallow real matches."""
        counters = filter_service.Counters()
        item = _FakeMailItem(
            "Time Card Status - Approved",
            body="This timecard has been approved. Please review.",
        )
        result = filter_service.process_email(item, temp_dir=".", counters=counters)
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()
