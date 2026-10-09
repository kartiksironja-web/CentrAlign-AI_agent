---
id: mark_bill_paid
title: Marking a bill as paid
summary: Change an existing Open bill in the ERP to Paid
done_requires:
  erp_bills: [status]
allow_literal:
  erp_bills: [status]
---
# Marking a bill as paid

Rules

- Mark a bill paid only if it exists in the ERP and its status is Open. If it does not exist, stop and tell the requester. If it is already Paid, nothing is needed (finish with status no_action_needed).
- Identify the bill by vendor and invoice number as given by the requester.
- Marking a bill over 50,000 rupees as paid needs human approval. The system will ask for it.
- Change only that bill's status. Nothing else may change.
- Use the ERP's normal web interface first. Use its API only if the interface does not work.
- After the change, look at the bill again and confirm its status.

Done means

That one bill's status is Paid, no other record in the company systems changed.
(The expected status "Paid" comes from this rule itself, so a literal expected value is allowed for it.)
