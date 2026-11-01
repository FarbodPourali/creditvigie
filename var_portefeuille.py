"""Value at Risk of a bond portfolio from the CreditVigie universe, historical and parametric.

    python var_portefeuille.py            # downloads the latest market history (ECB, FRED)
    python var_portefeuille.py --cache    # reuses data_sources/var_historique.csv
    python var_portefeuille.py --public   # without S&P ratings (licensed data): only hybrids count as high yield

Model (simple factor model, the same idea as a sensitivity-based VaR in a risk system):
- each bond's daily return = -duration x (change in the euro rate for its maturity + change in its credit spread);
- rates: ECB euro area AAA government yield curve, spot rates 1, 2, 3 and 5 years (each bond uses the
  maturity closest to its time to call);
- spreads: ICE BofA Euro High Yield index option-adjusted spread (FRED, BAMLHE00EHYIOAS). Each bond's spread
  is assumed to move in proportion to its level ("Duration Times Spread", Ben Dor et al., 2007):
  change = own spread x (relative change of the index spread). Spread levels are rounded assumptions for
  short-dated euro credit: 60 bp for investment grade, 200 bp for high yield, AT1 and hybrids;
- sovereigns move with the AAA curve only: the BTP-Bund or OAT-Bund spread is not modelled (limit of the model).

Historical VaR: the loss exceeded on only 5% (or 1%) of the days of the last ~3 years, replaying each past day's
market moves on today's portfolio. Parametric VaR: z x standard deviation, assuming normal returns.
Writes data_sources/var_facteurs.json, used by the dashboard to compute the VaR of any portfolio.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
TENORS = ["1Y", "2Y", "3Y", "5Y"]
TENOR_YEARS = np.array([1, 2, 3, 5])
SPREAD_IG, SPREAD_HY = 60, 200  # bp, assumed levels for short-dated euro investment grade and high yield
Z = {95: 1.645, 99: 2.326}
CACHE = ROOT / "data_sources" / "var_historique.csv"


def download(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read().decode("utf-8")


def market_history() -> pd.DataFrame:
    """Daily levels: AAA spot rates (%) and the euro high yield spread (%), on days where both exist."""
    start = (date.today() - timedelta(days=4 * 365)).isoformat()
    key = "B.U2.EUR.4F.G_N_A.SV_C_YM." + "+".join("SR_" + t for t in TENORS)
    ecb = pd.read_csv(io.StringIO(download(f"https://data-api.ecb.europa.eu/service/data/YC/{key}?format=csvdata&startPeriod={start}")))
    rates = ecb.pivot(index="TIME_PERIOD", columns="DATA_TYPE_FM", values="OBS_VALUE").rename(columns=lambda c: c[3:])
    fred = pd.read_csv(io.StringIO(download("https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLHE00EHYIOAS")),
                       index_col=0, na_values=".")
    hy = fred.iloc[:, 0].rename("HY")
    df = rates[TENORS].join(hy, how="inner").dropna()
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def factor_moves(levels: pd.DataFrame) -> pd.DataFrame:
    """Daily changes: rates in basis points, high yield spread as a relative change (+0.02 = spread up 2 %)."""
    moves = levels[TENORS].diff() * 100
    moves["HY_rel"] = levels["HY"].pct_change()
    return moves.dropna()


def line_exposure(line: dict, rating_rank) -> tuple[int, float]:
    """(index of the rate maturity used, credit spread level in bp) for one bond."""
    years = line.get("annees_call") or line.get("annees") or line["duration"]
    tenor = int(np.abs(TENOR_YEARS - years).argmin())
    if not line["credit"]:
        return tenor, 0.0
    high_yield = line["type"] == "Hybride perpétuelle" or (rating_rank is not None and rating_rank > 9)  # 9 = BBB-
    return tenor, float(SPREAD_HY if high_yield else SPREAD_IG)


def load_universe() -> dict:
    return json.loads((ROOT / "dashboard" / "data.js").read_text(encoding="utf-8")[len("window.DATA = "):-2])


def example_positions(data: dict) -> list[dict]:
    """The dashboard's 'Exemple diversifié' portfolio: issuer weight split equally across its senior bonds."""
    by_id = {e["id"]: e for e in data["emetteurs"]}
    out = []
    for issuer, w in data["presets"]["exemple"].items():
        lines = list(enumerate(by_id[issuer]["lignes"]))
        chosen = [(i, l) for i, l in lines if l["type"] != "Hybride perpétuelle"] or lines
        out += [{**l, "id": issuer, "i": i, "w": w / len(chosen)} for i, l in chosen]
    return out


def portfolio_returns(positions: list[dict], moves: pd.DataFrame, exposures: dict, rates=True, spreads=True) -> pd.Series:
    """Daily return of the portfolio in % of its total value, replaying each past day on today's holdings."""
    total = pd.Series(0.0, index=moves.index)
    for p in positions:
        tenor, spread = exposures[p["id"]][p["i"]]
        move_bp = (moves[TENORS[tenor]] if rates else 0) + (spread * moves["HY_rel"] if spreads else 0)
        total += p["w"] / 100 * -p["duration"] * move_bp / 100
    return total


def var_report(r: pd.Series) -> dict:
    q95, q99 = np.percentile(r, 5), np.percentile(r, 1)
    return {
        "var95": -q95, "var99": -q99,
        "es99": -r[r <= q99].mean(),
        "param95": Z[95] * r.std(), "param99": Z[99] * r.std(),
        "pire_jour": r.min(), "date_pire_jour": r.idxmin().date().isoformat(),
    }


def backtest(r: pd.Series, window: int = 250) -> tuple[int, int]:
    """Rolling check: 99% VaR from the previous 250 days vs the next day's return. Returns (breaches, days tested)."""
    breaches = int(sum(r.iloc[t] < np.percentile(r.iloc[t - window:t], 1) for t in range(window, len(r))))
    return breaches, len(r) - window


def main():
    if "--cache" in sys.argv and CACHE.exists():
        levels = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        levels = market_history()
        levels.to_csv(CACHE)
    moves = factor_moves(levels)

    data = load_universe()
    public = "--public" in sys.argv
    ranks = {e["id"]: None if public else (e.get("ciq") or {}).get("rang_note") for e in data["emetteurs"]}
    exposures = {e["id"]: [line_exposure(l, ranks[e["id"]]) for l in e["lignes"]] for e in data["emetteurs"]}

    # Example portfolio, as a check of the numbers shown in the dashboard
    positions = example_positions(data)
    r = portfolio_returns(positions, moves, exposures)
    rep = var_report(r)
    rates_only = var_report(portfolio_returns(positions, moves, exposures, spreads=False))["var99"]
    spreads_only = var_report(portfolio_returns(positions, moves, exposures, rates=False))["var99"]
    breaches, tested = backtest(r)

    first, last = moves.index[0].date(), moves.index[-1].date()
    print(f"Historique : {len(moves)} jours, du {first:%d/%m/%Y} au {last:%d/%m/%Y}")
    print(f"Portefeuille « Exemple diversifié » ({sum(p['w'] for p in positions):.0f} % investis)")
    print(f"  VaR historique 1 jour  95 % : {rep['var95']:.3f} %   99 % : {rep['var99']:.3f} %")
    print(f"  Expected shortfall 99 %    : {rep['es99']:.3f} %")
    print(f"  VaR paramétrique   95 % : {rep['param95']:.3f} %   99 % : {rep['param99']:.3f} %")
    print(f"  Volatilité ex-ante annualisée : {r.std() * 252 ** 0.5:.2f} %")
    print(f"  VaR 99 % à 10 jours (racine du temps) : {rep['var99'] * 10 ** 0.5:.3f} %")
    print(f"  Taux seuls : {rates_only:.3f} %   spreads seuls : {spreads_only:.3f} %")
    print(f"  Pire jour : {rep['pire_jour']:.3f} % le {rep['date_pire_jour']}")
    print(f"  Backtest : {breaches} dépassements sur {tested} jours (attendu ≈ {tested / 100:.1f})")

    payload = {
        "source": "BCE, courbe des taux AAA de la zone euro (taux zéro-coupon 1, 2, 3 et 5 ans) ; "
                  "ICE BofA Euro High Yield Index, spread ajusté des options (FRED, BAMLHE00EHYIOAS)",
        "debut": first.isoformat(), "fin": last.isoformat(),
        "spreads_reference": {"ig": SPREAD_IG, "hy": SPREAD_HY},
        "maturites": TENORS,
        "dates": [d.date().isoformat() for d in moves.index],
        "taux_pb": [[round(x, 2) for x in moves[t]] for t in TENORS],
        "hy_rel": [round(x, 5) for x in moves["HY_rel"]],
        "expositions": exposures,
        "backtest_exemple": {"depassements": breaches, "jours": tested},
    }
    (ROOT / "data_sources" / ("var_facteurs_public.json" if public else "var_facteurs.json")).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
