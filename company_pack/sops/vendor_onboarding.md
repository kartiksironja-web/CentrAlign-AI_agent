---
id: vendor_onboarding
title: Onboarding a new vendor from a form
summary: Create a vendor in the ERP from a vendor onboarding form file
done_requires:
  erp_vendors: [name, city, gst_no, email, payment_terms_days]
---
# Onboarding a new vendor from a form

Rules

- Take every detail (name, city, tax registration number, email, payment terms) from the form file the requester names. Do not guess or fill in missing details; if something is missing or unreadable, ask the requester.
- Before creating the vendor, check that the ERP does not already have a vendor with that name.
- Payment terms longer than 60 days need human approval. The system will ask for it when you try to save such a vendor.
- Use the ERP's normal web interface first. Use its API only if the interface does not work.
- After saving, look at the vendor again and compare every field with the form.

Where the truth is (use these as the independent sources when you check your work)

- The form prints one detail per line: `Company Name:`, `City:`, `GST No:`, `Contact Email:` and `Payment Terms (days):`. When a check reads the form, its path is `inputs/<form file name>`. The vendor's fields in the ERP table `erp_vendors` are name, city, gst_no, email and payment_terms_days.

Done means

Exactly one new vendor exists in the ERP, its name, city, tax registration number, email and payment terms match the form, and nothing else in the company systems changed.
