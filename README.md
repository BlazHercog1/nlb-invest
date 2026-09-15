# NLB investment tracker

An independent command-line tool for three NLB funds: **Visoka tehnologija
delniški**, **Globalni uravnoteženi**, and **Svetovni razviti trgi delniški**.
It is not an official NLB or Yahoo Finance product.

This tracker combines three views of the funds:

- Official daily NLB unit values (VEP/NAV) are used for the fund's actual performance.
- Equity holdings and weights are extracted from the supplied monthly PDF. Yahoo Finance prices explain which disclosed companies contributed to performance after the report date.
- A live estimate starts with NLB's latest official NAV and applies the latest available Yahoo/FX movement since that NAV date.

The split matters for **Globalni uravnoteženi**: Yahoo does not reliably price its direct bond holdings, so the official NLB NAV remains the source of truth.

## Run

The tracker uses Python 3.10+ and the pure-Python `pypdf` package. It does not require
Poppler, MiKTeX, or `pdftotext`. Install the Python dependency once:

```powershell
py -3 -m pip install -r requirements.txt
```

Monthly source PDFs are not included in the repository. Obtain a monthly holdings
report from [NLB Skladi](https://www.nlbskladi.si), save it locally, and supply its
path with `--pdf`. The parser expects NLB's monthly-report table layout; it does
not support arbitrary PDFs or other providers.

Then run the tracker with your local report:

```powershell
py -3 investment_tracker.py --pdf "path/to/monthly_report.pdf"
```

On macOS/Linux, use `python3` instead of `py -3` in the commands below.
Running without `--pdf` expects a local file named `Mesecno_porocilo_Julij_2026.pdf`.

Every successful run automatically refreshes `latest_report.txt` with the complete text dashboard. This also happens in JSON mode, so `--format json --output latest_report.json` keeps both reports current. Use `--latest-report PATH` only if you want the automatic text snapshot stored elsewhere.

The first run resolves the PDF's ISINs through Yahoo Finance and creates `ticker_cache.json`. Later runs reuse only those symbol mappings; prices and FX rates are downloaded fresh every time.

Live estimation is the default. The report includes Yahoo's earliest and latest quote timestamps because exchanges have different trading hours and some feeds may be delayed. Use `--official-close` when you want all market data stopped at NLB's latest published NAV date.

Each fund also has a dedicated return/earnings section starting on **2026-08-19**. It shows the official NLB return, the estimated live return, and the estimated gain per EUR 1,000. When `--invested-tech`, `--invested-balanced`, or `--invested-developed` is supplied, it also estimates the current value and gain/loss for that invested amount.

## My Investments

The personal tracking is intentionally simple: enter how much money you invested in each fund. You do not need to track units, transactions, fees, XIRR, allocation targets, or rebalancing.

The tracker treats those amounts as invested from the `--return-since` date. By default that date is `2026-08-19`; change it if your invested amount should be measured from another day.

```powershell
py -3 investment_tracker.py `
  --pdf Mesecno_porocilo_Julij_2026.pdf `
  --fund tech `
  --fund balanced `
  --fund developed `
  --return-since 2026-08-19 `
  --invested-balanced 1000 `
  --invested-tech 1000 `
  --invested-developed 1000
```

JSON output includes the existing `funds` array plus a top-level `investment_summary` when invested amounts are provided:

```powershell
py -3 investment_tracker.py --format json --output latest_report.json
```

Useful options:

```powershell
# Estimate your current value when you know how much money you invested
py -3 investment_tracker.py --invested-tech 1000 --invested-balanced 1000 --invested-developed 1000

# Machine-readable report
py -3 investment_tracker.py --format json --output latest_report.json

# Analyze every disclosed equity instead of the default 90% coverage target
py -3 investment_tracker.py --coverage 100

# Re-resolve Yahoo symbols after replacing the monthly PDF
py -3 investment_tracker.py --refresh-symbols

# Disable the live extension and align Yahoo with NLB's latest official date
py -3 investment_tracker.py --official-close

# Use a different starting date for the return/earnings section
py -3 investment_tracker.py --return-since 2026-08-19
```

Run the tests with:

```powershell
py -3 -m unittest -v
```

## Keep it current

`run_tracker.ps1` updates `latest_report.txt`. To run it each weekday at 19:00, open Task Scheduler, create a basic task, and use:

- Program: `powershell.exe`
- Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Finance\run_tracker.ps1"`

When NLB publishes a new monthly report, replace the PDF and update the filename in `run_tracker.ps1` if its name changes. Use `--refresh-symbols` once so newly added ISINs are resolved.

## Privacy and repository contents

The repository contains code, tests, setup instructions and the launcher, not
source PDFs or generated investment reports. `.gitignore` excludes PDFs,
`latest_report*.txt`, `latest_report*.json`, `ticker_cache.json`, Python bytecode,
virtual environments and common credential files. These files remain local.

Reports can contain your invested amounts, estimated balances and investment
start date. JSON reports include only the source PDF filename, not its full local
path. The tracker does not send invested amounts to NLB or Yahoo; Yahoo searches
do send the disclosed holdings' ISINs or issuer names.

For custom report or cache filenames, add the corresponding paths to `.gitignore`,
or keep them in the ignored `local/`, `private/` or `reports/` directories. Never
put actual investment amounts or credentials into committed scripts or examples.
Before publishing, review `git status --short`, `git add --dry-run .` and, after
staging, `git diff --cached`. Ignoring a file does not remove it from existing
commits, and ignored files should not be added with `git add -f`.

## Method and limitations

Holding returns use Yahoo adjusted closes, latest available regular-market prices, and matching foreign-exchange series to calculate EUR returns. Contribution in percentage points is the PDF weight multiplied by the holding's EUR return.

The live NAV estimate assumes the PDF weights remain fixed and that untracked assets are unchanged since NLB's last official NAV. It cannot see NLB's later trades, daily cash flows, fees, derivatives, or the balanced fund's direct bond sleeve. Yahoo's latest values may be delayed, may represent a closed exchange, and are not simultaneous executable prices. Its endpoints are public and require no key, but they are unofficial and have no uptime guarantee. Review unresolved symbols in `ticker_cache.json`; you can correct a mapping by changing its `symbol` value.

The return/earnings section assumes the invested amount was in the fund at the NLB NAV on the requested start date and stayed there continuously. It excludes entry or exit charges, taxes, and later deposits or withdrawals. If there is no NAV on the requested date, the latest preceding NLB NAV is used and its actual date is printed.

This is informational analysis, not investment advice.

## Simple browser dashboard

Install the optional interface dependencies once:

```powershell
py -3 -m pip install -r requirements-dashboard.txt
```

Double-click **Open dashboard.cmd**, or run `powershell -ExecutionPolicy Bypass -File run_dashboard.ps1`.
The dashboard runs locally at http://127.0.0.1:8501. Keep its launcher window open;
close it to stop the dashboard.

Enter investment amounts and their common start date, choose an NLB monthly PDF,
and click **Refresh**. The dashboard shows fund returns, estimated investment
values, official NAV performance charts and the disclosed holdings. It reuses
the command-line tracker's calculations. A refresh can take a few minutes.

Settings, uploaded PDFs and the last successful dashboard report are stored in
the ignored `local/` directory. Reopening the dashboard shows saved results;
no network refresh occurs until you click Refresh. It can also display an existing
`latest_report.json`; refresh once to add chart history. A failed refresh keeps
the previous results visible. The original command-line reports remain separate.
