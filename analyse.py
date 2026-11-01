"""Ask Claude to read an issuer's headlines the way a credit analyst would, and return a structured note."""

from __future__ import annotations

import json

import anthropic

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """Tu es l'assistant d'un analyste-gérant obligataire qui gère un fonds d'obligations euro court terme (1-3 ans, label ISR, note moyenne BBB).
Chaque semaine, tu lis les titres de presse sur un émetteur que le fonds détient et tu dis ce qui compte pour un créancier.

Le point de vue est celui du porteur d'obligations, pas de l'actionnaire. Ce qui compte :
- notations et perspectives (Moody's, S&P, Fitch), surveillance négative ou positive ;
- solvabilité et liquidité : ratios de capital (CET1, coussin MDA pour les AT1), levier, cash-flow, refinancement, mur de maturités ;
- pour les AT1 et hybrides : décision de call, risque d'extension, coupon ;
- fusions, acquisitions, cessions : qui paie, avec quelle dette, et ce que ça change pour les obligations existantes ;
- résultats quand ils touchent la qualité du crédit (provisions, marges, pertes) ;
- émissions obligataires, rachats d'obligations, accès au marché ;
- régulateurs, litiges, amendes, intervention publique ;
- lien avec le souverain (spread BTP-Bund pour les banques italiennes, politique hongroise pour OTP, etc.) ;
- controverses ESG pouvant menacer l'éligibilité ISR.

Ce qui compte peu ou pas : objectifs de cours, mouvements boursiers du jour, avis d'analystes actions, sauf si cela révèle un vrai sujet de crédit.
Les rachats d'actions et dividendes élevés sont plutôt un léger négatif pour le créancier ; mentionne-les seulement s'ils sont significatifs.

Règles :
- Tu ne disposes que des titres et de leur source. N'invente aucun chiffre ni fait qui n'y figure pas.
- Quand un point repose sur un seul titre ou un titre ambigu, mets la confiance à "faible" et dis ce qu'il faudrait vérifier.
- Regroupe les titres qui parlent du même événement en un seul point, et cite tous leurs identifiants.
- Signal : "vert" si rien ne dégrade le crédit, "orange" si un sujet mérite d'être suivi, "rouge" si un événement peut dégrader le crédit à court terme.
- Écris en français, phrases courtes et claires, comme une note interne. Pas de jargon inutile.
- S'il n'y a rien d'important, dis-le simplement : une semaine calme est une information utile."""

CATEGORIES = [
    "notation", "resultats", "capital_liquidite", "fusion_acquisition", "emission_refinancement",
    "hybride_call", "reglementaire_juridique", "souverain_macro", "esg", "autre",
]

NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["vert", "orange", "rouge"]},
        "resume": {"type": "string", "description": "Une ou deux phrases : la lecture crédit de la semaine."},
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "titre": {"type": "string"},
                    "categorie": {"type": "string", "enum": CATEGORIES},
                    "impact_credit": {"type": "string", "enum": ["positif", "neutre", "negatif", "incertain"]},
                    "importance": {"type": "string", "enum": ["haute", "moyenne", "basse"]},
                    "explication": {"type": "string", "description": "Pourquoi c'est important pour les obligations détenues."},
                    "confiance": {"type": "string", "enum": ["elevee", "moyenne", "faible"]},
                    "sources": {"type": "array", "items": {"type": "string"}, "description": "Identifiants des titres, ex. H3"},
                },
                "required": ["titre", "categorie", "impact_credit", "importance", "explication", "confiance", "sources"],
                "additionalProperties": False,
            },
        },
        "a_surveiller": {"type": "array", "items": {"type": "string"}},
        "titres_ignores": {"type": "integer", "description": "Nombre de titres jugés sans intérêt crédit."},
    },
    "required": ["signal", "resume", "points", "a_surveiller", "titres_ignores"],
    "additionalProperties": False,
}


def _build_user_message(issuer: dict, headlines: list[dict], portfolio_context: str, days: int) -> str:
    lines = "\n".join(
        f"- {l['titre']} | poids : {l['poids']} | rang : {l['rang']}" for l in issuer["lignes"]
    )
    news = "\n".join(
        f"[H{i}] {(h['date'] or '')[:10]} | {h['source']} | {h['titre']}"
        for i, h in enumerate(headlines, start=1)
    ) or "(aucun titre trouvé sur la période)"
    return f"""Contexte du fonds : {portfolio_context}

Émetteur : {issuer['nom']} ({issuer['pays']}, {issuer['secteur']})
Lignes détenues :
{lines}
Pourquoi on le suit : {issuer['pourquoi']}

Titres de presse des {days} derniers jours :
{news}

Rédige la note crédit de la semaine pour cet émetteur."""


def analyse_issuer(client: anthropic.Anthropic, issuer: dict, headlines: list[dict],
                   portfolio_context: str, days: int) -> dict:
    """Return the structured note, with each point's source ids resolved to real headlines."""
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": NOTE_SCHEMA},
        },
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(issuer, headlines, portfolio_context, days)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude a refusé la requête")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Réponse tronquée (max_tokens atteint)")

    text = next(b.text for b in response.content if b.type == "text")
    note = json.loads(text)

    # Claude cites headline ids (H1, H2...). Map them back to the real title/link so no URL is ever invented.
    by_id = {f"H{i}": h for i, h in enumerate(headlines, start=1)}
    for point in note["points"]:
        point["sources"] = [by_id[s] for s in point["sources"] if s in by_id]
    note["modele"] = response.model
    note["tokens"] = {"entree": response.usage.input_tokens, "sortie": response.usage.output_tokens}
    return note
