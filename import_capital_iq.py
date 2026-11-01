"""Turn the S&P Capital IQ screener export into clean per-issuer data for the dashboard.

    python import_capital_iq.py [path/to/export.xlsx]

Reads documents_emetteurs/CIQ_fondamentaux_*.xlsx (latest by default) and
data_sources/capital_iq_correspondance.json (our issuer id -> Capital IQ entity name),
writes data_sources/ciq_fondamentaux.json.

Notes on the export (checked on 01/11/2026 against the issuers' own H1 2026 publications):
- amounts are in thousands of USD (Capital IQ conversion), ratios are currency-free;
- bank ratios are SNL "as reported" values (CET1, total capital, Basel III leverage, LCR) and match
  the published figures; NPL and coverage use S&P definitions (coverage includes performing-loan
  reserves, so it is higher than the banks' own Stage 3 coverage);
- the "gross impaired loans" field was not consistent with published NPE ratios and is not used;
- Fitch fields are empty (not in the subscription).
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).parent
RATING_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
                "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
ACTION_FR = {"Upgrade": "Relèvement", "Downgrade": "Abaissement", "CreditWatch/Outlook": "Perspective / surveillance",
             "New Rating": "Nouvelle note", "Affirmation": "Confirmation"}
OUTLOOK_FR = {"Stable": "Stable", "Positive": "Positive", "Negative": "Négative", "Developing": "En évolution",
              "Watch Pos": "Surveillance positive", "Watch Neg": "Surveillance négative", "Watch Dev": "Surveillance en évolution"}


def clean(v):
    if v in (None, "NA", "NM", "", "-"):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return v


def first(row: dict, *keys):
    for k in keys:
        v = clean(row.get(k))
        if v is not None:
            return v
    return None


def parse_action(text):
    """'Upgrade | CreditWatch/Outlook (09/16/2026)' -> (['Relèvement', 'Perspective / surveillance'], '2026-09-16')."""
    if not text:
        return None
    m = re.search(r"\((\d\d)/(\d\d)/(\d{4})\)", text)
    when = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None
    kinds = [ACTION_FR.get(k.strip(), k.strip()) for k in re.sub(r"\(.*?\)", "", text).split("|") if k.strip()]
    return {"types": kinds, "date": when}


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else max((ROOT / "documents_emetteurs").glob("CIQ_fondamentaux_*.xlsx"))
    rows = list(openpyxl.load_workbook(src, data_only=True, read_only=True).worksheets[0].iter_rows(values_only=True))
    header = next(i for i, r in enumerate(rows) if r and r[0] == "SP_ENTITY_NAME")
    cols = [f"{a}|{p}" if p else str(a) for a, p in zip(rows[header], rows[header + 1])]
    seen = {}
    for c in cols:  # duplicated undated columns (local / foreign currency rating) -> keep the first
        seen[c] = seen.get(c, 0) + 1
    by_name = {}
    for r in rows[header + 2:]:
        if r and r[0]:
            d = {}
            for c, v in zip(cols, r):
                d.setdefault(c, v)
            by_name[r[0]] = d

    mapping = json.loads((ROOT / "data_sources" / "capital_iq_correspondance.json").read_text(encoding="utf-8"))
    out, missing = {}, []
    for issuer, name in mapping.items():
        row = by_name.get(name)
        if row is None:
            missing.append(issuer)
            continue
        rating = first(row, "RD_CREDIT_RATING_GLOBAL")
        rating_date = row.get("RD_CREDIT_RATING_DATE_GLOBAL")
        thousand = lambda *k: (round(first(row, *k) / 1000, 1) if first(row, *k) is not None else None)  # noqa: E731
        out[issuer] = {
            "entite": name,
            "secteur_sp": first(row, "MI_PRIMARY_INDUSTRY"),
            "pays_sp": first(row, "SP_COUNTRY_NAME"),
            "devise_publication": first(row, "IQ_CURRENCY_FINANCIALS|LTM", "IQ_CURRENCY_FINANCIALS|FY0"),
            "note_sp": rating if rating in RATING_SCALE else None,
            "note_sp_brute": rating,
            "rang_note": RATING_SCALE.index(rating) if rating in RATING_SCALE else None,
            "perspective_sp": OUTLOOK_FR.get(first(row, "RD_CWOL_GLOBAL"), first(row, "RD_CWOL_GLOBAL")),
            "derniere_action_sp": parse_action(first(row, "RD_RATING_ACTION_GLOBAL")),
            "date_note_sp": rating_date.date().isoformat() if isinstance(rating_date, datetime) else None,
            # companies (last twelve months, fallback fiscal year 2025)
            "dette_ebitda": first(row, "IQ_DEBT_TO_EBITDA|LTM", "IQ_DEBT_TO_EBITDA|FY0"),
            "dn_ebitda": first(row, "IQ_NET_DEBT_EBITDA|LTM", "IQ_NET_DEBT_EBITDA|FY0"),
            "ebitda_interets": first(row, "IQ_EBITDA_INT|LTM", "IQ_EBITDA_INT|FY0"),
            "dette_nette_musd": thousand("IQ_NET_DEBT|LTM", "IQ_NET_DEBT|FY0"),
            "ebitda_musd": thousand("IQ_EBITDA|LTM", "IQ_EBITDA|FY0"),
            "fcf_musd": thousand("IQ_LEVERED_FCF|LTM", "IQ_LEVERED_FCF|FY0"),
            # banks (latest quarter, fallback fiscal year 2025)
            "cet1": first(row, "SNL_T1_COMMON_CAPITAL_RATIO_RPTD_RC|FQ0", "SNL_T1_COMMON_CAPITAL_RATIO_RPTD_RC|FY0"),
            "ratio_global": first(row, "SNL_RISK_BASED_CAPITAL_RATIO_RPTD|FQ0", "SNL_RISK_BASED_CAPITAL_RATIO_RPTD|FY0"),
            "levier_bale3": first(row, "278093|FQ0", "278093|FY0"),
            "lcr": first(row, "SNL_LIQUIDITY_COV_RATIO|FQ0", "SNL_LIQUIDITY_COV_RATIO|FY0"),
            "npl": first(row, "SNL_NPL_TO_LOAN|FQ0", "SNL_NPL_TO_LOAN|FY0"),
            "couverture": first(row, "SNL_RESV_TO_NPL_AQ|FQ0", "SNL_RESV_TO_NPL_AQ|FY0"),
            "roe": first(row, "SNL_ROE|LTM", "SNL_ROE|FY0"),
            "prets_depots": first(row, "SNL_NET_CUSTOMER_LOANS_TO_DEPOSITS|FQ0", "SNL_NET_CUSTOMER_LOANS_TO_DEPOSITS|FY0"),
        }
    payload = {
        "source": f"S&P Capital IQ Pro (accès HEC Paris), export du {date.fromtimestamp(src.stat().st_mtime).strftime('%d/%m/%Y')}",
        "fichier": src.name,
        "echelle": RATING_SCALE,
        "emetteurs": out,
    }
    (ROOT / "data_sources" / "ciq_fondamentaux.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    rated = sum(1 for v in out.values() if v["note_sp"])
    banks = sum(1 for v in out.values() if v["cet1"] is not None)
    print(f"{len(out)} émetteurs importés ({rated} notés S&P, {banks} banques avec CET1), manquants : {missing}")


if __name__ == "__main__":
    main()
