"""Weekly issuer watch for a short-term euro bond portfolio.

    python veille.py               # fetch news, analyse with Claude, write the report
    python veille.py --dry-run     # fetch news only (no API key needed), to check the sources
    python veille.py --days 14 --emetteur Intesa
"""

from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import date
from pathlib import Path

from fetch_news import fetch_issuer_news
from rapport import build_report

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description="Veille crédit hebdomadaire sur les émetteurs du portefeuille")
    parser.add_argument("--days", type=int, default=7, help="période couverte, en jours (défaut : 7)")
    parser.add_argument("--dry-run", action="store_true", help="collecte les titres sans appeler Claude")
    parser.add_argument("--emetteur", help="ne traiter que les émetteurs dont le nom contient ce texte")
    parser.add_argument("--no-open", action="store_true", help="ne pas ouvrir le rapport dans le navigateur")
    args = parser.parse_args()

    watchlist = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
    issuers = [
        i for i in watchlist["emetteurs"]
        if not args.emetteur or args.emetteur.lower() in i["nom"].lower()
    ]
    if not issuers:
        parser.error(f"aucun émetteur ne correspond à « {args.emetteur} »")

    client = None
    if not args.dry_run:
        import anthropic
        from analyse import analyse_issuer
        client = anthropic.Anthropic()

    run_date = date.today().isoformat()
    results = []
    for issuer in issuers:
        print(f"→ {issuer['nom']}")
        headlines = fetch_issuer_news(issuer, args.days)
        print(f"  {len(headlines)} titres")
        result = {"emetteur": issuer, "titres": headlines, "nb_titres": len(headlines)}
        if client is not None:
            try:
                result["note"] = analyse_issuer(client, issuer, headlines, watchlist["contexte"], args.days)
                print(f"  signal : {result['note']['signal']}")
            except anthropic.RateLimitError:
                result["erreur"] = "limite de requêtes atteinte, relancer plus tard"
            except anthropic.APIStatusError as exc:
                result["erreur"] = f"erreur API {exc.status_code}"
            except anthropic.APIConnectionError:
                result["erreur"] = "connexion à l'API impossible"
            except (RuntimeError, ValueError, KeyError) as exc:
                result["erreur"] = str(exc)
            if "erreur" in result:
                print(f"  ! {result['erreur']}")
        results.append(result)

    data_dir, report_dir = ROOT / "data", ROOT / "reports"
    data_dir.mkdir(exist_ok=True)
    report_dir.mkdir(exist_ok=True)
    suffix = "_test" if args.dry_run else ""
    (data_dir / f"veille_{run_date}{suffix}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = report_dir / f"veille_{run_date}{suffix}.html"
    report_path.write_text(build_report(results, watchlist, run_date, args.days, args.dry_run), encoding="utf-8")
    print(f"\nRapport : {report_path}")
    if not args.no_open:
        webbrowser.open(report_path.as_uri())


if __name__ == "__main__":
    main()
