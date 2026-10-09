# How to add a task

A new task is **data**, not code. The agent (`agent/`) and the auditor (`auditor/`) stay as they are;
`python -m scripts.prove_generalization` checks that claim against a recorded freeze.

The four tasks in the repo were added this way:

| Task | Handbook file | What was new |
|---|---|---|
| Enter an invoice as a bill | `company_pack/sops/vendor_invoice_entry.md` | the first task |
| Update a bill from a revised invoice | `company_pack/sops/invoice_revision_update.md` | a new rule file, one scenario |
| Onboard a vendor from a form | `company_pack/sops/vendor_onboarding.md` | a new rule file, one scenario |
| Mark a bill paid | `company_pack/sops/mark_bill_paid.md` | a new rule file, one scenario |

## 1. Write the handbook file (`company_pack/sops/<id>.md`)

```markdown
---
id: my_task
title: One line, what the work is
summary: One sentence the agent reads when it chooses which rules apply
done_requires:            # fields the audit MUST check, per table
  erp_bills: [vendor_id, amount]
allow_literal:            # optional: fields whose expected value may be written down literally
  erp_bills: [status]
---
# Title

Rules
- Rules and limits, in plain words. No click paths: the agent finds its own way through the pages.

Where the truth is (use these as the independent sources when you check your work)
- Which document lines or tables hold the real values, and the path a check uses to read a document.

Done means
What must be true afterwards, and that nothing else changed.
```

`done_requires` is what the validator enforces: every listed field needs a `field_equals` assertion whose
expected value comes from an independent source (a document or another table), not from the agent's own notes.
`allow_literal` is the single exception (a value that the rule itself defines, such as the status "Paid").

## 2. Permissions, if the task writes something new

Writes are only possible if a connector names them (`company_pack/connectors/*.yaml`). If the new task needs a
new kind of write, add its action there and, if it needs a human's approval, a rule in `company_pack/policy.yaml`
(for example `{id: ..., action: ..., when: {field: amount, op: ">", value: 50000}, effect: require_approval}`).
Anything not named is denied by the gate.

## 3. Let the auditor see what it needs

`company_pack/audit.yaml` lists the databases, tables and document folders an assertion may read. Add a table
or folder only if the new task needs it.

## 4. Add a scenario (`evals/scenarios.yaml`)

```yaml
  - id: S11
    priority: P1
    name: What it checks
    task: "The request, in plain words."
    expect:
      outcomes: [verified_done]
      max_humans: 0
      ground_truth:            # written by hand from the seed data, never taken from a run
        row_delta: {}              # rows added per table; {} means no table may grow
        changed_tables: [erp_bills]   # tables whose existing rows may change
        rows:
          - {table: erp_bills, where: {invoice_no: INV-2039, status: Paid}, count: 1}
```

The harness checks `ground_truth` against the final database itself, independently of the auditor, so an
auditor mistake would show up as a disagreement.

## 5. Run it and prove the code did not change

```bash
python -m evals.run_evals --repeats 3 --scenarios S11
python -m scripts.prove_generalization      # exit code 0 = agent/ and auditor/ are byte-identical to the freeze
```

## What this does NOT prove

The tasks were designed by the person who built the framework, in the same fictional company, so they are
easier than tasks written by a stranger. If a new task needs a new TYPE of tool (for example, sending an email),
that is code and belongs in `agent/`.
