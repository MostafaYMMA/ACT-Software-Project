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

`SHAREPOINT_SYNC_SPEC.md` documents a planned second sync channel (per-device Excel files in a local
OneDrive-synced folder) in detail — see the Architecture section below for its actual (unwired) state
before trusting that doc's "Implemented" status line.

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

Coverage spans auth (`test_auth.py`), date-range math (`test_date_range.py`, `test_scan_period_accuracy.py`,
`test_scan_watermark.py`), storage/dedup (`test_storage_date_filter.py`, `test_project_type_tables.py`,
`test_two_device_no_duplicates.py`), export files (`test_active_export_failure.py`,
`test_active_export_project_type.py`, `test_export_history_path.py`), the Current Sheet page
(`test_current_sheet*.py` — rendering, color sync, project-type/prune behavior; several of these currently
fail on an unrelated pre-existing issue in that page's test setup, not something touched by the sync work
below — verify against `git stash`/baseline before assuming a change caused a Current Sheet test failure),
email sync plumbing (`test_sync_mail_filtering.py`, `test_sync_worker_project_type.py`), UI polish
(`test_ui_animations.py`, `test_font_assets.py`). There is no test file for `services/sharepoint_service.py`
or `services/onedrive_link_resolver.py` — consistent with them having no UI callers (see Architecture).

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
  tables rather than updating a status column — see `_save_row`. This identity tuple is the dedup key
  used everywhere records need to be matched across sources (email sync merge, SharePoint sheet merge,
  Current Sheet edits).
- Two derived "project type" tables (`timecards_food_beverage`, `timecards_hospitality`) are rebuilt
  from scratch from the status tables on every write (`_rebuild_project_type_tables`), routed by the
  project name's prefix (`FB`/`HL`). Never written to directly. The UI's Food & Beverage/Hospitality
  toggle (`ui/project_type_settings.py`) filters most reads by this same prefix.
- `invoice_lines` is one row per approved raw timecard entry, manually enriched (Invoice Number, Sales
  Price, etc.) after auto-creation; re-syncs only INSERT new rows, never touch existing manual edits.
- `expenses` is one row per expense *report* (not per line item); linked to a timecard record by sender
  + period overlap, not by any shared ID (`_fill_sender_timecard_match`).
- `get_device_id()` returns a 12-char hex id persisted in `app_state`, unique per install — both sync
  channels below tag rows/files with it to tell "scanned here" from "received from elsewhere" apart.
- Schema migrations are done in-place inside `init_db()` (`_ensure_columns`, table rebuilds like
  `_rebuild_status_table_with_received_month`) — there is no separate migrations directory/tool.

**Two cross-device sync channels exist in the code; only one is actually wired into the UI.** Don't
assume a function being called from `ui/` just because it exists and is documented — verify with grep
before relying on either description below.

1. **Email-snapshot sync (`services/sync_service.py` + `services/outlook_service.py` +
   `services/sync_payload_excel.py`) — the one actually in use.** `ui/Pages/History.py` and
   `ui/Pages/CurrentSheet.py` both drive this via the shared workers in `ui/sync_workers.py`
   (`RefreshWorker`/`UpdateWorker`/`LocalUpdateWorker`/`FinalizeWorker`/`LocalFinalizeWorker`).
   `storage_service.build_outgoing_snapshot`/`apply_incoming_snapshot` build/apply the payloads;
   `outlook_service.send_sync_mail`/`scan_sync_mails` move them as `.xlsx` attachments on emails whose
   subject matches a fixed pattern (`ACT-SYNC v1 | {kind} | {device_id} | seq={n}`);
   `sync_payload_excel.write_payload_workbook`/`read_payload_workbook` serialize/parse that `.xlsx`.
   Partner address lives in `ui/sync_partner_settings.py` (`sync_partner_email`, via
   `QSettings("ACTSoftware", "TimecardApp")`) — sync behavior is "on" only when that's set; empty
   partner falls back to the `local_update`/`local_finalize` (no email at all) path in the same worker
   set.

   **Sync = send-only; Scan Inbox = receive.** `update_with_other_user` (what the Sync button actually
   calls) only pushes this device's snapshot and tops up the export file — it does **not** pull/check
   for incoming sync mail. Pulling happens exclusively as part of `sync_cards` (Scan Inbox): rather than
   a second, dedicated Outlook walk, `filter_service.get_approved_cards` takes an optional
   `sync_mail_items` out-list it appends raw `ACT-SYNC`-subject items to during its *existing* walk
   (no attachment opened on them there), and `sync_service.pull_collected_updates` /
   `outlook_service.read_collected_sync_mails` process just that pre-collected list afterward — one
   Outlook walk covers both timecard scanning and sync-mail pickup, wrapped in `try/except` so a
   failure or simply "nothing waiting" (the common case) never blocks the regular scan. `scan_sync_mails`
   /`pull_updates` (their own dedicated walk) still exist and back `_apply_sync_messages`, the shared
   apply logic both paths call — kept only as a fallback/manual-trigger path, not used by Sync anymore.
   `finalize_month` still calls `update_with_other_user` internally, so Finalize's pre-close sync step
   is push-only too (it does not pull the partner's last-minute updates before closing the period).

   **`mail.Send()` succeeding does not mean the mail was transmitted** — it only means Outlook queued it
   into the Outbox; if Outlook is Working Offline, or its scheduled Send/Receive hasn't fired, the item
   can sit in the Outbox indefinitely (and won't appear in Sent Items until it actually goes out).
   `send_sync_mail` forces a `namespace.SendAndReceive(False)` right after `Send()` for this reason, and
   logs an explicit warning if `namespace.Offline` is true. A stuck Outbox with `item.Submitted == False`
   points at an Outlook/account-level problem (auth, connectivity — check Outlook's "Sync Issues"
   folder for `0x800CCC0E`-style errors), not at this code.

   **The payload's "Rows" sheet (the actual records) is the active sheet when the file opens, "Meta"
   (kind/device_id/seq/generated_at/period_start) is second** — `write_payload_workbook` inserts Rows
   at index 0 and sets it active specifically so opening the attachment shows the real timecard data
   immediately, not a small key/value summary with the data one un-clicked tab away. Column widths in
   both sheets are sized off the longest actual value written (capped at 60 chars), not just the header
   label, and the header row is bold + frozen (`freeze_panes="A2"`) on both sheets.

   Snapshots are cumulative, not deltas: `build_outgoing_snapshot` always publishes everything since the
   last boundary that originated on this device (`origin = get_device_id()`), so a newer `seq` from the
   same sender is always a superset of an older unprocessed one — `apply_incoming_snapshot` uses this to
   silently skip anything not strictly newer than the last-applied `seq` per sender
   (`get_last_applied_seq`), and it's also why stale queued Outbox duplicates can just be deleted rather
   than needing to be individually reconciled.

   **Known, deliberately deferred gaps (raise before touching further):** (a) no sender-address check —
   trust is "subject matches `ACT-SYNC v1 | ...`", not "came from the configured partner email"; (b) the
   cross-status-table conflict case (a record `Approved` locally but arriving from the partner tagged a
   different status) has no dedicated test confirming `_save_row`'s behavior.

2. **SharePoint-folder sync (`services/sharepoint_service.py` + `services/onedrive_link_resolver.py`)
   — settings UI exists, but the sync functions themselves are currently unreferenced from `ui/`.**
   `sync_service.sharepoint_update`/`sharepoint_view_current`/`sharepoint_finalize` (per-device Excel
   files + a shared `boundary.json` in a local OneDrive-synced folder, no Graph API/OAuth — see
   `SHAREPOINT_SYNC_SPEC.md` for the full design) have no callers anywhere under `ui/` or `tests/` as of
   this writing. Only the *configuration* side is wired up: `ui/Pages/Settings.py` +
   `ui/sharepoint_settings.py` persist `sharepoint_folder`/`sharepoint_onedrive_link`, and
   `onedrive_link_resolver.resolve_local_path_from_link` (reads OneDrive's own registry sync records,
   no network call) lets a user paste a web link there instead of Browsing to a local path. Comments in
   several files (`sharepoint_settings.py`, `CurrentSheet.py`) describe the three buttons as if they
   exist on `ui/Pages/History.py`/`CurrentSheet.py` — they don't currently; treat those comments as
   stale/aspirational, not as a map of working code.

**UI (`ui/`):** `main.py` is the only entry point/router: boot splash (`ui/boot_logo_splash.py`) ->
`ui/athu.py`-backed account create/select (`ui/account_page.py`/`ui/select_account_page.py`) ->
`ui/app.py`'s `MainWindow`; it contains no account or page logic itself, and calls `storage_service.init_db()`
before building any page so a fresh/missing `cards.db` can't crash on first launch. `ui/athu.py` owns
local account auth (salted-hash via `schemas/accounts.py`'s `Account` dataclass, stored as JSON under the
user's config dir, not the SQLite db).

`ui/Pages/` holds one file per sidebar destination: `Dashboard.py` (live inbox scan + stat cards, via its
own `SyncWorker`/`RowsWorker`), `History.py` (Export History: local Update + email-sync/local Finalize,
see above), `CurrentSheet.py` (the editable current-period grid: per-row rate + highlight-color editing,
same Update/Sync/Finalize workers as History), `Records.py`, `Late.py` (overdue-card view), `Settings.py`
(theme, project-type filter, notifications, sync partner email, SharePoint folder — all backed by small
`QObject`+`QSettings` singletons: `ui/theme_manager.py`, `ui/project_type_settings.py`,
`ui/notification_settings.py`, `ui/sync_partner_settings.py`, `ui/sharepoint_settings.py`, all under the
same `QSettings("ACTSoftware", "TimecardApp")` store), plus shared helpers `date_filter_header.py` /
`date_range_popup.py` / `placeholder.py`. Custom themed widgets (`switch.py`, `toggle_switch.py`,
`nav_button.py`, etc.) and `ui/theme.py`/`theme_manager.py` form a small in-house design system — reuse
these rather than raw Qt widgets when adding UI.

## Data files (do not hand-edit)

`data/cards.db` (SQLite, binary), `output.csv`, `expenses.csv`, `invoice_lines.csv/.xlsx`, and anything
under `exports/` (per-period `.xlsx` exports named like `2026-07_food_beverage.xlsx`) are generated
artifacts from running the app, not source — avoid editing them by hand. `services/date_utils.py` is a
small shared helper for turning UI period selections into date ranges used across `storage_service`.
`models/cards.py` is stray/junk (a single line of gibberish, not real code) — do not treat it as part of
the data model; the actual timecard row shape lives only implicitly in `storage_service`'s SQL.
