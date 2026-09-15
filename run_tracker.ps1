$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $workspace

py -3 investment_tracker.py `
  --pdf "Mesecno_porocilo_Julij_2026.pdf" `
  --coverage 90 `
  --output "latest_report.txt"
