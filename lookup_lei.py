"""Find each issuer's LEI in the public GLEIF registry, for the Capital IQ list upload.

    python lookup_lei.py

Writes documents_emetteurs/capital_iq_import_LEI.csv (one LEI per line, ready to upload)
and documents_emetteurs/lei_controle.csv (issuer, LEI, registry name, confidence) to review.
"""

from __future__ import annotations

import csv
import json
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
API = "https://api.gleif.org/api/v1/lei-records?"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def query(params: dict) -> list[dict]:
    url = API + urllib.parse.urlencode({**params, "filter[entity.status]": "ACTIVE", "page[size]": "10"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/vnd.api+json"}), timeout=30) as r:
                time.sleep(1.1)  # GLEIF asks for at most 60 requests per minute
                return json.loads(r.read())["data"]
        except Exception:
            time.sleep(3)
    return []


SUFFIX = {"SA", "S", "A", "SPA", "P", "AG", "SE", "NV", "N", "V", "PLC", "INC", "BV", "B", "ASA", "AS", "AB", "OYJ",
          "SOCIETA", "PER", "AZIONI", "AKTIENGESELLSCHAFT", "PUBLIC", "LIMITED", "COMPANY", "GMBH", "LLC", "CORP",
          "CORPORATION", "CO", "SAS", "D", "O", "IN", "FORMA", "ABBREVIATA", "GROUPE", "GROUP"}


def core(s: str) -> str:
    return " ".join(t for t in norm(s).split() if t not in SUFFIX)


def pick_core(records: list[dict], names: list[str]) -> tuple[dict | None, str]:
    wanted = {core(n) for n in names}
    hits = [r for r in records if r["attributes"]["entity"].get("category") not in ("FUND", "BRANCH")
            and core(r["attributes"]["entity"]["legalName"]["name"]) in wanted]
    hq = [r for r in hits if not (r["attributes"]["entity"].get("associatedEntity") or {}).get("lei")]
    if len(hits) == 1:
        return hits[0], "élevée"
    if hits:
        return (hq or hits)[0], "à vérifier (plusieurs entités du même nom)"
    return None, "introuvable"


def pick(records: list[dict], names: list[str]) -> tuple[dict | None, str]:
    wanted = {norm(n) for n in names}
    general = [r for r in records if r["attributes"]["entity"].get("category") not in ("FUND", "BRANCH")]
    exact = [r for r in general if norm(r["attributes"]["entity"]["legalName"]["name"]) in wanted]
    if len(exact) == 1:
        return exact[0], "élevée"
    if len(exact) > 1:
        return exact[0], "à vérifier (plusieurs entités du même nom)"
    if len(general) == 1:
        return general[0], "à vérifier (nom différent)"
    return None, "introuvable"


def main():
    names = json.loads((ROOT / "data_sources" / "noms_legaux.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "dashboard" / "data.js").read_text(encoding="utf-8")[len("window.DATA = "):-2])
    issuers = [e for e in data["emetteurs"] if e["secteur"] not in ("Souverains", "Fonds monétaires")]
    rows = []
    for e in sorted(issuers, key=lambda x: x["nom"]):
        legal, country = names.get(e["id"], [[e["nom"]], None])
        rec, conf = None, "introuvable"
        for n in legal:
            params = {"filter[entity.legalName]": n}
            if country:
                params["filter[entity.legalAddress.country]"] = country
            rec, conf = pick(query(params), legal)
            if rec:
                break
        if conf != "élevée":  # second chance: full-text search, companies only, names compared without legal suffix
            params = {"filter[fulltext]": core(legal[0]), "filter[entity.category]": "GENERAL"}
            if country:
                params["filter[entity.legalAddress.country]"] = country
            rec2, conf2 = pick_core(query(params), legal)
            if rec2 and (conf2 == "élevée" or not rec):
                rec, conf = rec2, conf2
        reg = rec["attributes"]["entity"]["legalName"]["name"] if rec else ""
        rows.append([e["nom"], rec["id"] if rec else "", reg, conf])
        print(f"{conf[:10]:10} {e['nom'][:40]:40} {reg}")

    # LEIs checked by hand override the automatic lookup (null = add by name in Capital IQ).
    corr = json.loads((ROOT / "data_sources" / "lei_corrections.json").read_text(encoding="utf-8"))
    for r in rows:
        if r[0] in corr:
            r[1] = corr[r[0]] or ""
            r[2] = r[2] if corr[r[0]] else ""
            r[3] = "vérifié à la main" if corr[r[0]] else "à ajouter par nom dans Capital IQ"

    out = ROOT / "documents_emetteurs"
    with open(out / "capital_iq_import_LEI.csv", "w", encoding="utf-8", newline="") as f:
        for r in rows:
            if r[1]:
                f.write(r[1] + "\n")
    with open(out / "lei_controle.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Émetteur", "LEI", "Nom dans le registre GLEIF", "Confiance"])
        w.writerows(rows)
    found = sum(1 for r in rows if r[1])
    print(f"\n{found} LEI trouvés sur {len(rows)}, dont {sum(1 for r in rows if r[3] == 'élevée')} en confiance élevée")


if __name__ == "__main__":
    main()
