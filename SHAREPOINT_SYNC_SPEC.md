# SharePoint Update / View Current / Finalize — Architecture Spec

> **Status:** Implemented (see `services/sharepoint_service.py`, `services/sync_service.py`, `ui/Pages/History.py`, `ui/Pages/Settings.py`).
> **Audience:** An engineer or coding agent working on this feature.
> **Goal of this doc:** contain enough detail to work on the feature without re-deriving decisions. Where a decision has already been made, it is stated as a rule, not an option.
>
> **Update:** the email-snapshot sync channel referenced throughout §9 below (`sync_service.push_updates`/`finalize_month`, `outlook_service.py`, `storage_service.apply_incoming_snapshot`) has since been **removed entirely**. This SharePoint channel is now the app's only cross-device sync mechanism — §9's "co-existence" framing is historical context for why some design choices (e.g. per-device-own-rows Excel files, a separate boundary marker) were made, not a description of two channels still running side by side.

---

## 1. What this feature is

Three UI buttons — **Update**, **View Current**, **Finalize** — that move approved timecard data from the local SQLite DB into per-device Excel files kept in a **local OneDrive/SharePoint-synced folder**, merge those files for review, and close out a period by printing the merged result and starting a fresh empty sheet.

This was originally built as a second, independent sync channel alongside an email-snapshot sync (`services/sync_service.py`). That email channel has since been removed (see the note above) — this is now the only sync channel in the app. §9 below is kept for the historical reasoning behind some of its design choices.

### The lifecycle (mirrors the existing email `finalize_month` loop)

```
Update        : DB  ->  this device's Excel  ->  SharePoint folder
View Current  : (Update first)  ->  read all device Excels  ->  merge+dedup  ->  display only
Finalize      : (View Current first)  ->  Confirm/Cancel  ->  print merged  ->  reset to new empty sheet
```

---

## 2. Non-negotiable rules (the two that prevent data corruption)

These two rules are the whole point of the design review. An implementation that violates either is wrong even if it "runs".

### RULE 1 — Update REBUILDS the device's Excel from the DB. It never blind-appends.

Every Update click regenerates the device's entire current-sheet Excel from the DB, filtered to the current open period. Update is **idempotent**: clicking it five times produces the identical file. The DB is the source of truth; the Excel is a pure render of it.

> ❌ Do **not** open the existing xlsx and append rows to it. That stacks duplicates across repeated clicks and forces the merge step to paper over self-inflicted duplication.
> ✅ Query the DB, write a fresh file (overwrite in place), copy to SharePoint.

### RULE 2 — There is ONE shared reset boundary, stored in the SharePoint folder, not per-device.

Finalize prints the **merged (all-device)** sheet but must reset the period for **every** device, not just the one that clicked Finalize. Because this channel has no messaging (unlike email sync), the shared folder itself carries the boundary: a single marker file (`boundary.json`, §5) holds the last-finalized date.

- Every **Update** filters the DB to `received >= boundary_date`.
- Every **Finalize** writes a **new** boundary date into `boundary.json`.
- All devices read the same marker, so a Finalize on device A automatically resets device B's included window the next time B runs Update/View Current.

> ❌ Do **not** infer the boundary from filenames, file mtimes, or a per-device DB value.
> ✅ Single shared `boundary.json` in the SharePoint folder is the authority.

---

## 3. Configuration the user must provide

Add these to whatever settings mechanism the app uses (a new row in `app_state`, or the existing settings surface — check `ui/` for an existing settings page before inventing one):

| Key | Meaning | Example |
|-----|---------|---------|
| `sharepoint_folder` | Absolute path to a **locally-synced** OneDrive/SharePoint folder. | `C:\Users\me\ACT Company\Timecards - Current` |

**No Graph API, no OAuth, no `requests`/`msal` dependency.** Transport is `shutil.copy` into this folder; the OneDrive client handles the actual upload. If the path does not exist or is not a directory, every button surfaces a clear error and does nothing (do not silently create it elsewhere).

---

## 4. Files in the SharePoint folder

```
<sharepoint_folder>/
  boundary.json                 # shared reset marker (§5) — ONE file, all devices
  current_<deviceA>.xlsx        # device A's rendered current sheet
  current_<deviceB>.xlsx        # device B's rendered current sheet
  ...
```

- `<device>` is `get_device_id()` (already exists in `storage_service.py`, a 12-char hex id persisted in `app_state`). Device id in the filename guarantees each device only ever **writes its own file**, which avoids OneDrive "conflict copy" collisions.
- A device's Excel contains **only that device's own scanned rows** for the current period (see §9, RULE 3-parallel). It is the file equivalent of `build_outgoing_snapshot()`.

---

## 5. `boundary.json` format

Small JSON written into the SharePoint folder. Last-writer-wins is acceptable (only Finalize writes it; simultaneous finalizes on two devices are an accepted edge case for a 2-device tool).

```json
{
  "boundary_date": "2026-07-01",
  "finalized_at": "2026-07-20 14:32:05",
  "finalized_by": "a1b2c3d4e5f6"
}
```

- `boundary_date` (`YYYY-MM-DD`): records with `received >= boundary_date` are "current" (in the open period). Records before it were already finalized/printed.
- If `boundary.json` is **missing** (first ever run), treat the boundary as "beginning of time" — include everything. The first Finalize creates the file.
- `finalized_at` / `finalized_by`: audit only; not used for filtering.

---

## 6. Button-by-button behavior

All three hang off **`services/sync_service.py`** as new functions (parallel to `push_updates`/`finalize_month`), and the UI calls into `sync_service`, never into `storage_service`/file IO directly. Reuse the existing `progress_callback(msg)` reporting pattern.

### 6.1 Update — `sharepoint_update(progress_callback=None, project_type=None)`

1. Resolve `sharepoint_folder` (error out if unset/missing).
2. Read `boundary_date` from `boundary.json` (or `None` = include everything).
3. Build **this device's own current-period rows** from the DB. **Reuse the existing snapshot logic** — `build_outgoing_snapshot()` already returns "this device's own scanned rows (`origin = device_id`) since a start date." Preferred implementation: add a `since_date=None` parameter to `build_outgoing_snapshot` (defaulting to `get_last_export_date()` so the email flow is unchanged), and pass `boundary_date` here. Do **not** fork a second copy of the origin/period query.
4. Render those rows to `current_<device_id>.xlsx` — **overwrite** (RULE 1). Reuse the existing xlsx writer helpers used by `export_invoice_lines_to_excel` / `export_act_invoice_overview_range` (openpyxl) rather than a new column layout, unless the user has specified a different sheet shape.
5. `shutil.copy` the file into `sharepoint_folder` (or write directly there — either is fine, but write to a temp path and atomically replace to avoid a half-written file being synced).
6. Report row count. Return `{"file": <path>, "rows": n}`.

**Idempotent.** No append, ever.

### 6.2 View Current — `sharepoint_view_current(progress_callback=None, project_type=None)`

1. Call `sharepoint_update(...)` first (so this device's contribution is fresh).
2. Read **all** `current_*.xlsx` files in `sharepoint_folder` (every device, including this one's just-written file).
3. **Merge + dedup** them into an in-memory table. Dedup key: reuse the system-wide identity **`(day, "Project Number", "Task Name", person_number, received_month)`** (the same tuple `storage_service._save_row` uses). Do not invent a new key. On duplicate, keep one deterministically (the first one seen, in sorted-filename device order — see `sharepoint_service.merge_device_sheets`).
4. `sharepoint_view_current` itself still **writes nothing** — it only reads and returns `{"rows": [...], "sources": [<filenames merged>]}`. Each row is tagged with `_origin_device_id` (parsed from which `current_*.xlsx` it came from) so the UI can tell which rows are this device's own.
5. **The UI window it opens is no longer read-only** (see §6.2.1) — this function's own read-only guarantee is unchanged; the editing happens one layer up, in `ui/Pages/History.py`.

#### 6.2.1 View Current window — editing (added after initial implementation)

The View Current button opens a window (`ui/Pages/History.py`'s `_CurrentSheetDialog`) showing the merged rows from step 3/4 above, with two editable things:

- **Rate** (per row) and a **highlight-color** tag (per row, purely local — see `highlight_color` column, `storage_service.py`) — nothing else is editable. Day/project/task/hours/etc. reflect what was actually scanned and stay read-only.
- **Only editable on rows this device scanned itself** — i.e. `row["_origin_device_id"] == get_device_id()`, which is the only case where a local DB record exists to attach the edit to (`storage_service.find_record_id`, matched by the same identity tuple as §6.2 step 3). A row that only exists because another device scanned it shows read-only in this window; there is nothing local to edit.
- Edits are held in memory while the window is open. **On close, if there are pending edits:** a "Confirm changes" Save/Discard/Cancel prompt appears. **Save** writes each edit to the local DB (`storage_service.update_status_record_field`) and then calls `sharepoint_update(...)` once more, so this device's `current_<device_id>.xlsx` — and therefore the shared folder — reflects the edit immediately rather than waiting for an unrelated future Update click. **Discard** closes without touching the DB or the shared folder. **Cancel** keeps the window open.
- This is the one place SharePoint-adjacent UI code writes to the timecard tables — it does so through the exact same `update_status_record_field` path the (now-removed) email-sync Dashboard rate edit used, not a new write path, and only ever for rows this device already owns in its own DB.

### 6.3 Finalize — `sharepoint_finalize(progress_callback=None, project_type=None, printer=None)`

Called by the UI **only after** the user confirms in a Confirm/Cancel dialog. The confirm dialog is UI-side; this function assumes confirmation already happened (mirror how `finalize_month` is "called AFTER the user has confirmed in the UI").

1. Call `sharepoint_view_current(...)` to get the fresh merged rows (which also runs Update).
2. Build a **transient** merged workbook from those rows — a real `.xlsx` on disk (e.g. in `%TEMP%` or `$CLAUDE_JOB_DIR/tmp`), because a printer needs a file. This file is **ephemeral**; it is not a device current-sheet and is deleted after printing.
3. **Print it literally** (see §7).
4. **Advance the shared boundary:** write a new `boundary.json` with `boundary_date` = today (`YYYY-MM-DD`), `finalized_at` = now, `finalized_by` = `get_device_id()`. This is the global reset (RULE 2).
5. **Reset this device to a fresh empty sheet:** because Update now filters on the new boundary and the current period is empty, the correct next state is an **empty** `current_<device_id>.xlsx`. Regenerate it (it will contain zero data rows, headers only) and copy to SharePoint — i.e. call `sharepoint_update(...)` once more after the boundary moves, which naturally produces the empty sheet. Other devices produce their own empty sheets the next time they run Update.
6. Clean up the transient merged workbook.
7. Return `{"printed": bool, "boundary_date": <new date>, "rows_printed": n}`.

> **Reset semantics restated:** "reset the current date and sheet" = write the new shared `boundary_date` **and** regenerate this device's Excel (now empty) against it. The "completely empty new Excel" is the natural consequence of Update running after the boundary advanced — not a separately-invented blank file.

---

## 7. Printing (literal)

Print the transient merged workbook to a physical printer.

- **Mechanism:** Excel COM via `win32com.client` — open the workbook, `Workbook.PrintOut()`, close without saving. This matches the app's existing COM-first posture (`outlook_service.py`, `win32com`). Alternatively `os.startfile(path, "print")` (ShellExecute "print" verb) if Excel COM is undesirable; pick Excel COM for control over which printer.
- **Printer selection:** default printer unless a `printer` name is passed. Do not hard-code a printer.
- **Failure handling:** if no printer is available or printing raises, **do not advance the boundary and do not reset** — a failed print must leave the period open so the user can retry. Report the error. (Same philosophy as `export_act_invoice_overview_range` refusing to export silently-wrong data.)
- Delete the transient file in a `finally`.

---

## 8. Ordering guarantees (why the "trigger" chain is nested)

Each button re-runs the cheaper one beneath it so the user never acts on stale data — identical to why `finalize_month` calls `update_with_other_user` first:

- View Current runs Update first → the merged view always includes this device's newest DB state.
- Finalize runs View Current first → you print exactly what was on screen, and the boundary advances over data that was actually just re-derived, not a stale snapshot.

Preserve this nesting. Do not "optimize" it away.

---

## 9. Co-existence with the existing email sync (do not break this)

The email channel (`sync_service.push_updates` / `finalize_month`, `storage_service.build_outgoing_snapshot` / `apply_incoming_snapshot`) stays fully intact. Rules to keep the two channels from corrupting each other:

- **Separate boundary.** Email finalize uses `app_state.last_export_date` (`get_last_export_date`). SharePoint finalize uses `boundary.json`. **Keep them separate** — do not have SharePoint finalize write `last_export_date` or vice versa. They are two independent period trackers by design (the user chose co-exist).
- **Excel = this device's OWN rows only** (`origin = device_id`), exactly like `build_outgoing_snapshot`. Do **not** render the full merged DB (which already contains email-synced rows from other devices) into the per-device Excel — that would double-count when the Excels are merged. Per-device-own-rows + merge is the parallel of email's snapshot + `apply_incoming_snapshot`.
- **Known redundancy (accepted).** Because email sync already replicates every device's rows into every device's DB, a single device's DB may already contain the other device's data. The "merge multiple Excels" step is therefore partially redundant with the DB. This is **accepted** under co-exist; the merge dedup (§6.2) absorbs it. Do not try to "fix" it by reading from the merged DB instead of the files — that breaks the excel-per-device model the user chose.
- **No shared mutable state** between channels except the DB tables themselves (read-only from SharePoint's perspective — SharePoint code never writes timecard tables; it only reads them and writes files + `boundary.json`).

---

## 10. New / changed code — where things go

| Concern | Location | Notes |
|---------|----------|-------|
| `sharepoint_update`, `sharepoint_view_current`, `sharepoint_finalize` | `services/sync_service.py` | New orchestrators, parallel to `push_updates`/`finalize_month`. UI calls these. |
| `since_date` param on `build_outgoing_snapshot` | `services/storage_service.py` | Add optional param, default preserves current email behavior. Reuse — don't fork. |
| xlsx rendering of snapshot rows | `services/storage_service.py` | Reuse openpyxl helpers from `export_invoice_lines_to_excel` / `export_act_invoice_overview_range`. |
| `boundary.json` read/write | new small helper, e.g. `services/sharepoint_service.py` | Folder resolution, marker read/write, `shutil.copy`, listing `current_*.xlsx`. Keep file IO out of `sync_service`. |
| Merge + dedup | `services/sharepoint_service.py` | Dedup key = the system identity tuple (§6.2). |
| Print | `services/sharepoint_service.py` | Excel COM `PrintOut`. |
| 3 buttons + Confirm/Cancel dialog | `ui/` | Reuse the in-house themed widgets (`nav_button.py`, etc. — see `CLAUDE.md` UI section). Buttons call `sync_service` with a `progress_callback`. |
| `sharepoint_folder` setting | wherever app settings live (check `ui/` first) | Absolute path to synced folder. |
| Link → local path resolution (§13) | `services/onedrive_link_resolver.py` | Reads OneDrive's own registry sync records — no Graph API. `ui/Pages/Settings.py` calls this to fill in `sharepoint_folder`. |

---

## 11. Acceptance checklist (the feature is "100% as intended" when all hold)

- [ ] Clicking **Update** twice in a row produces byte-for-similar identical data in `current_<device>.xlsx` — no duplicated rows. (RULE 1)
- [ ] The device's Excel contains only rows this device scanned (`origin = device_id`) within `received >= boundary_date`.
- [ ] `sharepoint_view_current` (the function) displays the merged, deduped set of all `current_*.xlsx` and writes nothing to disk on its own (device files and DB unchanged if the window is closed with no edits made, or edits are Discarded).
- [ ] View Current runs Update first; Finalize runs View Current first.
- [ ] In the View Current window, rate/highlight are editable only on rows this device scanned itself (§6.2.1); a row from another device is read-only there.
- [ ] Closing the View Current window with pending edits prompts Save/Discard/Cancel; Save writes to the local DB and re-publishes this device's `current_<device_id>.xlsx`; Discard and Cancel touch nothing (Cancel also keeps the window open).
- [ ] **Finalize** shows a Confirm/Cancel; Cancel does nothing at all (no print, no boundary change, no reset).
- [ ] Finalize on Confirm literally prints the merged sheet to a printer.
- [ ] After a successful Finalize, `boundary.json` holds today's date, and every device's next Update yields an **empty** current sheet (headers only) — including a second device that never clicked Finalize.
- [ ] A failed print leaves the period **open**: boundary unchanged, no reset, error surfaced.
- [ ] Missing/unset `sharepoint_folder` → clear error, no crash, no files written elsewhere.
- [ ] SharePoint code never writes timecard tables — it only reads them and writes files + `boundary.json`.

---

## 12. Deliberately out of scope (do not build unless asked)

- Microsoft Graph / real cloud upload / OAuth — transport is the local synced folder only.
- Merging into a single shared workbook that multiple devices write to — rejected (conflict-copy hell); excel-per-device is the chosen model.
- Reconciling / unifying the email boundary with the SharePoint boundary — they are intentionally separate (moot now that the email channel is gone, see the note at the top of this doc, but the boundary.json mechanism itself is unchanged).
- Any change to the timecard dedup identity tuple — reuse the existing one.

---

## 13. Link-to-local-folder resolution (convenience, added after initial implementation)

**Problem this solves:** users kept trying to paste a SharePoint "Copy Link" web URL into the `sharepoint_folder` setting, because "SharePoint" reads as "a link", not "a disk path". §3 already made the transport decision (local folder, no Graph API) — this section adds a UI convenience that fills in that local path FROM a pasted link, without changing the transport decision at all.

### 13.1 What it is NOT

- **Not the Graph API.** No network call, no OAuth, no Azure AD app registration, no sign-in. If a real engineer or agent picks this up assuming it calls out to `graph.microsoft.com`, that is wrong — it never does.
- **Not a replacement for `sharepoint_folder`.** The resolved local path is written into the exact same `sharepoint_folder` setting §3 already defines. `sync_service`/`sharepoint_service` never see the link — they only ever see the resolved local path, exactly as before this section existed.

### 13.2 How it actually works

OneDrive's desktop client already keeps its own local record of every SharePoint/OneDrive library it has synced on this Windows account, in the registry:

```
HKEY_CURRENT_USER\SOFTWARE\SyncEngines\Providers\OneDrive\<GUID>
    UrlNamespace   -- the library's SharePoint URL, e.g. https://contoso.sharepoint.com/sites/TeamSite
    MountPoint     -- the local folder OneDrive chose for it, e.g. C:\Users\me\Contoso\TeamSite - Documents
    DisplayName    -- human-readable library name
```

`services/onedrive_link_resolver.py` reads this (`list_onedrive_sync_registrations`), then given a pasted link:

1. Parses the link's host + path.
2. Matches it against the registrations: same host, then longest matching URL-path prefix (so a tenant with several synced libraries resolves to the *right* one, not just "the first OneDrive library found").
3. **Best-effort sub-folder extraction:** a SharePoint "library view" link (`.../Forms/AllItems.aspx?id=%2Fsites%2F...%2FSubFolder`) encodes the exact server-relative folder path in its `id` query parameter — when present, this is decoded and appended to the matched `MountPoint`, resolving all the way down to a specific sub-folder rather than just the library root. A "Copy Link" share link's path is an opaque resource id instead (no folder path recoverable from it at all) — for that shape, resolution stops at the library's top-level synced folder, which is still correct, just less precise.
4. Verifies the resolved path actually exists on disk (`os.path.isdir`) before returning it — OneDrive being paused, signed out, or still syncing surfaces as a clear error here, not a folder path that turns out not to exist when Update runs.

Never guesses silently: an ambiguous match (same host, no path overlap, more than one candidate) or a registration whose `MountPoint` doesn't currently exist raises `OneDriveLinkResolutionError` with a message that always ends by pointing back at manual Browse.

### 13.3 UI (`ui/Pages/Settings.py`, `ui/sharepoint_settings.py`)

- A "Paste a link..." field + **Resolve** button sits above the existing manual folder field/Browse button, framed as "fills in the field below" rather than as a separate/competing setting.
- On success: fills the existing folder `QLineEdit` and calls the same `sharepoint_settings.set_folder(...)` the manual path/Browse flow already uses — one settings key, one source of truth (`sharepoint_folder`), regardless of how it got populated.
- On failure: shows the resolver's message inline (red), and does **not** touch the folder field — whatever was there (even if blank) is left alone, so a failed resolve can never silently blank out a working config.
- The pasted link itself is persisted separately (`sharepoint_onedrive_link` in `ui/sharepoint_settings.py`) purely so the link field is pre-filled next time — nothing downstream of Settings ever reads it; only the resolved `sharepoint_folder` matters to `sync_service`/`sharepoint_service`.
- Manual Browse/typed-path entry is unchanged and remains the guaranteed fallback when resolution fails (e.g. a machine where OneDrive isn't installed, or the registry shape differs from what's documented above — this registry layout is OneDrive's own undocumented internal bookkeeping, not a public API, so treat §13.2's exact value names as best-effort/subject-to-drift across OneDrive client versions).
