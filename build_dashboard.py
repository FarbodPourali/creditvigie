"""Build the issuer universe (bonds, durations, fundamentals, weekly analysis, news) into dashboard/data.js.

    python build_dashboard.py

All the arithmetic the page needs (durations, maturities, issuer aggregates) is done here, deterministically,
so the page only displays and recombines numbers.
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
VALUATION = date.today()
YIELD = 0.0389  # fund average yield, used as discount rate for duration estimates
HYBRID_DURATION = 1.03  # published sensitivity of the hybrid bucket (monthly report, Sept 2026)

ALIASES = {"EUROBANK ERGASIA": "EUROBANK", "TEVA PHARM FNC": "TEVA PHARMACEUTI", "KORIAN SA": "CLARIANE SE"}
UNIVERSE = json.loads((ROOT / "data_sources" / "univers_emetteurs.json").read_text(encoding="utf-8"))
DISPLAY = {k: v[0] for k, v in UNIVERSE["emetteurs"].items()}
COUNTRY = {k: v[1] for k, v in UNIVERSE["emetteurs"].items()}
# watchlist names (veille.py) -> universe issuer, to attach the collected headlines
NEWS_KEYS = {"Intesa Sanpaolo": "INTESA SANPAOLO", "Banca Monte dei Paschi di Siena": "MONTE DEI PASCHI",
             "Commerzbank": "COMMERZBANK AG", "Volkswagen Bank / Volkswagen Financial Services": "VOLKSWAGEN BANK",
             "Unibail-Rodamco-Westfield": "UNIBAIL-RODAMCO", "OTP Bank": "OTP BANK"}

# Neutral example portfolio (weights in %, the rest is cash). Not the fund's weights.
PRESET_EXAMPLE = {  # 40 issuers, 97 % invested
    "ITALY BTPS": 8, "SPANISH GOVT": 7, "EUROPEAN UNION": 5,
    "BNP PARIBAS": 3, "INTESA SANPAOLO": 3, "CAIXABANK": 3, "UNICREDIT SPA": 3, "BANCO BILBAO VIZ": 3,
    "MONTE DEI PASCHI": 3, "BANQ CAISSE EPAR": 3, "ING BANK NV": 2, "DNB BANK ASA": 2, "CRED AGRICOLE SA": 2,
    "OTP BANK": 2, "BANCO SANTANDER": 2, "COMMERZBANK AG": 1,
    "ENEL SPA": 3, "IBERDROLA FIN SA": 3, "ELEC DE FRANCE": 3, "TERNA RETE": 1, "NATURGY FINANCE": 1,
    "ORANGE": 2, "DEUTSCHE TELEKOM": 2, "CELLNEX TELECOM": 2, "TELEFONICA EUROP": 1, "KONINKLIJKE KPN": 1,
    "LOREAL SA": 2, "ALPHABET INC": 2, "SCHNEIDER ELEC": 2, "NEXANS SA": 2, "RCI BANQUE": 2, "VINCI SA": 2,
    "CIE DE ST GOBAIN": 2, "ESSILORLUXOTTICA": 2, "CAPGEMINI SE": 2, "AYVENS SA": 2, "UNIBAIL-RODAMCO": 2,
    "AKZO NOBEL NV": 2, "VOLKSWAGEN INTFN": 1, "VEOLIA ENVRNMT": 1,
}
PRESET_DEFENSIVE = {"ITALY BTPS": 10, "SPANISH GOVT": 10, "EUROPEAN UNION": 10, "BUNDESOBLIGATION": 10,
                    **{k: 5 for k in ("EURONEXT NV", "IBERDROLA FIN SA", "COMPASS GROUP", "LOREAL SA", "ALPHABET INC",
                                      "SCHNEIDER ELEC", "ORANGE", "DEUTSCHE TELEKOM", "VINCI SA", "CAPGEMINI SE",
                                      "ESSILORLUXOTTICA", "ADIDAS AG")}}


def pretty(issuer: str) -> str:
    return DISPLAY.get(issuer) or " ".join(w.capitalize() if len(w) > 3 else w for w in issuer.split())


def modified_duration(years: float, coupon: float | None) -> float:
    """Modified duration of an annual-coupon bullet bond priced at the fund's yield."""
    if years <= 0:
        return 0.0
    y = YIELD
    c = (coupon / 100) if coupon is not None else y
    if c <= 0:
        macaulay = years
    else:
        macaulay = (1 + y) / y - (1 + y + years * (c - y)) / (c * ((1 + y) ** years - 1) + y)
    return macaulay / (1 + y)


def enrich(line: dict) -> dict:
    line["emetteur"] = ALIASES.get(line["emetteur"], line["emetteur"])
    years = None
    if line["echeance"]:
        years = (date.fromisoformat(line["echeance"]) - VALUATION).days / 365.25
    line["annees"] = round(years, 2) if years is not None else None
    line["echue"] = years is not None and years <= 0
    if years is not None and years > 15 and line["secteur"] != "Souverains":
        line["type"] = "Hybride perpétuelle"  # e.g. 2049/2061 corporate hybrids: priced to call, not to maturity
        line["echeance"] = None
    t = line["type"]
    if t == "OPCVM" or line["echue"]:
        d = 0.0
    elif t == "Hybride perpétuelle":
        d = HYBRID_DURATION
    elif t == "Callable / FRN":
        # Assumption: 10-year-plus bank papers are 10NC5 (Tier 2), shorter ones callable one year before maturity.
        tenor = date.fromisoformat(line["echeance"]).year - (line["annee_emission"] or VALUATION.year)
        to_call = years - (tenor - 5) if tenor >= 10 else years - 1
        d = modified_duration(max(to_call, 0.25), line["coupon"])
        line["annees_call"] = round(max(to_call, 0.25), 2)
    else:
        d = modified_duration(years, line["coupon"])
    line["duration"] = round(d, 2)
    if "annees_call" not in line:
        line["annees_call"] = HYBRID_DURATION if t == "Hybride perpétuelle" else line["annees"]
    line["credit"] = line["secteur"] not in ("Souverains", "Fonds monétaires")
    line["banque"] = line["secteur"] == "Banques"
    if t == "Hybride perpétuelle" and "-" in line["libelle"] and "/" in line["libelle"]:  # day/month in the name = call/reset anniversary
        dd, mm = map(int, line["libelle"].split("-", 1)[1][:5].split("/"))
        if (dd, mm) == (31, 12):
            line["anniversaire_call"] = None  # 31/12 is a placeholder perpetual date, not a call date
        else:
            nxt = date(VALUATION.year, mm, dd)
            if nxt <= VALUATION:
                nxt = date(VALUATION.year + 1, mm, dd)
            line["anniversaire_call"] = nxt.isoformat()
    return line


# Our own sector classification where the report's categories are misleading (towers, gaming, packaging...).
SECTOR_FIX = {
    "CELLNEX TELECOM": "Télécoms", "INFRASTRUTTURE W": "Télécoms", "TDF INFRASTRUCTU": "Télécoms", "ILIAD": "Télécoms",
    "CZECHOSLOVAK GRO": "Industrie et défense",
    "ALLWYN ENTERTAIN": "Jeux et loisirs", "FLUTTER TREASURY": "Jeux et loisirs", "LOTTO GROUP": "Jeux et loisirs",
    "NEXI": "Services financiers",
    "BALL CORP": "Emballage", "CROWN EUROPEAN": "Emballage", "HUHTAMAKI OYJ": "Emballage",
    "BARRY CALLE SVCS": "Agroalimentaire", "BEL SA": "Agroalimentaire",
    "CAPGEMINI SE": "Services informatiques",
    "HOLDING DINFRAS": "Infrastructures de transport", "ATLANTIA SPA": "Infrastructures de transport",
}
SECTOR_RENAME = {"Matériaux de construction": "Construction et matériaux"}
# Headlines that only share a name with the issuer (football league, car reviews, stock quote pages).
NEWS_NOISE = re.compile(r"\bLiga\b|\bFC\b|Football|Trim Comparison|\bvs\b|Company Profile|Stock Baskets|"
                        r"Historical Valuations|\breview\b|Cours Action|Cotation|target for .* stock|keeps EUR", re.I)


def latest_news() -> dict:
    files = sorted(glob.glob(str(ROOT / "data" / "veille_*.json")))
    if not files:
        return {}
    news = {}
    for r in json.loads(Path(files[-1]).read_text(encoding="utf-8")):
        key = NEWS_KEYS.get(r["emetteur"]["nom"])
        if key:
            words = [w for w in (pretty(key) + " " + key).lower().replace("-", " ").split() if len(w) >= 4]
            named = [h for h in r["titres"] if any(w in h["titre"].lower() for w in words) and not NEWS_NOISE.search(h["titre"])]
            news[key] = [{"titre": h["titre"], "source": h["source"], "url": h["url"], "date": (h["date"] or "")[:10]}
                         for h in named[:8]]
    return news


def september_additions() -> list[dict]:
    """Bonds bought in September (monthly report): coupon and maturity year only, so the date is approximate."""
    out = []
    for a in UNIVERSE["ajouts_septembre"]:
        out.append({"libelle": a["libelle"], "emetteur": a["id"], "secteur": a.get("secteur"), "type": a["type"],
                    "coupon": a["coupon"], "echeance": a["echeance"], "annee_emission": 2026, "score_esg": None,
                    "approx": True})
        if a.get("nom"):
            DISPLAY[a["id"]], COUNTRY[a["id"]] = a["nom"], a["pays"]
    return out


PUBLIC = "--public" in sys.argv  # GitHub version: S&P ratings only, no Capital IQ ratios
RATING_FIELDS = ("note_sp", "note_sp_brute", "rang_note", "perspective_sp", "derniere_action_sp", "date_note_sp")
PUBLIC_DIR = ROOT.parent / "CreditVigie_github"


def main():
    portfolio = json.loads((ROOT / "data" / "portefeuille.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / "data_sources" / "marche.json").read_text(encoding="utf-8"))  # marche.py (ECB, FRED)
    weekly = json.loads((ROOT / "data_sources" / "analyse_semaine.json").read_text(encoding="utf-8"))
    fill = lambda t: t.format_map(market["valeurs"])  # noqa: E731  market figures quoted in the weekly texts
    weekly["a_retenir"] = [fill(t) for t in weekly["a_retenir"]]
    for a in weekly["alertes"]:
        a["titre"], a["texte"] = fill(a["titre"]), fill(a["texte"])
    fundamentals = json.loads((ROOT / "data_sources" / "fondamentaux.json").read_text(encoding="utf-8"))
    ciq_path = ROOT / "data_sources" / "ciq_fondamentaux.json"
    ciq = json.loads(ciq_path.read_text(encoding="utf-8")) if ciq_path.exists() else {"emetteurs": {}, "source": None, "echelle": []}
    if PUBLIC:
        ciq = {"emetteurs": {k: {f: v[f] for f in RATING_FIELDS} for k, v in ciq["emetteurs"].items()},
               "source": "notations S&P Global Ratings (note d'émetteur long terme), relevées le " + ciq["source"].rsplit("du ", 1)[-1],
               "echelle": ciq["echelle"], "notes_seulement": True}
    var_path = ROOT / "data_sources" / "var_facteurs.json"  # var_portefeuille.py
    var = json.loads(var_path.read_text(encoding="utf-8")) if var_path.exists() else None
    raw = [l for l in portfolio["lignes"] if l["type"] != "OPCVM"] + september_additions()
    sectors = {ALIASES.get(l["emetteur"], l["emetteur"]): l["secteur"] for l in raw if l.get("secteur")}
    lines = []
    for l in raw:
        key = ALIASES.get(l["emetteur"], l["emetteur"])
        l["secteur"] = SECTOR_FIX.get(key) or l.get("secteur") or sectors.get(key)
        l["secteur"] = SECTOR_RENAME.get(l["secteur"], l["secteur"])
        l = enrich(l)
        if not l["echue"]:
            lines.append(l)
    news = latest_news()

    issuers = defaultdict(lambda: {"lignes": []})
    for l in lines:
        i = issuers[l["emetteur"]]
        i["lignes"].append(l)
        i["secteur"] = l["secteur"]
    signals = {}
    for a in weekly["alertes"]:
        for e in a["emetteurs"]:
            if signals.get(e) != "alerte":
                signals[e] = a["niveau"]
    keep = ("libelle", "type", "coupon", "echeance", "annees", "annees_call", "duration", "credit", "banque", "annee_emission",
            "anniversaire_call", "approx")
    emetteurs = sorted(
        ({"id": k, "nom": pretty(k), "pays": COUNTRY.get(k, "n.d."), "secteur": v["secteur"],
          "signal": signals.get(k, "ras"), "actualites": news.get(k, []),
          "lignes": [{kk: l[kk] for kk in keep if kk in l} for l in v["lignes"]]}
         for k, v in issuers.items()),
        key=lambda e: e["nom"].lower(),
    )
    by_id = {e["id"]: e for e in emetteurs}
    for kind in ("banques", "entreprises"):
        for f in fundamentals[kind]:
            by_id[f["id"]]["fondamentaux"] = {**f, "type": kind}
    for e in emetteurs:
        if e["id"] in ciq["emetteurs"]:
            e["ciq"] = ciq["emetteurs"][e["id"]]
    for preset in (PRESET_EXAMPLE, PRESET_DEFENSIVE):
        missing = [k for k in preset if k not in by_id]
        assert not missing, f"preset issuers not in universe: {missing}"
    data = {
        "genere_le": VALUATION.isoformat(),
        "marche": {k: market[k] for k in ("source", "date", "libelle", "indicateurs")},
        "semaine": weekly,
        "univers": {"source": "rapport semestriel public d'un fonds obligataire euro court terme (positions au 30/06/2026)" if PUBLIC
                    else portfolio["source"].split(" et inventaire")[0],
                    "date": portfolio["date_positions"]},
        "presets": {"exemple": PRESET_EXAMPLE, "defensif": PRESET_DEFENSIVE},
        "emetteurs": emetteurs,
        "fondamentaux": {k: fundamentals[k] for k in ("periode", "collecte", "methode", "points_cles")},
        "ciq": {"source": ciq["source"], "echelle": ciq["echelle"], "notes_seulement": ciq.get("notes_seulement", False)},
        "var": var,
    }
    payload = "window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
    if not PUBLIC:
        (ROOT / "dashboard" / "data.js").write_text(payload, encoding="utf-8")
    # Single-file version (data inlined): opens anywhere, can be attached to an email or published as one page.
    page = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
    standalone = page.replace('<script src="data.js"></script>', "<script>" + payload.replace("</", "<\\/") + "</script>")
    if PUBLIC:
        (PUBLIC_DIR / "docs").mkdir(parents=True, exist_ok=True)
        (PUBLIC_DIR / "docs" / "index.html").write_text(standalone, encoding="utf-8")
    else:
        (ROOT / "dashboard" / "veille_credit_autonome.html").write_text(standalone, encoding="utf-8")
    print(f"{len(emetteurs)} émetteurs, {len(lines)} lignes, pays manquants : "
          f"{[e['id'] for e in emetteurs if e['pays'] == 'n.d.']}, actualités pour {len(news)} émetteurs")


if __name__ == "__main__":
    main()
