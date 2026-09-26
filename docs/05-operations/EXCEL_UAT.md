# Excel Sandbox UAT

Status: **SANCTIONED TEST HARNESS — NOT A PRODUCTION DATABASE**

The owner requested that OS AI Core be tested against Excel before connecting a real customer API.

## Purpose

Treat one local XLSX workbook as a fake customer system so the Windows UAT can exercise the same F9 flow without production credentials:

```text
trusted actor
-> F9 capability/policy
-> deterministic validation
-> step-up approval test boundary
-> core idempotency journal
-> Excel source adapter
-> source-side Excel idempotency journal
-> XLSX mutation
-> audit receipt
```

## Workbook contract

The sandbox creates these sheets:

- `Customers`
- `ProcurementRequests`
- `ProcurementItems`
- `DraftVouchers`
- `VoucherEntries`
- `SystemJournal`

The first four owner-approved actions are supported:

- create customer;
- update customer;
- create procurement request;
- create draft voucher.

Voucher posting, deletion and posted-record editing remain unavailable.

## Run on Windows

From the repository root after installing the project:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\excel-uat.ps1
```

Or call the Python UAT directly:

```powershell
python -m osai.excel_uat smoke --path "C:\OSAI\pilot.xlsx" --db "C:\OSAI\pilot.sqlite3"
```

Close the workbook in Microsoft Excel before a write test. The adapter uses atomic replacement and fails closed if Windows has the file locked.

## What the smoke test proves

The UAT creates and updates a customer, creates a procurement request with line items, creates a balanced draft voucher with two lines, verifies the audit chain and writes source receipts into the workbook.

It also tests that:
- local Excel reads are scope constrained;
- formula-like user text is stored as plain data, not executed;
- source-side idempotency prevents duplicate rows;
- the same F9 validation and approval pipeline is used.

## Limits

This is deliberately a test source. It does not claim concurrent multi-user Excel safety, production secrets, production step-up approval, ERP workflow rules, or database-grade transactions. A real customer deployment still uses a sanctioned API/source integration.
