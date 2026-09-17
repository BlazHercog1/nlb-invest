$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $workspace

py -3 -m nlb_invest `
  --pdf "Mesecno_porocilo_Avgust_2026.pdf" `
  --coverage 90 `
  --output "latest_report.txt"
