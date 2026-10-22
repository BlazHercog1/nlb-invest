$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $workspace

$trackerArgs = @(
  "-m", "nlb_invest",
  "--pdf", "Mesecno_porocilo_Julij_2026.pdf",
  "--pdf", "Mesecno_porocilo_Avgust_2026.pdf",
  "--pdf", "Mesecno_porocilo_September_2026.pdf",
  "--coverage", "90",
  "--format", "json",
  "--output", "local/dashboard_report.json"
)

$investmentSettings = Join-Path $workspace "local\dashboard_settings.json"
if (Test-Path -LiteralPath $investmentSettings) {
  $trackerArgs += @("--investment-settings", $investmentSettings)
}

New-Item -ItemType Directory -Path (Join-Path $workspace "local") -Force | Out-Null
py -3 @trackerArgs
exit $LASTEXITCODE
