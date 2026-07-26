"""
When one email carries two timecard PDFs covering two DIFFERENT periods,
each attachment's day-entries must be tagged with THAT attachment's own
period -- not have the second PDF's days silently inherit the first PDF's
period (which is what a single email-wide _merge_header_fields did).

A source text that carries its own "Period Person Number" header line uses
its own value; a source that lacks one (typically the plain-text body)
still borrows the email-level merged value, so the original anti-duplication
reason for merging -- a body day-entry getting a blank person_number and
being stored as a separate row from the attachment's same-day entry -- is
preserved.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import extractor_service as ex


# Minimal Oracle-timecard-shaped text: one "Period Person Number Time Card
# Status" header line + one day block carrying hours/project/task, which is
# what _header_fields / _day_blocks / _parse_block key off of.
def _timecard_text(period, person, day):
    return (
        "Period Person Number Time Card Status\n"
        f"{period} {person} Approved\n"
        f"{day}\n"
        "Contractor Labor - ORCL\n"
        "AE - Straight Time\n"
        "8.00 Hours\n"
        "400380981 - HLGIU-EMEA-Motel One-OPERA Cloud rollout Task\n"
        "1.01.00 - FP Labor -Billable Cost\n"
    )


class _FakeMail:
    Body = ""
    ReceivedTime = "2026-07-26 12:15:11+00:00"
    SenderEmailType = None
    SenderEmailAddress = "sender@example.com"


def _email_with_attachment_texts(texts):
    return {
        "mail_item": _FakeMail(),
        "subject": "Fwd: FW: Timecard& expenses.",
        "sender": "sender@example.com",
        "status": "Approved",
        "attachments": [{"matches_keyword": True, "text": t} for t in texts],
    }


class MultiTimecardPeriodTests(unittest.TestCase):
    def test_two_attachments_keep_their_own_periods(self):
        card1 = _timecard_text("3/7/26 - 3/13/26", "1960153", "Saturday, 07 Mar")
        card2 = _timecard_text("2/28/26 - 3/6/26", "1960153", "Wednesday, 04 Mar")

        entries = ex.extract(_email_with_attachment_texts([card1, card2]))

        by_day = {e["day"]: e for e in entries}
        self.assertEqual(len(entries), 2, entries)
        self.assertEqual(by_day["Saturday, 07 Mar"]["period"], "3/7/26 - 3/13/26")
        # The regression: this used to be "3/7/26 - 3/13/26" too.
        self.assertEqual(by_day["Wednesday, 04 Mar"]["period"], "2/28/26 - 3/6/26")

    def test_body_without_a_header_borrows_the_attachment_fields(self):
        """A source lacking its own Period/Person header still inherits the
        email-level merged values, so its day-entry lines up with (dedupes
        against) the attachment's same-day entry instead of getting a blank
        person_number."""
        attachment = _timecard_text("3/7/26 - 3/13/26", "1960153", "Saturday, 07 Mar")
        # Body-style text: a day block but no "Period Person Number" header line.
        body_only = (
            "Monday, 09 Mar\n"
            "Contractor Labor - ORCL - AE - Straight Time\n"
            "8.00 Hours\n"
            "400380981 - HLGIU-EMEA-Motel One-OPERA Cloud rollout Task\n"
            "1.01.00 - FP Labor -Billable Cost\n"
        )
        email = _email_with_attachment_texts([attachment])
        email["attachments"].append({"matches_keyword": True, "text": body_only})

        entries = ex.extract(email)
        by_day = {e["day"]: e for e in entries}
        self.assertEqual(by_day["Monday, 09 Mar"]["person_number"], "1960153")
        self.assertEqual(by_day["Monday, 09 Mar"]["period"], "3/7/26 - 3/13/26")


if __name__ == "__main__":
    unittest.main()
