import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

import storage_service


def _entry(status, project_name, project_number, day, person_number, received="2026-07-05 10:00:00"):
    return {
        "status": status,
        "day": day,
        "project_name": project_name,
        "project_code": project_number,
        "task": "T1",
        "hours": "8",
        "name": "Jane",
        "person_number": person_number,
        "subject": f"Time Entries {status}",
        "sender": "s@x.com",
        "received": received,
        "labor_type": "L",
        "time_type": "R",
        "period": "2026-06-29 to 2026-07-05",
    }


class ProjectTypeTableTests(unittest.TestCase):
    """
    Covers the split of live work into the two project-type tables: an entry
    whose project name starts with FB belongs to Food & Beverage, one starting
    with HL to Hospitality, and only the Approved and Pending statuses feed
    them (a rejected entry is in neither division's book of work).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = os.path.join(self.temp_dir.name, "cards_test.db")

        self.db_path_patcher = patch.object(storage_service, "DB_PATH", self.db_path)
        self.db_path_patcher.start()
        self.addCleanup(self.db_path_patcher.stop)

        storage_service.init_db()
        storage_service.save_cards([
            _entry("Approved", "FBGIU NA Bevco Bottling", "B100", "Monday", "1"),
            _entry("Pending", "FBGIU EMEA Brew House", "B200", "Tuesday", "2"),
            _entry("Approved", "HLGIU NA Hilton Downtown", "H100", "Monday", "3"),
            # Lower-case, to pin down that the routing is case-insensitive.
            _entry("Pending", "hlgiu emea Hyatt Place", "H200", "Tuesday", "4"),
            _entry("Rejected", "FBGIU NA Bevco Bottling", "B100", "Friday", "5"),
            _entry("Approved", "Acme Corp", "A100", "Monday", "6"),
        ])

    def _table_contents(self, table):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(f'SELECT status, "Project Name" FROM "{table}"').fetchall()
        finally:
            conn.close()
        return sorted(rows)

    def test_beverage_table_holds_approved_and_pending_b_projects(self):
        self.assertEqual(
            self._table_contents("timecards_food_beverage"),
            [("Approved", "FBGIU NA Bevco Bottling"), ("Pending", "FBGIU EMEA Brew House")],
        )


    def test_hospitality_table_holds_approved_and_pending_h_projects(self):
        self.assertEqual(
            self._table_contents("timecards_hospitality"),
            [("Approved", "HLGIU NA Hilton Downtown"), ("Pending", "hlgiu emea Hyatt Place")],
        )

    def test_rejected_entries_are_in_neither_table(self):
        for table in ("timecards_food_beverage", "timecards_hospitality"):
            self.assertNotIn("Rejected", [status for status, _name in self._table_contents(table)])

    def test_project_of_neither_type_is_in_neither_table(self):
        names = [
            name
            for table in ("timecards_food_beverage", "timecards_hospitality")
            for _status, name in self._table_contents(table)
        ]
        self.assertNotIn("Acme Corp", names)

    def test_the_prefix_is_the_two_letter_code_not_just_its_first_letter(self):
        # "Hyatt..."/"Bevco..." start with H and B but carry neither division's
        # code -- routing on the first letter alone would wrongly claim them.
        storage_service.save_cards([
            _entry("Approved", "Hyatt Corporation Direct", "X100", "Thursday", "7"),
            _entry("Approved", "Bevco Direct", "X200", "Thursday", "8"),
        ])
        names = [
            name
            for table in ("timecards_food_beverage", "timecards_hospitality")
            for _status, name in self._table_contents(table)
        ]
        self.assertNotIn("Hyatt Corporation Direct", names)
        self.assertNotIn("Bevco Direct", names)

    def test_rebuild_drops_entries_that_left_their_status_table(self):
        # Re-saving the pending Brew House entry as Approved MOVES it between
        # status tables (see _save_row) -- the division table must follow it,
        # not keep the stale Pending copy alongside the new Approved one.
        storage_service.save_cards([
            _entry("Approved", "FBGIU EMEA Brew House", "B200", "Tuesday", "2", received="2026-07-06 10:00:00"),
        ])
        self.assertEqual(
            self._table_contents("timecards_food_beverage"),
            [("Approved", "FBGIU EMEA Brew House"), ("Approved", "FBGIU NA Bevco Bottling")],
        )

    def test_renaming_a_project_moves_the_entry_between_divisions(self):
        row_id = next(
            row["id"] for row in storage_service.get_status_rows("approve")
            if row["Project Name"] == "FBGIU NA Bevco Bottling"
        )
        self.assertTrue(
            storage_service.update_status_record_field("approve", row_id, "Project Name", "HLGIU NA Hilton Grand")
        )
        self.assertEqual(
            self._table_contents("timecards_food_beverage"), [("Pending", "FBGIU EMEA Brew House")]
        )
        self.assertIn(("Approved", "HLGIU NA Hilton Grand"), self._table_contents("timecards_hospitality"))

    def test_get_project_type_rows_returns_the_division_with_its_statuses(self):
        rows = storage_service.get_project_type_rows("hospitality")
        self.assertEqual(
            sorted((row["status"], row["Project Name"]) for row in rows),
            [("Approved", "HLGIU NA Hilton Downtown"), ("Pending", "hlgiu emea Hyatt Place")],
        )

    def test_status_counts_can_be_narrowed_to_one_project_type(self):
        counts = storage_service.get_status_project_counts(project_type="beverage")
        self.assertEqual(counts["approve"], 1)
        self.assertEqual(counts["pending"], 1)

        counts = storage_service.get_status_project_counts(project_type="hospitality")
        self.assertEqual(counts["approve"], 1)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["reject"], 0)

    def test_status_rows_can_be_narrowed_to_one_project_type(self):
        rows = storage_service.get_status_rows("approve", project_type="hospitality")
        self.assertEqual([row["Project Name"] for row in rows], ["HLGIU NA Hilton Downtown"])

    def test_project_type_filter_composes_with_the_date_range(self):
        # Both filters narrow the same query -- the Dashboard applies its
        # period picker and its project-type toggle together.
        rows = storage_service.get_status_rows(
            "approve",
            start_date=datetime(2026, 7, 1),
            end_date=datetime(2026, 7, 31, 23, 59, 59),
            project_type="beverage",
        )
        self.assertEqual([row["Project Name"] for row in rows], ["FBGIU NA Bevco Bottling"])

        rows = storage_service.get_status_rows(
            "approve",
            start_date=datetime(2026, 6, 1),
            end_date=datetime(2026, 6, 30, 23, 59, 59),
            project_type="beverage",
        )
        self.assertEqual(rows, [])

    def _export(self, project_type=None):
        """Runs the History page's export over a range covering every seeded
        row, and returns the project names actually written to the file."""
        import csv

        output_path = os.path.join(self.temp_dir.name, "export.csv")
        storage_service.export_summary_csv_range(
            "2026-01-01", "2026-12-31", output_path, project_type=project_type
        )
        with open(output_path, newline="", encoding="utf-8") as f:
            return sorted(row["Project Name"] for row in csv.DictReader(f))

    def test_export_covers_every_division_when_no_type_is_selected(self):
        # Approved only -- that's what the export has always been.
        self.assertEqual(
            self._export(),
            ["Acme Corp", "FBGIU NA Bevco Bottling", "HLGIU NA Hilton Downtown"],
        )

    def test_export_can_be_narrowed_to_one_project_type(self):
        self.assertEqual(self._export(project_type="beverage"), ["FBGIU NA Bevco Bottling"])
        self.assertEqual(self._export(project_type="hospitality"), ["HLGIU NA Hilton Downtown"])

    def test_export_only_flags_the_rows_it_actually_wrote(self):
        # is_exported drives "this entry has already gone out in a file", so a
        # Food & Beverage export must not quietly mark Hospitality's entries as
        # exported too.
        self._export(project_type="beverage")
        exported = {
            row["Project Name"]: row["is_exported"]
            for row in storage_service.get_status_rows("approve")
        }
        self.assertEqual(exported["FBGIU NA Bevco Bottling"], 1)
        self.assertEqual(exported["HLGIU NA Hilton Downtown"], 0)

    def test_no_project_type_still_returns_every_division(self):
        counts = storage_service.get_status_project_counts()
        self.assertEqual(counts["approve"], 3)
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["reject"], 1)


if __name__ == "__main__":
    unittest.main()
