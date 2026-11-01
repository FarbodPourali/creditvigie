"""Turn the weekly notes into one self-contained HTML page (opens in any browser, prints cleanly to PDF)."""

from __future__ import annotations

from html import escape

SIGNAL_LABEL = {"vert": "Rien à signaler", "orange": "À suivre", "rouge": "Alerte", None: "Non analysé"}
IMPACT_LABEL = {"positif": "Positif", "neutre": "Neutre", "negatif": "Négatif", "incertain": "Incertain"}
CATEGORY_LABEL = {
    "notation": "Notation", "resultats": "Résultats", "capital_liquidite": "Capital / liquidité",
    "fusion_acquisition": "M&A", "emission_refinancement": "Émission / refinancement",
    "hybride_call": "Hybride / call", "reglementaire_juridique": "Réglementaire / juridique",
    "souverain_macro": "Souverain / macro", "esg": "ESG", "autre": "Autre",
}
CONFIANCE_LABEL = {"elevee": "confiance élevée", "moyenne": "confiance moyenne", "faible": "confiance faible, à vérifier"}
IMPORTANCE_ORDER = {"haute": 0, "moyenne": 1, "basse": 2}
SIGNAL_ORDER = {"rouge": 0, "orange": 1, "vert": 2, None: 3}

CSS = """
:root { --ink:#1d2733; --muted:#5d6875; --line:#dde3ea; --bg:#ffffff; --panel:#f5f7fa; --accent:#0b3a5b;
        --vert:#2e7d4f; --orange:#c77700; --rouge:#b3261e; --gris:#8a94a0; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width:860px; margin:0 auto; padding:32px 16px 48px; }
h1 { font-size:24px; margin:0 0 4px; color:var(--accent); }
h2 { font-size:18px; margin:0; }
.sub { color:var(--muted); font-size:13px; margin:0 0 20px; }
table { width:100%; border-collapse:collapse; font-size:14px; margin-bottom:28px; }
th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:middle; }
.vert { background:var(--vert); } .orange { background:var(--orange); } .rouge { background:var(--rouge); } .none { background:var(--gris); }
section.issuer { border:1px solid var(--line); border-radius:8px; padding:16px; margin-bottom:18px; break-inside:avoid; }
.head { display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:baseline; }
.meta { color:var(--muted); font-size:13px; }
.resume { background:var(--panel); border-radius:6px; padding:10px 12px; margin:10px 0; }
.point { border-top:1px solid var(--line); padding:10px 0 4px; }
.tags { font-size:12px; color:var(--muted); margin-bottom:2px; }
.tag { display:inline-block; border:1px solid var(--line); border-radius:4px; padding:0 6px; margin-right:4px; }
.neg { color:var(--rouge); border-color:var(--rouge); } .pos { color:var(--vert); border-color:var(--vert); }
.src { font-size:12px; color:var(--muted); margin:4px 0 0; padding-left:16px; }
.src a { color:var(--accent); }
.watch { font-size:14px; margin:8px 0 0; }
.foot { color:var(--muted); font-size:12px; border-top:1px solid var(--line); padding-top:12px; margin-top:28px; }
@media print { main { padding:0; } a { color:inherit; text-decoration:none; } }
"""


def _dot(signal):
    return f'<span class="dot {signal or "none"}"></span>'


def _date(iso):
    return f"{iso[8:10]}/{iso[5:7]}" if iso else ""


def _point_html(p):
    impact_cls = {"negatif": "neg", "positif": "pos"}.get(p["impact_credit"], "")
    sources = "".join(
        f'<li><a href="{escape(s["url"])}">{escape(s["titre"])}</a> · {escape(s["source"])} {_date(s["date"])}</li>'
        for s in p["sources"]
    )
    return f"""<div class="point">
<div class="tags"><span class="tag">{CATEGORY_LABEL.get(p['categorie'], p['categorie'])}</span><span class="tag {impact_cls}">Impact {IMPACT_LABEL[p['impact_credit']].lower()}</span><span class="tag">Importance {p['importance']}</span>{CONFIANCE_LABEL[p['confiance']]}</div>
<strong>{escape(p['titre'])}</strong>
<div>{escape(p['explication'])}</div>
<ul class="src">{sources}</ul>
</div>"""


def _issuer_html(r):
    issuer, note = r["emetteur"], r.get("note")
    lignes = " · ".join(f"{escape(l['titre'])} ({escape(l['poids'])})" for l in issuer["lignes"])
    head = f"""<div class="head"><h2>{_dot(note and note['signal'])}{escape(issuer['nom'])}</h2>
<span class="meta">{escape(issuer['pays'])} · {escape(issuer['secteur'])} · {r['nb_titres']} titres lus</span></div>
<div class="meta">Lignes : {lignes}</div>"""

    if r.get("erreur"):
        return f'<section class="issuer">{head}<div class="resume">Analyse impossible : {escape(r["erreur"])}</div></section>'

    if note is None:  # dry run: show the raw headlines so the fetch step can be checked without the API
        items = "".join(
            f'<li><a href="{escape(h["url"])}">{escape(h["titre"])}</a> · {escape(h["source"])} {_date(h["date"])}</li>'
            for h in r["titres"]
        ) or "<li>Aucun titre sur la période.</li>"
        return f'<section class="issuer">{head}<ul class="src">{items}</ul></section>'

    points = sorted(note["points"], key=lambda p: IMPORTANCE_ORDER[p["importance"]])
    watch = "".join(f"<li>{escape(w)}</li>" for w in note["a_surveiller"])
    return f"""<section class="issuer">{head}
<div class="resume"><strong>{SIGNAL_LABEL[note['signal']]}.</strong> {escape(note['resume'])}</div>
{''.join(_point_html(p) for p in points)}
{f'<div class="watch"><strong>À surveiller</strong><ul>{watch}</ul></div>' if watch else ''}
<div class="meta">{note['titres_ignores']} titres écartés comme sans intérêt crédit.</div>
</section>"""


def build_report(results: list[dict], watchlist: dict, run_date: str, days: int, dry_run: bool) -> str:
    results = sorted(results, key=lambda r: SIGNAL_ORDER[(r.get("note") or {}).get("signal")])
    rows = "".join(
        f"<tr><td>{_dot((r.get('note') or {}).get('signal'))}{escape(r['emetteur']['nom'])}</td>"
        f"<td>{SIGNAL_LABEL[(r.get('note') or {}).get('signal')]}</td>"
        f"<td>{escape((r.get('note') or {}).get('resume', 'Mode test : titres bruts ci-dessous.' if dry_run else ''))}</td></tr>"
        for r in results
    )
    total = sum(r["nb_titres"] for r in results)
    mode = " · mode test (sans analyse IA)" if dry_run else ""
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Veille émetteurs {run_date}</title><style>{CSS}</style></head>
<body><main>
<h1>Veille émetteurs : semaine du {run_date[8:10]}/{run_date[5:7]}/{run_date[:4]}</h1>
<p class="sub">{escape(watchlist['portefeuille'])} · {len(results)} émetteurs · {total} titres de presse lus sur {days} jours{mode}</p>
<table><thead><tr><th>Émetteur</th><th>Signal</th><th>En une phrase</th></tr></thead><tbody>{rows}</tbody></table>
{''.join(_issuer_html(r) for r in results)}
<p class="foot">Lignes du portefeuille tirées du rapport mensuel public ({escape(watchlist['source'])}).
Titres collectés via Google Actualités, lus et classés par Claude ({'non utilisé' if dry_run else 'claude-opus-5-5'}).
Outil de démonstration construit par Farbod Pourali sur données publiques. Ce n'est pas un conseil d'investissement :
chaque point doit être vérifié à la source avant toute décision.</p>
</main></body></html>"""
