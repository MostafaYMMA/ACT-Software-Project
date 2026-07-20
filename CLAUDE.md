# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop app (PySide6/Qt) that automates reading "Weekly Cards" (timecard) and expense-report
emails from a local Outlook client (via `win32com.client`, offline/COM automation — no Outlook API/cloud
access), and persists the extracted data into a SQLite database (`data/cards.db`) and CSV/Excel exports.
Internship project, work in progress.

## Running

```
python main.py
```

Requires Windows + a configured local Outlook desktop client (COM automation via `outlook_service.py`).
No requirements.txt/pyproject.toml exists in the repo — dependencies (PySide6, win32com, pdfplumber,
openpyxl, docx/docx2txt, xlrd, python-pptx, extract_msg) are installed ad hoc; check imports if setting
up a fresh environment.

## Tests

Tests use `unittest` (not pytest), live in `tests/`, and import service/ui modules directly (no package
install step — `main.py` appends `services/` to `sys.path`, tests rely on running from repo root).

```
python -m unittest discover tests
python -m unittest tests.test_auth
python -m unittest tests.test_auth.VerifyPasswordTests.test_verify_password
```

`tests/verify_scan_vs_outlook.py` is a manual verification script (compares a live Outlook scan against
stored data), not an automated test.

## Architecture

**Pipeline:** `outlook_service` (reads raw Outlook mail via COM) -> `filter_service` (keeps only
Approved/relevant timecard & expense-report emails, extracts attachment text from PDF/docx/xlsx/pptx/msg)
-> `extractor_service` (regex-parses email/attachment text into structured entries) -> `storage_service`
(dedupes, upserts into SQLite, rebuilds derived tables, exports CSV/Excel) -> `ui/` (PySide6 dashboard).
`services/sync_service.py` (`sync_cards`) is the orchestrator that runs this full pipeline end to end;
UI code calls into it rather than the individual services directly.

**Two independent email types, each with its own filter → extract path:**
- Timecards: `filter_service.get_approved_cards` -> `extractor_service.extract` -> `storage_service.save_cards`
- Expense reports: `filter_service.get_expense_reports` -> `extractor_service.extract_expense` -> `storage_service.save_expenses`

**Storage model (`services/storage_service.py`, the largest/most load-bearing file):**
- Three status tables (`timecards_approved`/`_pending`/`_rejected`) hold the same *kind* of row; an
  entry exists in exactly one at a time, identified by `(day, "Project Number", "Task Name",
  person_number, received_month)`. A status change (e.g. Pending -> Approved) *moves* the row between
  tables rather than updating a status column — see `_save_row`.
- Two derived "project type" tables (`timecards_food_beverage`, `timecards_hospitality`) are rebuilt
  from scratch from the status tables on every write (`_rebuild_project_type_tables`), routed by the
  project name's prefix (`FB`/`HL`). Never written to directly.
- `invoice_lines` is one row per approved raw timecard entry, manually enriched (Invoice Number, Sales
  Price, etc.) after auto-creation; re-syncs only INSERT new rows, never touch existing manual edits.
- `expenses` is one row per expense *report* (not per line item); linked to a timecard record by sender
  + period overlap, not by any shared ID (`_fill_sender_timecard_match`).
- Multi-device sync (`sync_service`/`storage_service.build_outgoing_snapshot`/`apply_incoming_snapshot`)
  works by emailing snapshot payloads between devices, tagging rows with `origin`/device id so a device
  never re-broadcasts data it merely received.
- Schema migrations are done in-place inside `init_db()` (`_ensure_columns`, table rebuilds like
  `_rebuild_status_table_with_received_month`) — there is no separate migrations directory/tool.

**UI (`ui/`):** `main.py` is the only entry point/router (splash -> account create/select -> `ui/app.py`
`MainWindow`); it contains no account or page logic itself. `ui/athu.py` owns local account
auth (salted-hash, stored in a JSON file under the user's config dir, not the SQLite db). Custom themed
widgets (`switch.py`, `toggle_switch.py`, `nav_button.py`, etc.) and `ui/theme.py`/`theme_manager.py`
form a small in-house design system — reuse these rather than raw Qt widgets when adding UI.

## Data files (do not hand-edit)

`data/cards.db` (SQLite, binary), `output.csv`, `expenses.csv`, `invoice_lines.csv/.xlsx` are generated
artifacts from running the app, not source — avoid editing them by hand. `services/date_utils.py` is a
small shared helper for turning UI period selections into date ranges used across `storage_service`.
