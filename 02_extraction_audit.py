"""
Etape E du pipeline : extraction des trois sources et audit initial.

Regle de methode : cette etape ne modifie rien. Elle mesure.

Les chiffres produits ici justifient chaque decision de nettoyage prise a
l'etape suivante, et alimentent la section 3 du rapport technique
(description du dataset, audit initial). Un nettoyage dont on ne peut pas
dire ce qu'il a corrige, et en quelle quantite, n'est pas defendable.

Sortie : data/output/rapport_audit.json
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import pandas as pd

from etl_common import (
    DATA_OUTPUT,
    FICHIER_COOPERATIVES,
    FICHIER_PESEES,
    FICHIER_PLANTEURS,
    afficher_df,
    alerte,
    console,
    etape,
    normaliser,
    ok,
    sauver_etape,
    taille_lisible,
    titre,
)

SOURCES = {
    "pesees": FICHIER_PESEES,
    "planteurs": FICHIER_PLANTEURS,
    "cooperatives": FICHIER_COOPERATIVES,
}


def extraire() -> dict[str, pd.DataFrame]:
    """Lit les trois fichiers sources et mesure le cout de l'extraction."""
    etape("Extraction des sources")
    tables = {}

    for nom, chemin in SOURCES.items():
        if not chemin.exists():
            raise FileNotFoundError(
                f"Source absente : {chemin}\nLancez d'abord 01_generer_dataset.py"
            )
        debut = time.time()
        df = pd.read_csv(chemin, encoding="utf-8", low_memory=False)
        duree = time.time() - debut
        memoire = df.memory_usage(deep=True).sum() / 1024**2

        ok(
            f"{nom:<14} {len(df):>7,} lignes x {len(df.columns):>2} colonnes  "
            f"{taille_lisible(chemin):>9}  lu en {duree:.2f}s  ({memoire:.1f} Mo en memoire)"
        )
        tables[nom] = df

    return tables


def auditer_types(df: pd.DataFrame) -> pd.DataFrame:
    """Type detecte par Pandas pour chaque colonne, avec un exemple de valeur."""
    return pd.DataFrame(
        {
            "colonne": df.columns,
            "type_pandas": [str(t) for t in df.dtypes],
            "exemple": [str(df[c].dropna().iloc[0])[:24] if df[c].notna().any() else "-"
                        for c in df.columns],
            "valeurs_distinctes": [df[c].nunique() for c in df.columns],
        }
    )


def auditer_pesees(pesees: pd.DataFrame) -> dict:
    """Audit detaille de la table de faits, sans aucune modification."""
    constats = {}

    # --- Types --------------------------------------------------------------
    etape("Types de donnees")
    afficher_df(auditer_types(pesees), "Structure du fichier des pesees", max_lignes=12)
    alerte("La colonne date est de type texte, elle sera convertie a l'etape suivante.")

    # --- Valeurs manquantes -------------------------------------------------
    etape("Valeurs manquantes")
    manquants = pesees.isna().sum()
    manquants = manquants[manquants > 0]
    if manquants.empty:
        ok("Aucune valeur manquante")
        constats["valeurs_manquantes"] = {}
    else:
        tableau = pd.DataFrame(
            {
                "colonne": manquants.index,
                "nb_manquants": manquants.to_numpy(),
                "part_pct": (manquants / len(pesees) * 100).round(2).to_numpy(),
            }
        )
        afficher_df(tableau, "Valeurs manquantes")
        constats["valeurs_manquantes"] = dict(
            zip(tableau["colonne"], tableau["nb_manquants"].astype(int))
        )

    # --- Doublons -----------------------------------------------------------
    etape("Doublons")
    nb_doublons_id = int(pesees["id_pesee"].duplicated().sum())
    nb_doublons_complets = int(pesees.duplicated().sum())
    console.print(f"  doublons sur id_pesee     : {nb_doublons_id:,}")
    console.print(f"  lignes entierement identiques : {nb_doublons_complets:,}")
    if nb_doublons_id == nb_doublons_complets and nb_doublons_id:
        ok("Tous les doublons sont des copies exactes : double scan du meme ticket")
    constats["doublons_id_pesee"] = nb_doublons_id
    constats["doublons_lignes_completes"] = nb_doublons_complets

    # --- Coherence des libelles ---------------------------------------------
    # Un libelle mal saisi cree une modalite supplementaire : c'est ainsi qu'on
    # detecte les erreurs de saisie sans les chercher une par une.
    etape("Coherence des libelles categoriels")
    lignes = []
    for colonne in ["region", "cooperative", "qualite"]:
        brut = pesees[colonne].nunique()
        normalise = pesees[colonne].map(normaliser).nunique()
        lignes.append(
            {
                "colonne": colonne,
                "modalites_brutes": brut,
                "modalites_normalisees": normalise,
                "variantes_de_saisie": brut - normalise,
            }
        )
    afficher_df(pd.DataFrame(lignes), "Erreurs de saisie detectees")

    exemples = sorted(
        v for v in pesees["region"].unique() if v != v.strip() or v != v.title()
    )[:6]
    console.print(f"  exemples de variantes : {exemples}")
    constats["variantes_saisie"] = {
        ligne["colonne"]: int(ligne["variantes_de_saisie"]) for ligne in lignes
    }

    # --- Statistiques des mesures -------------------------------------------
    etape("Statistiques des mesures")
    stats = pesees[["tonnage_kg", "prix_fcfa_kg", "humidite_pct"]].describe().T
    stats = stats.round(1).reset_index().rename(columns={"index": "mesure"})
    afficher_df(stats, "Distribution des mesures")

    nb_prix_negatifs = int((pesees["prix_fcfa_kg"] <= 0).sum())
    if nb_prix_negatifs:
        alerte(
            f"{nb_prix_negatifs:,} prix negatifs ou nuls : code d'erreur -1 du logiciel "
            "de bascule, a traiter comme une valeur manquante"
        )
    constats["prix_aberrants"] = nb_prix_negatifs

    # --- Periode couverte ---------------------------------------------------
    dates = pd.to_datetime(pesees["date"])
    console.print(
        f"  periode : du [cyan]{dates.min().date()}[/] au [cyan]{dates.max().date()}[/] "
        f"({(dates.max() - dates.min()).days} jours)"
    )
    constats["periode"] = {"debut": str(dates.min().date()), "fin": str(dates.max().date())}

    return constats


def auditer_integrite(tables: dict[str, pd.DataFrame]) -> dict:
    """
    Verifie que les cles des pesees existent bien dans les referentiels.

    On compare sur la forme normalisee : sinon les erreurs de saisie feraient
    passer pour orphelines des lignes parfaitement valides.
    """
    etape("Integrite referentielle")
    pesees, planteurs, cooperatives = tables["pesees"], tables["planteurs"], tables["cooperatives"]

    planteurs_connus = set(planteurs["id_planteur"])
    coops_connues = {normaliser(c) for c in cooperatives["code_cooperative"]}
    regions_connues = {normaliser(r) for r in cooperatives["region"]}

    orphelins_planteur = int((~pesees["id_planteur"].isin(planteurs_connus)).sum())
    orphelins_coop = int((~pesees["cooperative"].map(normaliser).isin(coops_connues)).sum())
    orphelines_region = int((~pesees["region"].map(normaliser).isin(regions_connues)).sum())

    lignes = [
        {"cle": "id_planteur", "orphelins": orphelins_planteur, "reference": "referentiel_planteurs"},
        {"cle": "cooperative", "orphelins": orphelins_coop, "reference": "referentiel_cooperatives"},
        {"cle": "region", "orphelins": orphelines_region, "reference": "referentiel_cooperatives"},
    ]
    afficher_df(pd.DataFrame(lignes), "Cles orphelines apres normalisation")

    if orphelins_planteur or orphelins_coop or orphelines_region:
        alerte("Des cles ne trouvent pas leur correspondance, a investiguer avant chargement")
    else:
        ok("Toutes les cles des pesees existent dans les referentiels")

    return {
        "orphelins_planteur": orphelins_planteur,
        "orphelins_cooperative": orphelins_coop,
        "orphelins_region": orphelines_region,
    }


def main() -> None:
    titre("ETAPE 2A : extraction et audit initial", "Aucune donnee n'est modifiee ici")

    tables = extraire()
    constats = auditer_pesees(tables["pesees"])
    constats["integrite"] = auditer_integrite(tables)

    # Les tables brutes sont archivees : l'etape de transformation repart de la,
    # sans relire les CSV, et le brut reste disponible pour comparaison.
    for nom, df in tables.items():
        sauver_etape(df, f"brut_{nom}")

    rapport = {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "sources": {nom: {"fichier": chemin.name, "nb_lignes": len(tables[nom])}
                    for nom, chemin in SOURCES.items()},
        "constats": constats,
    }
    chemin = DATA_OUTPUT / "rapport_audit.json"
    chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")

    ok(f"Rapport d'audit ecrit : data/output/{chemin.name}")
    console.print(
        "\n[dim]Ces chiffres justifient les decisions de nettoyage de l'etape suivante. "
        "Ils doivent être reportés tels quels dans la section 3 du rapport.[/]"
    )


if __name__ == "__main__":
    main()