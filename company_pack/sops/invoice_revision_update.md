---
id: invoice_revision_update
title: Updating a bill after the vendor revised the invoice
summary: Update an existing bill's due date from a revised invoice document; no new bill is created
done_requires:
  erp_bills: [invoice_no, due_date]
---
# Updating a bill after the vendor revised the invoice

Rules

- A revised invoice document replaces the earlier one. Its number carries the original number plus a revision suffix, and the document says which invoice it revises.
- Find the revised document on the vendor portal and read it. Its due date is the new due date. Dates in documents are written DD/MM/YYYY.
- The bill for the original invoice already exists in the ERP. Update that bill. Never create a new bill for a revision.
- Change only that bill's due date. Do not change its amount, vendor, status or any other bill.
- If the ERP has no bill for the original invoice, do not create one: stop and tell the requester.
- Use the ERP's normal web interface first. Use its API only if the interface does not work.
- After saving, look at the bill again and compare its due date with the revised document.

Where the truth is (use these as the independent sources when you check your work)

- The revised document prints its own number and the number it revises on lines such as `Invoice No: INV-2033-R1` and `Revision of: INV-2033.`, and the new due date on a line such as `Due Date: 07/09/2026`. When a check reads the document, its path is `portal_files/<revised invoice number>.pdf`.
- The bill to check is in the ERP table `erp_bills`, found by the ORIGINAL invoice number.

Done means

Exactly one bill changed, its due date matches the revised invoice document, no bill was added, and nothing else in the company systems changed.
