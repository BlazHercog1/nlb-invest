# NLB investment tracker

An independent command-line tool for three NLB funds: **Visoka tehnologija
delniški**, **Globalni uravnoteženi**, and **Svetovni razviti trgi delniški**.
It is not an official NLB or Yahoo Finance product.

This tracker combines three views of the funds:

- Official daily NLB unit values (VEP/NAV) are used for the fund's actual performance.
- Equity holdings and weights are extracted from the supplied monthly PDFs. Each
  allocation is applied only until the next report takes over, and Yahoo Finance
  prices explain which disclosed companies contributed during each period.
- A live estimate starts with NLB's latest official NAV and applies the latest available Yahoo/FX movement since that NAV date.

The split matters for **Globalni uravnoteženi**: Yahoo does not reliably price its direct bond holdings, so the official NLB NAV remains the source of truth.

## Run

The tracker uses Python 3.10+ and the pure-Python `pypdf` package. It does not require
Poppler, MiKTeX, or `pdftotext`. Install the Python dependency once:

```powershell
py -3 -m pip install -e .
```

Monthly source PDFs are not included in the repository. Obtain a monthly holdings
report from [NLB Skladi](https://www.nlbskladi.si), save it locally, and supply its
path with `--pdf`. The parser expects NLB's monthly-report table layout; it does
not support arbitrary PDFs or other providers.

Then run the tracker with your local reports. Repeat `--pdf` in chronological or
any other order; the tracker orders them by their disclosed holdings dates:

```powershell
py -3 -m nlb_invest `
  --pdf "path/to/july_report.pdf" `
  --pdf "path/to/august_report.pdf"
```

On macOS/Linux, use `python3` instead of `py -3` in the commands below.
Running without `--pdf` expects a local file named `Mesecno_porocilo_Julij_2026.pdf`.

Every successful run automatically refreshes `latest_report.txt` with the complete text dashboard. This also happens in JSON mode, so `--format json --output latest_report.json` keeps both reports current. Use `--latest-report PATH` only if you want the automatic text snapshot stored elsewhere.

The first run resolves the PDF's ISINs through Yahoo Finance and creates `ticker_cache.json`. Later runs reuse only those symbol mappings; prices and FX rates are downloaded fresh every time.

Live estimation is the default. The report includes Yahoo's earliest and latest quote timestamps because exchanges have different trading hours and some feeds may be delayed. Use `--official-close` when you want all market data stopped at NLB's latest published NAV date.

Each fund also has a dedicated return/earnings section starting on **2026-08-19**. It shows the official NLB return, the estimated live return, and the estimated gain per EUR 1,000. When `--invested-tech`, `--invested-balanced`, or `--invested-developed` is supplied, it also estimates the current value and gain/loss for that invested amount.

## My Investments (command line)

Enter a single amount invested in each fund, or reuse the initial-plus-monthly
contribution plan saved by the browser dashboard. The supplied
`run_tracker.ps1` uses `local/dashboard_settings.json`, so its scheduled refresh
keeps the contribution totals and gain/loss figures in `latest_report.txt`.

The tracker treats those amounts as invested from the `--return-since` date. By default that date is `2026-08-19`; change it if your invested amount should be measured from another day.

```powershell
py -3 -m nlb_invest `
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
py -3 -m nlb_invest --format json --output latest_report.json
```

Useful options:

```powershell
# Estimate your current value when you know how much money you invested
py -3 -m nlb_invest --invested-tech 1000 --invested-balanced 1000 --invested-developed 1000

# Reuse the initial and monthly contributions saved by the dashboard
py -3 -m nlb_invest --investment-settings local/dashboard_settings.json

# Machine-readable report
py -3 -m nlb_invest --format json --output latest_report.json

# Analyze every disclosed equity instead of the default 90% coverage target
py -3 -m nlb_invest --coverage 100

# Re-resolve Yahoo symbols after replacing the monthly PDF
py -3 -m nlb_invest --refresh-symbols

# Disable the live extension and align Yahoo with NLB's latest official date
py -3 -m nlb_invest --official-close

# Use a different starting date for the return/earnings section
py -3 -m nlb_invest --return-since 2026-08-19
```

Run the tests with:

```powershell
py -3 -m unittest discover -s tests -v
```

## Keep it current

`run_tracker.ps1` updates `latest_report.txt` and `local/dashboard_report.json`, so scheduled refreshes also update the browser dashboard. To run it each weekday at 19:00, open Task Scheduler, create a basic task, and use:

- Program: `powershell.exe`
- Arguments: `-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\Finance\run_tracker.ps1"`

When NLB publishes a new monthly report, keep the earlier PDFs and add the new
one to `run_tracker.ps1`. Use `--refresh-symbols` once so newly added ISINs are
resolved.

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
py -3 -m pip install -e ".[dashboard]"
```

Double-click **Open dashboard.cmd**, or run `powershell -ExecutionPolicy Bypass -File run_dashboard.ps1`.
The dashboard runs locally at http://127.0.0.1:8501. Keep its launcher window open;
close it to stop the dashboard.

For each fund, enter either a one-off investment of at least EUR 1,000, a
savings-plan contribution from EUR 40 to EUR 500, or both. Choose the initial
and first monthly contribution dates and, if applicable, one later monthly-amount
change.
Then select all available NLB monthly PDFs and click **Refresh**. Each report's
holdings mix is used until the next monthly report takes over. This improves the
holdings attribution as the fund manager changes the portfolio.
Each scheduled payment buys estimated units using the first published NLB NAV on
or after its date. Because every EUR 50 payment buys fund units, its actual value
is driven by NLB's NAV history; it does not remain tied to the holdings mix from
the month when it was paid. The dashboard shows total contributions, estimated investment
values, a contribution-versus-value chart, the generated transaction history,
official NAV performance charts and the disclosed holdings.

**Your monthly story** separates added money from investment earnings. Choose a
month to see opening and closing values and a breakdown by fund. The current
month ends at the latest common published NLB date; deposits count on their
purchase/NAV date, and entered fees reduce earnings.

Under **My contributions > Manage payments**, confirm, skip or edit a scheduled
payment, restore its estimate, or add an extra payment. Enter the amount paid
(including any fee), the purchase date, and optionally the actual units received
from your statement. If units are omitted, they are estimated as
(amount paid - fee) / first published NAV on or after the purchase date.
Confirmed payments verify that a payment happened; their units may still be
estimated. Skipped payments and payments awaiting a published NAV are excluded
from the balance. Recorded payments survive later plan changes.

Payment edits recalculate the saved report immediately with existing prices and
update the text/JSON exports; they do not fetch new market data. Click **Refresh**
to get new prices. The scheduled CLI refresh reuses these same payment records and saves a dashboard
snapshot with NAV history. An open dashboard picks up newer snapshots on its
next interaction.
The transaction history shows each payment's current value, euro gain/loss and
return using the latest published NLB NAV. Existing saved plans continue to work
and their generated payments are marked Estimated. Withdrawals are not supported.

The dashboard records the
15-year holding date for each payment under the tax explanation currently
published by NLB; this is informational and is not a tax calculation. A refresh
can take a few minutes.
The progress bar shows PDF parsing, NLB values and holding prices for each fund,
then saving. It advances by completed stages rather than elapsed time.

Use **Download text report** or **Download JSON report** to export the last
successful report shown on screen. Downloads do not fetch new prices. The text
export uses the CLI report format; JSON also includes the chart history.
Files are downloaded through your browser as `latest_report.txt` and
`latest_report.json`.

Settings, uploaded PDFs and the last successful dashboard report are stored in
the ignored `local/` directory. Reopening the dashboard shows saved results;
when a new monthly payment is due, the dashboard automatically refreshes the
saved plan and prices on opening or rerunning. A payment on the 15th repeats on
the 15th of every month; the original date is its first occurrence. The overview
shows the latest included monthly payment and the next scheduled payment.
If prices are unavailable, due payments remain visible as pending NAV; no earlier
price is substituted. Automatic checks retry on the next day, and **Refresh**
can retry immediately. It can also display an existing
`latest_report.json`; refresh once to add chart history. A successful dashboard
refresh also updates the root `latest_report.txt`, including initial and monthly
contributions, their estimated current values, and the resulting gain or loss.
A failed refresh keeps the previous results visible. Invalid payment edits leave saved results unchanged.

## NLB savings-plan rules represented in the dashboard

The contribution-plan inputs follow the public NLB Skladi terms checked in
September 2026:

- a one-off fund purchase starts at EUR 1,000;
- a savings-plan payment is EUR 40-EUR 500, and no fixed payment frequency is
  required (NLB recommends a monthly standing order);
- advertised entry-cost discounts are 15% for EUR 40-59.99, 33.33% for
  EUR 60-149.99, and 50% for EUR 150-500; minors have no entry cost until age
  18;
- the published capital-gains rates are 25% through five years, 20% after five,
  15% after ten, and 0% after fifteen years; every payment has its own holding
  period and FIFO applies;
- redemptions are available at any time and NLB says proceeds are paid within
  six business days.

The dashboard shows these rules for planning and records each payment's
15-year holding date. It does not automatically deduct entry/exit costs or
calculate tax because promotions, investor status, fund, realized price, and
personal circumstances can change the actual result.

Official references: [NLB savings plan](https://www.nlbskladi.si/nalozbene-moznosti/nacini-varcevanja/postopno-varcevanje-z-varcevalnim-nacrtom),
[NLB fund and pricing example](https://www.nlbskladi.si/nalozbene-moznosti/vzajemni-skladi/svetovni-razviti-trgi-delniski),
and [NLB tax overview](https://www.nlbskladi.si/osnove-in-strategije-investiranja/osnove-investiranja/zakaj-varcevati-v-vzajemnih-skladih).

## Project structure

- `src/nlb_invest/models.py`: shared models and fund configuration.
- `nlb_client.py` and `yahoo_client.py`: official NAV and market data access.
- `pdf_parser.py`: monthly holdings report extraction.
- `analytics.py` and `currency.py`: returns, attribution and FX conversion.
- `reporting.py` and `cli.py`: report formatting and command-line orchestration.
- `http_client.py` and `utils.py`: shared transport and decimal parsing.
- `dashboard/app.py`, `data.py`, `launch.py`: interface, local storage and browser launcher.
- `tests/`: tests grouped by responsibility.
- `pyproject.toml`: package metadata, dependencies and optional dashboard dependencies.

Install in editable mode from the project root with `py -3 -m pip install -e .`,
or `py -3 -m pip install -e ".[dashboard]"` for the browser interface.
After installation, use `py -3 -m nlb_invest` for the CLI and
`py -3 -m dashboard.launch` for the dashboard. The original
`investment_tracker.py` remains a small compatibility entry point.
The PowerShell launchers and **Open dashboard.cmd** still work.
