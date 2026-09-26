$ErrorActionPreference = "Stop"

param(
    [string]$WorkbookPath = "$(Join-Path $PWD 'var\OS-AI-Core-Excel-UAT.xlsx')",
    [string]$DatabasePath = "$(Join-Path $PWD 'var\OS-AI-Core-Excel-UAT.sqlite3')"
)

New-Item -ItemType Directory -Force -Path (Split-Path $WorkbookPath) | Out-Null
python -m osai.excel_uat smoke --path $WorkbookPath --db $DatabasePath

Write-Host ""
Write-Host "Excel UAT completed."
Write-Host "Workbook: $WorkbookPath"
Write-Host "Open the workbook in Excel and review Customers, ProcurementRequests, DraftVouchers and SystemJournal."
