"""Build the vendored symbol data.

EGX comes from the official exchange listing in `EGX.pdf` at the project root —
the upstream ticker database is missing major names (COMI, EAST) and stores
others in a way that will not match how a trader speaks. US comes from the
upstream database, which is complete for that market.

Run this to refresh `seed_data/`. The output is committed; the seed script reads
the committed files, so nothing is fetched at runtime.

    ./.venv/bin/python scripts/build_symbol_data.py

Sources: EGX.pdf (official EGX listing) and
https://github.com/adanos-software/free-ticker-database (MIT).
"""

import csv
import re
import sys
from pathlib import Path

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "seed_data"
EGX_PDF = ROOT / "EGX.pdf"

UPSTREAM = "https://raw.githubusercontent.com/adanos-software/free-ticker-database/main/data"

# The US market is not one exchange upstream: the listings are split across
# venues. OTC is deliberately excluded — pink-sheet rows would let a shell
# company's name shadow a real listing, and a miss is recoverable (the trader
# types the ticker once) while a wrong match is not.
US_VENUES = {"NASDAQ", "NYSE", "NYSE ARCA", "NYSE MKT", "BATS"}

# "COMI.CA EGS60121C018 Commercial International Bank (Egypt)" — the Reuters
# code, the ISIN, then the name, which runs until the next entry begins.
#
# Two shapes appear in the extraction, so both must match. Most rows are
# "COMI.CA EGS60121C018 Name", but some are glued with the ISIN truncated:
# "EGAS.CAEGS39011C Natural Gas & Mining Project (Egypt Gas)". That single row
# was silently dropped while the pattern demanded whitespace and 10+ ISIN
# characters, so the separators are `\s*` and the ISIN is allowed to be short.
# The lookahead is relaxed the same way, or a row following a glued one would
# not terminate. Verified by count: the PDF lists 215 tickers and the parser
# must keep all 215.
EGX_ROW = re.compile(
    r"([A-Z][A-Z0-9]{1,6})\.CA\.?\s*(EG[A-Z0-9]{5,14})\s+(.+?)"
    r"(?=\s+[A-Z][A-Z0-9]{1,6}\.CA\.?\s*EG|\Z)",
    re.S,
)


def normalize_name(name: str) -> str:
    """The trailing artefacts of the PDF's columns are not part of the name."""
    name = re.split(r"\s+#\s*\*|\s+\d+%", name)[0]
    name = re.sub(
        r"\s*[-–]\s*(In|I\.N\.)\s*(EGP|US Dollars?|USD|LE|\$).*$", "", name
    )
    return " ".join(name.split()).strip(" -")


def egx_rows() -> list[dict]:
    if not EGX_PDF.exists():
        raise SystemExit(f"missing {EGX_PDF} — the official EGX listing")
    reader = PdfReader(EGX_PDF)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    rows: dict[str, dict] = {}
    for match in EGX_ROW.finditer(text):
        ticker, isin = match.group(1), match.group(2)
        name = normalize_name(match.group(3))
        if len(name) < 3:
            continue
        rows.setdefault(
            ticker,
            {
                "market": "EGX",
                "ticker": ticker,
                "name": name,
                "exchange": "EGX",
                "isin": isin,
                "aliases": name.lower(),
            },
        )
    return [rows[key] for key in sorted(rows)]


def us_rows() -> list[dict]:
    print("downloading tickers.csv ...")
    text = httpx.get(f"{UPSTREAM}/tickers.csv", timeout=120.0).text
    rows = list(csv.DictReader(text.splitlines()))
    print(f"  {len(rows)} primary tickers upstream")
    return [
        {
            "market": "US",
            "ticker": row["ticker"],
            "name": row["name"],
            "exchange": row["exchange"],
            "isin": row.get("isin", ""),
            "aliases": row.get("aliases", ""),
        }
        for row in rows
        if row.get("exchange") in US_VENUES and row.get("asset_type") == "Stock"
    ]


def main() -> None:
    OUT.mkdir(exist_ok=True)

    egx = egx_rows()
    us = sorted(us_rows(), key=lambda r: (r["exchange"], r["ticker"]))
    symbols = egx + us
    print(f"  {len(egx)} EGX rows from EGX.pdf")

    tickers_out = OUT / "symbols.csv"
    with tickers_out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["market", "ticker", "name", "exchange", "isin", "aliases"],
        )
        writer.writeheader()
        writer.writerows(symbols)

    # Extra name aliases come from upstream, which only covers the tickers it
    # knows. The EGX names from the PDF are already in the `aliases` column.
    print("downloading aliases.csv ...")
    alias_text = httpx.get(f"{UPSTREAM}/aliases.csv", timeout=120.0).text
    wanted = {row["ticker"] for row in symbols}
    pairs = {
        (row["ticker"], row["alias"].strip())
        for row in csv.DictReader(alias_text.splitlines())
        if row.get("alias_type") == "name"
        and row.get("ticker") in wanted
        and row.get("alias", "").strip()
    }
    aliases_out = OUT / "symbol_aliases.csv"
    with aliases_out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "alias"])
        for ticker, alias in sorted(pairs):
            writer.writerow([ticker, alias])

    print(f"\nwrote {tickers_out}  ({len(symbols)} symbols)")
    for market in ("EGX", "US"):
        print(f"  {market}: {sum(1 for r in symbols if r['market'] == market)}")
    print(f"wrote {aliases_out}  ({len(pairs)} name aliases)")


if __name__ == "__main__":
    sys.exit(main())
