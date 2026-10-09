# Walkthrough of one real run

This is a real run, not a mock-up: run `20261009-084036-c44b`, task "Enter Acme Supplies' latest invoice into the ERP and
tell me when it's done.", model `mistral/ministral-14b-latest`, no faults, audit mode `enforce`. The log is kept in
`runs/samples/` and can be turned into a clickable timeline with `python -m scripts.render_run runs/samples/<run id>/run.json`.
It is one successful run out of many (see `docs/PROGRESS.md` for the failures and `evals/RESULTS.md` for the evaluation).

## Before the first step (three model calls, no tools)

1. **Understand.** The request became a task contract: a goal, the named things involved, what has to be found out, the risks.
   `ambiguities` was empty, so no human was asked (the vendor is named in the request; which invoice is newest is found by looking).
2. **Pick the handbook sections.** The model chose `vendor_invoice_entry` from the one-line summaries.
3. **Plan.** A seven-item checklist: find the latest invoice, read the PDF, check the vendor and that no bill exists yet,
   enter the bill, verify it, confirm.

## The work (steps 1 to 20)

Every step is one tool call with a stated reason. The model sees a compact state each time (request, contract, the chosen rules,
plan, its notes, a one-line summary of older steps and the last three in full), never an ever-growing chat.

| Steps | What happened |
|---|---|
| 1-4 | Opened the portal vendor list, found Acme Supplies, opened its invoices. The list is unsorted; it worked out from the dates that INV-2041 is the newest. Two notes recorded the vendor id and the invoice. |
| 5-8 | The amount and due date are only inside the PDF, so it opened the PDF link and read it with `read_document`. Noted the amount (48,200.00) and due date (03/11/2026). |
| 9-11 | Checked that the vendor exists in the ERP and that no bill for INV-2041 exists yet (the handbook says never to create a duplicate). |
| 12-17 | Opened the ERP's new-bill form and filled vendor, invoice number, amount and due date, then submitted. Each action went through the policy gate (12 gate decisions, all allowed: 48,200 is under the 50,000 approval limit). |
| 18-20 | Noted the new bill's id, opened it again and compared its fields with the PDF; `self_check` recorded "matched". |

## Verification (steps 21 to 24)

The agent states what must be true, as YAML. The validator refused the first two attempts, both times because the invoice number
was written as a literal instead of being read from an independent source (and, in the first, because the added row was not
declared). The third was accepted: seven checks. The invoice number, amount and due date of the saved bill must equal values read
from the PDF by patterns using the exact labels the document prints (`Invoice No:`, `Amount Due: INR`, `Due Date:`); the vendor
id must equal the id the vendor table gives for that name; exactly one row was added; nothing else changed.

`finish(done)` then triggered the independent audit. It is plain code with its own read-only database connection:

```
PASS  bill_exists_INV-2041       row_exists on erp_bills: 1
PASS  bill_invoice_no_match      erp_bills.invoice_no = 'INV-2041', matches the independent source
PASS  bill_amount_match          erp_bills.amount = 48200.0, matches the independent source
PASS  bill_due_date_match        erp_bills.due_date = '2026-11-03', matches the independent source
PASS  bill_vendor_match          erp_bills.vendor_id = 7, matches the independent source
PASS  rows_added_erp_bills       rows_added on erp_bills: 1 (expected 1)
PASS  no_other_changes           nothing changed beyond the declared changes
```

The final report puts the code's findings first (outcome, audit evidence, the exact row that was added) and the agent's own
summary under a heading that says it is not verified.

## Numbers for this run

24 steps, 150 seconds, 153,945 tokens in and 3,683 out (about $0.024 at the model's list price; the plan used was free),
0 detector firings, 0 gate blocks, 0 human interactions, 1 audit round, first claim correct.

## What this run does not show

It is the happy path. The harder scenarios (a silent date fault, a broken button, a bill over the approval limit, a second
vendor with a similar name) are in `evals/RESULTS.md`, and the runs that failed on the way here are listed in `docs/PROGRESS.md`.
