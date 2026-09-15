"""Extract monthly NLB reports and parse their disclosed equity holdings."""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from .models import FundConfig, Holding
from .utils import parse_decimal


COUNTRY_NAMES = sorted(
    {
        "ARGENTINA", "AVSTRALIJA", "AVSTRIJA", "BELGIJA", "BOLGARIJA", "BRAZILIJA",
        "DANSKA", "FINSKA", "FRANCIJA", "HONG KONG", "HRVAŠKA", "INDIJA", "IRSKA",
        "ISLANDIJA", "ITALIJA", "IZRAEL", "JAPONSKA", "JUŽNA AFRIKA", "JUŽNA KOREJA",
        "KANADA", "KITAJSKA", "LUKSEMBURG", "MADŽARSKA", "MEHIKA", "NEMČIJA",
        "NIZOZEMSKA", "NORVEŠKA", "POLJSKA", "PORTUGALSKA", "ROMUNIJA", "SINGAPUR",
        "SLOVENIJA", "ŠPANIJA", "ŠVEDSKA", "ŠVICA", "TAJVAN", "TAJSKA", "URUGVAJ",
        "ZDA", "ZDRUŽENI ARABSKI EMIRATI", "ZDRUŽENO KRALJESTVO",
    },
    key=len,
    reverse=True,
)


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "The Python package pypdf is required. Install it with: "
            "py -3 -m pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(pdf_path)
        pages: list[str] = []
        for page in reader.pages:
            try:
                page_text = page.extract_text(extraction_mode="layout")
            except TypeError:
                # Compatibility with older pypdf versions that lack layout mode.
                page_text = page.extract_text()
            pages.append(page_text or "")
    except Exception as exc:
        raise RuntimeError(f"Could not read PDF {pdf_path}: {exc}") from exc

    return "\n\f\n".join(pages).replace("–", "-").replace("—", "-")


def parse_holdings(text: str, fund: FundConfig) -> tuple[date, list[Holding]]:
    table_header = "Naložbe podsklada v delnice in enote delniških ciljnih skladov na dan"
    table_positions = [match.start() for match in re.finditer(re.escape(table_header), text)]
    fund_header_re = re.compile(r"(?m)^\s*NLB Skladi\s+-\s+([^\r\n]+?)\s*$")
    headers = list(fund_header_re.finditer(text))
    table_pos: int | None = None
    for candidate in table_positions:
        previous_headers = [match for match in headers if match.start() < candidate]
        if previous_headers and fund.title.casefold() in previous_headers[-1].group(1).casefold():
            table_pos = candidate
            break
    if table_pos is None:
        raise RuntimeError(f"Could not locate the equity holdings table for {fund.title}")

    date_match = re.search(r"na dan\s+(\d{2}\.\d{2}\.\d{4})", text[table_pos : table_pos + 300])
    if not date_match:
        raise RuntimeError(f"Could not determine the holdings date for {fund.title}")
    holdings_date = datetime.strptime(date_match.group(1), "%d.%m.%Y").date()

    row_re = re.compile(r"(?m)^\s*(\d+)\s+([A-Z]{2}[A-Z0-9]{10})\s+")
    row_matches = list(row_re.finditer(text, table_pos))
    holdings: list[Holding] = []
    expected_number = 1
    for index, match in enumerate(row_matches):
        number = int(match.group(1))
        if not holdings and number != 1:
            continue
        if holdings and number != expected_number:
            break
        body_end = row_matches[index + 1].start() if index + 1 < len(row_matches) else len(text)
        body = text[match.end() : body_end]
        weight_match = re.search(r"(\d+),((?:\s*\d)+)\s*%", body)
        if not weight_match:
            if holdings:
                break
            continue
        raw_identity = body[: weight_match.start()].replace("\f", " ").strip()
        identity = re.sub(r"\s+", " ", raw_identity).strip()
        country = next((name for name in COUNTRY_NAMES if identity.endswith(f" {name}")), None)
        if country is None:
            pieces = [re.sub(r"\s+", " ", piece).strip() for piece in re.split(r"\s{2,}", raw_identity) if piece.strip()]
            country = pieces[-1] if len(pieces) >= 2 else None
        if not country:
            raise RuntimeError(f"Could not parse issuer/country for row {number} in {fund.title}")
        issuer = identity[: -len(country)].strip()
        fractional_digits = re.sub(r"\D", "", weight_match.group(2))
        weight = float(f"{weight_match.group(1)}.{fractional_digits}")
        holdings.append(Holding(number, match.group(2), issuer, country, weight))
        expected_number = number + 1

    if not holdings:
        raise RuntimeError(f"No holdings parsed for {fund.title}")
    return holdings_date, holdings
