# Walkthrough of one real run

This is a real run from the official evaluation sweep, not a mock-up: run `20261009-102834-ea71`, scenario S1, task
"Enter Acme Supplies' latest invoice into the ERP and tell me when it's done.", model `mistral/ministral-14b-latest`, no faults,
audit mode `enforce`, run on the frozen code (tag `agent-frozen`). The log is in `runs/samples/20261009-102834-ea71/run.json`;
`timeline.html` next to it is the clickable version (`python -m scripts.render_run <run.json>` makes one for any run).
It is a successful run. `evals/RESULTS.md` shows how often that happens (not always) and `docs/PROGRESS.md` lists the failures on the way.

## Before the first step (three model calls, no tools)

1. **Understand.** The request became a task contract: a goal, the named things involved, what has to be found out, the risks.
   `ambiguities` was empty, so no human was asked.
2. **Pick the handbook sections.** The model chose `vendor_invoice_entry` from the one-line summaries of the four procedures.
3. **Plan.** A five-item checklist: confirm the vendor in the ERP, find the latest invoice by comparing dates, read the PDF, enter the bill,
   double-check the saved bill against the document.

## The work (steps 1 to 18)

Every step is one tool call with a stated reason. The model sees a compact state each time (request, contract, the chosen rules, plan,
its notes, one line per older step and the last three in full), never an ever-growing chat.

| Steps | What happened |
|---|---|
| 1-4 | Opened the ERP vendor list (to avoid mixing up similar names), found Acme Supplies, then listed its invoices on the portal and worked out from the dates that INV-2041 is the newest. Two notes recorded the vendor id and the invoice number. |
| 5 | Checked that no bill for INV-2041 exists yet (the handbook says never to create a duplicate). |
| 6-9 | The amount and due date are only inside the PDF. Step 7 failed (it tried a local path); the error message said documents on a connected system are read by URL; steps 8-9 found the link and read the PDF. |
| 10-11 | Noted the amount and the due date. |
| 12-17 | Opened the ERP's new-bill form, chose the vendor, typed the invoice number, amount and due date, and submitted. The policy gate saw and allowed the one write (the amount is under the 50,000 approval limit). |
| 18 | `self_check`: it re-read the saved bill and compared it with the document. |

## Verification (steps 19 to 25)

The agent states what must be true, as YAML. The validator refused four attempts before accepting the fifth, each with a specific, readable
reason:

- an expected value written as a literal instead of read from an independent source ("must come from an independent source, not a literal");
- an expected-value query that matched no row (the vendor name was wrong, so the agent looked the exact name up in step 22);
- an added row that was not declared ("1 row(s) were added to erp_bills: declare `{kind: rows_added, table: erp_bills, count: 1}`").

The accepted set (step 24) has six checks. The invoice number is compared with the portal's own record (the query filters the portal by that
vendor and that invoice number, so it confirms the invoice exists for this vendor; it does **not** prove that it is the *latest* one, because
the agent wrote the filter itself), the amount and due date with the PDF, the vendor id with the vendor table, plus "exactly one row was added"
and "nothing else changed". That gap, a check that agrees with the agent's own choice, is the main limitation of this design and is measured
by the "auditor false pass" column in `evals/RESULTS.md`.

`finish(done)` then triggered the independent audit. It is plain code with its own read-only database connection:

```
PASS  field_equals_1       erp_bills.vendor_id: expected 7, actual 7
PASS  field_equals_2       erp_bills.invoice_no: expected 'INV-2041', actual 'INV-2041'
PASS  field_equals_3       erp_bills.amount: expected 48200.0, actual 48200.0
PASS  field_equals_4       erp_bills.due_date: expected '2026-11-03', actual '2026-11-03'
PASS  rows_added_5         rows added to erp_bills: expected 1, actual 1
PASS  no_other_changes_6   nothing changed beyond the declared changes
```

The final report puts the code's findings first (outcome, audit evidence, the exact row that was added) and the agent's own summary under a
heading that says it is not verified.

## Numbers for this run

25 steps, 154 seconds, 164,694 tokens in and 4,199 out (about $0.021 at the model's list price; the plan used was free), 0 detector firings,
0 gate blocks, 0 human interactions, 1 audit round, first claim correct.

## What this run does not show

It is the happy path. The audit catching a wrong claim, a dead button, a silently wrong date, a bill over the approval limit and a second
vendor with a similar name are in `evals/RESULTS.md` (and in `runs/samples/` where a run is worth keeping). One run is also not a rate: the
agent finishes this task most of the time, not always.
