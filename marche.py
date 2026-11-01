"""Market indicators for the dashboard, from public sources only (ECB and FRED).

    python marche.py

Writes data_sources/marche.json: latest level and change over one month for each indicator.
"""

from __future__ import annotations

import io
import json
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
ECB = "https://data-api.ecb.europa.eu/service/data/{}?format=csvdata&startPeriod={}"
MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]


def download(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def ecb(key: str, start: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(download(ECB.format(key, start))))
    return df.pivot(index="TIME_PERIOD", columns="KEY", values="OBS_VALUE").sort_index()


def last_and_month_change(s: pd.Series) -> tuple[str, float, float]:
    """(date of last value, last value, change since the last value at least one month earlier)."""
    s = s.dropna()
    s.index = pd.to_datetime(s.index)
    last = s.index[-1]
    before = s[s.index <= last - pd.DateOffset(months=1)]
    return last.date().isoformat(), s.iloc[-1], s.iloc[-1] - before.iloc[-1]


def fr_number(v: float, d: int) -> str:
    return f"{v:.{d}f}".replace(".", ",")


def bp(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}{abs(round(v))} pb"


def main():
    start = (date.today() - timedelta(days=120)).isoformat()
    rates = ecb("YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y+SR_5Y", start)
    rates.columns = [c.split("_")[-1] for c in rates.columns]  # 2Y, 5Y
    dfr = ecb("FM/D.U2.EUR.4F.KR.DFR.LEV", start).iloc[:, 0]
    ten_year = ecb("IRS/M.FR+DE.L.L40.CI.0000.EUR.N.Z", start[:7])
    oat_bund = (ten_year["IRS.M.FR.L.L40.CI.0000.EUR.N.Z"] - ten_year["IRS.M.DE.L.L40.CI.0000.EUR.N.Z"]) * 100
    hy = pd.read_csv(io.StringIO(download("https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLHE00EHYIOAS")),
                     index_col=0, na_values=".").iloc[:, 0] * 100

    tiles = []
    for name, series, fmt in [
        ("Taux de dépôt BCE", dfr, "pct"),
        ("Taux souverain AAA 2 ans", rates["2Y"], "pct"),
        ("Taux souverain AAA 5 ans", rates["5Y"], "pct"),
        ("Pente AAA 2-5 ans", (rates["5Y"] - rates["2Y"]) * 100, "bp"),
        ("Spread OAT-Bund 10 ans", oat_bund, "bp"),
        ("Spread high yield €", hy, "bp"),
    ]:
        when, level, change = last_and_month_change(series)
        change_bp = change * 100 if fmt == "pct" else change
        tiles.append({"nom": name, "valeur": fr_number(level, 2) + " %" if fmt == "pct" else f"{round(level)} pb",
                      "variation": bp(change_bp), "date": when})
    tiles[4]["nom"] += f" ({MONTHS_FR[int(tiles[4]['date'][5:7]) - 1]})"  # monthly ECB average
    last = tiles[1]["date"]  # daily yield curve date
    payload = {
        "source": "BCE (taux de dépôt, courbe des taux souverains AAA de la zone euro, taux à 10 ans France et "
                  "Allemagne en moyenne mensuelle) ; ICE BofA Euro High Yield Index, spread ajusté des options (FRED)",
        "date": last,
        "libelle": f"au {int(last[8:10])} {MONTHS_FR[int(last[5:7]) - 1]} {last[:4]}, variation sur un mois",
        "indicateurs": tiles,
        "valeurs": {"taux_2a": tiles[1]["valeur"], "oat_bund": tiles[4]["valeur"], "oat_bund_var": tiles[4]["variation"],
                    "oat_bund_mois": MONTHS_FR[int(tiles[4]["date"][5:7]) - 1]},
    }
    (ROOT / "data_sources" / "marche.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for t in tiles:
        print(f"{t['nom']:38} {t['valeur']:>9} {t['variation']:>8}  {t['date']}")


if __name__ == "__main__":
    main()
