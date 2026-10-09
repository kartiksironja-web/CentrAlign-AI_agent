---
id: vendor_invoice_entry
title: Entering a vendor invoice into the ERP as a bill
summary: How we record a vendor's invoice as a bill in the ERP
done_requires:
  erp_bills: [vendor_id, invoice_no, amount, due_date]
---
# Entering a vendor invoice into the ERP as a bill

Rules

- Use the vendor's MOST RECENT invoice, judged by invoice date, unless the requester names a specific invoice. The vendor portal lists invoices in no particular order, so work out which is newest from the dates. Every date on the portal and in documents is written DD/MM/YYYY (day first). To compare two dates, compare the year first, then the month, then the day; never compare the day numbers alone.
- The invoice amount and due date are only in the invoice document itself. Take them from the document, not from list pages. Dates in documents are written DD/MM/YYYY.
- Vendors can have similar names. Before you write anything, open the ERP's full vendor list and compare the requester's vendor name with EVERY vendor in it. If more than one vendor could be meant (for example the same name with a different ending or city), ask the requester which one before writing anything. Do not choose between them yourself.
- Before creating anything, check whether the ERP already has a bill for that vendor and invoice number. If it does, do not create another one: the work is already done (finish with status no_action_needed).
- Use the ERP's normal web interface first. Use its API only if the interface does not work.
- Bills over 50,000 rupees need human approval. The system will ask for it when you try to save such a bill.
- After saving, look at the saved bill again and compare every field (vendor, invoice number, amount, due date) with the invoice document. A "saved" message proves nothing.

Where the truth is (use these as the independent sources when you check your work)

- The invoice document prints the invoice number, the amount and the due date on separate lines that start with `Invoice No:`, `Amount Due: INR` and `Due Date:` (the date is written DD/MM/YYYY). Always read the values from the document itself. When a check reads the document, its path is `portal_files/` followed by the invoice number and `.pdf`.
- The vendor's id is in the ERP vendor list (table `erp_vendors`), found by the vendor's name.
- Which invoice is the vendor's most recent is also recorded in the portal table `portal_invoices` (the row of that vendor's id with the newest `invoice_date`). Use that table to check that you picked the right invoice, not only that the saved bill matches the document you picked.

Done means

Exactly one new bill exists in the ERP for that vendor and invoice, its vendor, invoice number, amount and due date match the invoice document, and nothing else in the company systems changed.
