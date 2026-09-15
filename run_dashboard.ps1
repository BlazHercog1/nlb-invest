$ErrorActionPreference = "Stop"
$dashboardWorkspace = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $dashboardWorkspace
& py -3 -c "import nlb_invest, dashboard, streamlit, plotly" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Install the dashboard once with: py -3 -m pip install -e ".[dashboard]"'
    exit 1
}
& py -3 -m dashboard.launch
exit $LASTEXITCODE
