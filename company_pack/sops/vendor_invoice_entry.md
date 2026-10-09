---
id: vendor_invoice_entry
title: Entering a vendor invoice into the ERP as a bill
summary: How we record a vendor's invoice as a bill in the ERP
done_requires:
  erp_bills: [vendor_id, invoice_no, amount, due_date]
---
# Entering a vendor invoice into the ERP as a bill

Rules

- Use the vendor's MOST RECENT invoice, judged by invoice date, unless the requester names a specific invoice. The vendor portal lists invoices in no particular order, so work out which is newest from the dates.
- The invoice amount and due date are only in the invoice document itself. Take them from the document, not from list pages. Dates in documents are written DD/MM/YYYY.
- Vendors can have similar names. If it is not clear which vendor is meant, ask the requester before writing anything.
- Before creating anything, check whether the ERP already has a bill for that vendor and invoice number. If it does, do not create another one: the work is already done (finish with status no_action_needed).
- Use the ERP's normal web interface first. Use its API only if the interface does not work.
- Bills over 50,000 rupees need human approval. The system will ask for it when you try to save such a bill.
- After saving, look at the saved bill again and compare every field (vendor, invoice number, amount, due date) with the invoice document. A "saved" message proves nothing.

Where the truth is (use these as the independent sources when you check your work)

- The invoice document prints the invoice number, amount and due date on lines such as `Invoice No: INV-2041`, `Amount Due: INR 48,200.00` and `Due Date: 03/11/2026`. When a check reads the document, its path is `portal_files/<invoice number>.pdf`.
- The vendor's id is in the ERP vendor list (table `erp_vendors`), found by the vendor's name.

Done means

Exactly one new bill exists in the ERP for that vendor and invoice, its vendor, invoice number, amount and due date match the invoice document, and nothing else in the company systems changed.
