# End-to-End Real Outlook Test — Weekly Cards / Timecard App

## Objective
Verify the app behaves as intended by actually driving it against a real local Outlook
client — not mocks, not unit tests in isolation. Manually establish "ground truth" from
the mailbox itself, run the app's real pipeline, then diff the two.

## Scope — full autonomy
This runs against the real internship inbox with full autonomy: read, extract, write to
`cards.db`/exports, and send/receive real emails (including triggering
`outlook_service.send_sync_mail`) as needed to exercise the complete pipeline
end-to-end, including the real sync round-trip. No stop-and-confirm gates — proceed
through the full flow.
- Take a full backup copy of `data/cards.db` before starting (cheap insurance, not a
  gate — just do it, then continue).

## Agent architecture
Run this as a lead/subagent pipeline, not a single flat session:
- **Lead agent: Opus 5.** Owns the plan, reads this file, decides what to delegate,
  reviews every subagent's output against the ground truth, decides whether a finding
  is a real bug or a misread, and is the only one that orders fixes.
- **Subagents: Sonnet.** Each subagent gets one scoped task from Steps 1–4 below (or a
  slice of one — e.g. one subagent per attachment type, or one per DB table being
  checked), its own context window, and reports back findings only — it does not
  decide what's a bug and what isn't.

  **Setup required to actually get this model split** (this file alone won't enforce
  it): define each subagent with `model: sonnet` in its config — either a
  `.claude/agents/*.md` file with `model: sonnet` in its frontmatter, or pass the model
  explicitly when the Lead invokes the Task tool for each dispatch. Start the top-level
  session itself on Opus 5 (`/model claude-opus-5` or your default-opus env var) so the
  Lead is the one running Opus.
- **Loop**: Lead dispatches subagents → subagents report → Lead reviews and either (a)
  accepts the finding, (b) sends the subagent back with a follow-up to dig deeper on
  an ambiguous result, or (c) dispatches a new subagent for a gap the results revealed.
  Repeat until the Lead judges Steps 1–4 fully covered.
- **Fix phase**: only after the Lead has the complete problem list (end of Step 4) does
  it dispatch subagents to implement fixes — one subagent per problem/file area, with
  the Lead reviewing each fix before moving to the next.
- Report (Step 5) is written by the Lead, synthesizing all subagent findings — not
  handed back verbatim from any one subagent.


1. Open the real Outlook mailbox and manually enumerate every email that matches the
   "Weekly Cards" / expense-report subject/sender patterns used by `filter_service`
   (read the actual filter logic first — don't assume from memory).
2. For each matching email, manually note: sender, subject, date, attachment type
   (PDF/docx/xlsx/pptx/msg), and the specific card/expense fields it should produce
   (day, project number, task name, person number, amount, status: approved/pending/
   rejected).
3. Write this ground-truth list to a scratch file (e.g. `test_artifacts/ground_truth.md`)
   before running any app code, so it can't be unconsciously influenced by the app's output.

## Step 2 — Run the real pipeline
1. Snapshot `data/cards.db` (copy it aside).
2. Run the actual scan path used by the UI — trace `ui/Pages/Dashboard.py`'s
   `SyncWorker`/`RowsWorker` or the equivalent `sync_service` entry point — against the
   real mailbox, including the full sync send/receive round-trip if the flow calls for it.
3. Capture whatever the pipeline reports (counts, errors, skipped items) as it runs.

## Step 3 — Compare actual vs. intended, field by field
Diff the post-run `cards.db` against the Step 1 ground truth:
- **Status routing**: did each entry land in the correct table
  (`timecards_approved`/`_pending`/`_rejected`), and — for a status change — did it
  *move* rather than duplicate (per the identity tuple: day, project number, task name,
  person number, received_month)?
- **Project-type routing**: did `FB`/`HL`-prefixed entries end up correctly rebuilt into
  `timecards_food_beverage` / `timecards_hospitality`?
- **Expense linking**: did each `expenses` row link to the right timecard record via
  sender + period overlap?
- **Invoice lines**: did new approved entries insert into `invoice_lines` without
  touching any pre-existing manually-enriched rows?
- **Attachment extraction**: for each attachment type present in the mailbox, does
  the extracted text/fields match what you manually read from the file?
- **Dedup**: re-run the same scan a second time — confirm no duplicate rows appear.
- **Device tagging**: confirm `get_device_id()`-tagged rows are marked correctly as
  "scanned here."

## Step 4 — Sync-specific checks (full round-trip)
- Actually exercise the sync send/receive path end to end: trigger a real sync send,
  confirm it arrives, and confirm `apply_incoming_snapshot` merges it correctly on the
  receiving side without clobbering local-only rows.
- Confirm — from code, not assumption — whether the receiving member's own inbox is
  scanned independently through the normal filter → extractor path for their own
  records, separate from applying an incoming sync snapshot. State what you find
  explicitly; don't guess.

## Step 5 — Report and fix
Lead produces a report with:
- Ground truth vs. actual, as a table, one row per email/entry, with a pass/fail per
  field checked in Step 3.
- Any mismatch: quote the exact discrepancy and point to the code path responsible.
- Anything unverifiable (e.g. no test data for a given attachment type) — flagged
  explicitly, not skipped silently.
- Confirmation the SharePoint-folder sync channel is still unwired — noted, not tested.

Once the full problem list is assembled, the Lead dispatches fix subagents (one per
problem/file area) and reviews each fix before continuing to the next.

## Ground rule
Backup is the only safety net here (per Scope above) — beyond that, run the full flow
autonomously through to the Step 5 report.
