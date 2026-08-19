"""
Etape T du pipeline : nettoyage, enrichissement et controle qualite.

Quatre familles de defauts sont traitees, dans un ordre qui n'est pas
arbitraire :

  1. normalisation des libelles avant tout, sinon les jointures avec les
                                 referentiels echouent sur les variantes
  2. dedoublonnage               avant les imputations, pour ne pas calculer
                                 des medianes sur des lignes comptees deux fois
  3. conversion des types        avant le calcul des campagnes
  4. imputation des manquants    en dernier, une fois le perimetre stabilise

Principe applique aux valeurs manquantes : on impute et on marque, on ne
supprime pas. Une pesee dont le prix n'a pas ete saisi reste une pesee dont le
tonnage est valide. La supprimer ferait disparaitre du tonnage reel des
analyses de production.

Sorties : data/interim/pesees_propres.parquet
          data/output/rapport_qualite.json
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from etl_common import (
    DATA_OUTPUT,
    afficher_df,
    alerte,
    charger_etape,
    console,
    construire_lookup,
    echec,
    etape,
    normaliser,
    ok,
    sauver_etape,
    titre,
)
from qualite import detecter_anomalies, taux_manquants, valider

# Bornes des categories de tonnage, exprimees en kilogrammes
BINS_TONNAGE = [0, 100, 500, 1_000, float("inf")]
LABELS_TONNAGE = ["Petit (<100 kg)", "Moyen (100-500 kg)",
                  "Gros (500-1000 kg)", "Tres gros (>1000 kg)"]

# La campagne principale ivoirienne court d'octobre a mars
MOIS_CAMPAGNE_PRINCIPALE = {10, 11, 12, 1, 2, 3}

HUMIDITE_NORME_EXPORT = 8.0


# ---------------------------------------------------------------------------
# 1. Normalisation des libelles
# ---------------------------------------------------------------------------
def normaliser_libelles(
    pesees: pd.DataFrame, cooperatives: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    """
    Ramene chaque libelle saisi a sa forme officielle.

    La methode ne code aucune variante en dur. On reduit le libelle saisi et le
    libelle officiel a la meme forme canonique (sans accent, minuscules, sans
    ponctuation), puis on remplace par l'officiel. Une nouvelle faute de frappe
    jamais rencontree sera rattrapee sans modifier le code.
    """
    etape("Normalisation des libelles")

    lookup_region = construire_lookup(cooperatives["region"].unique())
    lookup_coop = construire_lookup(cooperatives["code_cooperative"].unique())

    stats = {}
    for colonne, lookup in (("region", lookup_region), ("cooperative", lookup_coop)):
        avant = pesees[colonne].nunique()
        corrigees = pesees[colonne].map(normaliser).map(lookup)

        # Un libelle sans correspondance reste tel quel : il doit rester
        # visible, la regle R4 le signalera plutot que de le masquer.
        non_resolus = int(corrigees.isna().sum())
        pesees[colonne] = corrigees.fillna(pesees[colonne])

        apres = pesees[colonne].nunique()
        stats[colonne] = {"modalites_avant": int(avant), "modalites_apres": int(apres),
                          "non_resolus": non_resolus}
        console.print(
            f"  {colonne:<12} : {avant:>4} modalites -> {apres:>3} "
            f"({avant - apres} variantes corrigees, {non_resolus} non resolues)"
        )

    return pesees, stats


# ---------------------------------------------------------------------------
# 2. Dedoublonnage
# ---------------------------------------------------------------------------
def dedoublonner(pesees: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Supprime les doublons de scan en conservant la premiere occurrence.

    Le dedoublonnage passe AVANT les imputations : sinon les medianes seraient
    calculees sur des lignes comptees deux fois, ce qui les biaiserait
    legerement, et surtout le volume total serait fausse.
    """
    etape("Dedoublonnage")

    avant = len(pesees)
    exacts = int(pesees.duplicated().sum())
    pesees = pesees.drop_duplicates(subset="id_pesee", keep="first").copy()
    supprimes = avant - len(pesees)

    console.print(f"  lignes entierement identiques : {exacts:,}")
    console.print(f"  lignes supprimees            : {supprimes:,} ({supprimes / avant * 100:.2f} %)")
    ok(f"{len(pesees):,} pesees uniques conservees")

    return pesees, {"lignes_avant": avant, "doublons_supprimes": supprimes,
                    "lignes_apres": len(pesees)}


# ---------------------------------------------------------------------------
# 3. Types et colonnes temporelles
# ---------------------------------------------------------------------------
def corriger_types(pesees: pd.DataFrame) -> pd.DataFrame:
    """
    Convertit la date et derive les colonnes temporelles.

    Le format est impose explicitement plutot que devine : sur un fichier au
    format americain, Pandas peut inverser jour et mois pour toutes les dates
    anterieures au 13 du mois, sans le signaler.
    """
    etape("Conversion des types")

    pesees["date"] = pd.to_datetime(pesees["date"], format="%Y-%m-%d")

    pesees["annee"] = pesees["date"].dt.year
    pesees["mois"] = pesees["date"].dt.month
    pesees["jour_semaine"] = pesees["date"].dt.day_name()

    ok(f"date convertie en {pesees['date'].dtype}")
    return pesees


# ---------------------------------------------------------------------------
# 4. Enrichissement
# ---------------------------------------------------------------------------
def enrichir(pesees: pd.DataFrame, planteurs: pd.DataFrame,
             cooperatives: pd.DataFrame) -> pd.DataFrame:
    """
    Ajoute les attributs des referentiels et les six colonnes calculees.

    Les campagnes sont calculees avant l'imputation des prix : la mediane
    servant a imputer est celle du meme grade sur la meme campagne, ce qui est
    plus juste qu'une mediane globale puisque le prix garanti varie d'une
    campagne a l'autre.
    """
    etape("Enrichissement")

    # --- Jointure avec les referentiels ------------------------------------
    # Le referentiel fait foi : la certification bio et la superficie sont des
    # proprietes de l'exploitation, elles n'ont pas leur place sur la ligne de
    # pesee mais sur le planteur.
    pesees = pesees.merge(
        planteurs[["id_planteur", "superficie_ha", "annee_adhesion", "certifie_bio"]],
        on="id_planteur",
        how="left",
        validate="many_to_one",   # garantit qu'un planteur n'apparait qu'une fois au referentiel
    )
    pesees = pesees.merge(
        cooperatives[["code_cooperative", "zone_production", "port_export"]],
        left_on="cooperative",
        right_on="code_cooperative",
        how="left",
        validate="many_to_one",
    ).drop(columns="code_cooperative")

    # --- Colonne 1 et 2 : campagne et saison -------------------------------
    # Une saison de campagne court d'octobre a septembre : une pesee de
    # janvier 2023 appartient a la saison 2022-2023, pas a 2023-2024.
    pesees["campagne"] = np.where(
        pesees["mois"].isin(MOIS_CAMPAGNE_PRINCIPALE), "Principale", "Intermediaire"
    )
    annee_saison = np.where(pesees["mois"] >= 10, pesees["annee"], pesees["annee"] - 1)
    pesees["saison"] = [f"{a}-{a + 1}" for a in annee_saison]

    # --- Colonne 3 : conformite a la norme d'exportation -------------------
    pesees["conforme_export"] = pesees["humidite_pct"] <= HUMIDITE_NORME_EXPORT

    # --- Colonne 4 : categorie de tonnage ----------------------------------
    pesees["categorie_tonnage"] = pd.cut(
        pesees["tonnage_kg"], bins=BINS_TONNAGE, labels=LABELS_TONNAGE, right=True
    ).astype(str)

    ok("attributs des referentiels et colonnes temporelles ajoutes")
    return pesees


def imputer(pesees: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Impute les valeurs manquantes et marque chaque ligne concernee.

    Le drapeau est ce qui rend l'imputation defendable : toute analyse peut
    ecarter les valeurs imputees d'un simple filtre, et le rapport peut dire
    exactement combien de lignes ne sont pas des mesures reelles.
    """
    etape("Imputation des valeurs manquantes")
    stats = {}

    # --- Prix : -1 est un code d'erreur, pas un prix -----------------------
    pesees["prix_impute"] = pesees["prix_fcfa_kg"] <= 0
    pesees.loc[pesees["prix_impute"], "prix_fcfa_kg"] = np.nan

    mediane_prix = pesees.groupby(["qualite", "campagne"])["prix_fcfa_kg"].transform("median")
    pesees["prix_fcfa_kg"] = pesees["prix_fcfa_kg"].fillna(mediane_prix).round(0).astype(int)

    nb_prix = int(pesees["prix_impute"].sum())
    console.print(
        f"  prix     : {nb_prix:,} valeur(s) imputee(s) par la mediane du grade et de la campagne"
    )
    stats["prix_impute"] = nb_prix

    # --- Humidite : capteur defaillant ou mesure oubliee -------------------
    pesees["humidite_imputee"] = pesees["humidite_pct"].isna()
    mediane_humidite = pesees.groupby("qualite")["humidite_pct"].transform("median")
    pesees["humidite_pct"] = pesees["humidite_pct"].fillna(mediane_humidite).round(1)

    nb_humidite = int(pesees["humidite_imputee"].sum())
    console.print(f"  humidite : {nb_humidite:,} valeur(s) imputee(s) par la mediane du grade")
    stats["humidite_imputee"] = nb_humidite

    # La conformite export doit etre recalculee : elle depend de l'humidite
    pesees["conforme_export"] = pesees["humidite_pct"] <= HUMIDITE_NORME_EXPORT

    alerte(
        "L'humidite determine le grade : l'imputer par la mediane du grade est "
        "circulaire. Les analyses portant sur l'humidite ecarteront ces lignes."
    )
    return pesees, stats


def calculer_mesures(pesees: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les deux dernieres colonnes, apres imputation puisqu'elles
    dependent du prix.
    """
    etape("Calcul des mesures derivees")

    # --- Colonne 5 : montant de la transaction -----------------------------
    # C'est la colonne qui rend possible la moyenne ponderee. Le prix moyen par
    # qualite ne se calcule pas en AVG(prix) : une pesee de 12 kg pese alors
    # autant qu'une pesee de 2 000 kg. Le prix moyen reel est
    # SUM(montant) / SUM(tonnage), ce qui exige de stocker le montant.
    pesees["montant_fcfa"] = (pesees["tonnage_kg"] * pesees["prix_fcfa_kg"]).round(0).astype(int)

    # --- Colonne 6 : ecart au prix median du grade -------------------------
    mediane_grade = pesees.groupby("qualite")["prix_fcfa_kg"].transform("median")
    pesees["ecart_prix_grade_pct"] = (
        (pesees["prix_fcfa_kg"] - mediane_grade) / mediane_grade * 100
    ).round(1)

    ok("montant_fcfa et ecart_prix_grade_pct calcules")
    return pesees


# ---------------------------------------------------------------------------
# 5. Controle qualite et rapport
# ---------------------------------------------------------------------------
def controler(pesees: pd.DataFrame, planteurs: pd.DataFrame,
              cooperatives: pd.DataFrame) -> dict:
    """Applique les sept regles et la detection d'anomalies."""
    etape("Controle qualite")

    resultat = valider(pesees, planteurs=planteurs, cooperatives=cooperatives)

    lignes = (
        [{"niveau": "OK", "message": c} for c in resultat.controles]
        + [{"niveau": "AVERTISSEMENT", "message": a} for a in resultat.avertissements]
        + [{"niveau": "ERREUR", "message": e} for e in resultat.erreurs]
    )
    afficher_df(pd.DataFrame(lignes), "Regles de validation", max_lignes=20)

    if resultat.est_valide:
        ok(resultat.resume())
    else:
        echec(resultat.resume())

    etape("Detection des anomalies de pesee")
    anomalies = detecter_anomalies(pesees)
    afficher_df(anomalies, "Anomalies detectees (signalees, non supprimees)")

    manquants = taux_manquants(pesees)
    if manquants.empty:
        ok("Aucune valeur manquante dans le jeu final")
    else:
        afficher_df(manquants, "Valeurs manquantes restantes")

    return {
        "validation": resultat.en_dict(),
        "anomalies": anomalies.to_dict(orient="records"),
        "valeurs_manquantes_restantes": manquants.to_dict(orient="records"),
    }


def main() -> None:
    titre("ETAPE 2B : transformation et controle qualite")

    pesees = charger_etape("brut_pesees")
    planteurs = charger_etape("brut_planteurs")
    cooperatives = charger_etape("brut_cooperatives")

    nb_depart = len(pesees)

    pesees, stats_libelles = normaliser_libelles(pesees, cooperatives)
    pesees, stats_doublons = dedoublonner(pesees)
    pesees = corriger_types(pesees)
    pesees = enrichir(pesees, planteurs, cooperatives)
    pesees, stats_imputation = imputer(pesees)
    pesees = calculer_mesures(pesees)

    rapport_controle = controler(pesees, planteurs, cooperatives)

    etape("Apercu du jeu final")
    apercu = pesees[
        ["id_pesee", "date", "region", "qualite", "tonnage_kg", "prix_fcfa_kg",
         "montant_fcfa", "campagne", "conforme_export"]
    ].head(5)
    afficher_df(apercu, "Cinq premieres lignes")
    console.print(
        f"  {len(pesees):,} lignes x {len(pesees.columns)} colonnes "
        f"(depart : {nb_depart:,} x 9)"
    )

    sauver_etape(pesees, "pesees_propres")

    rapport = {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "volumes": {
            "lignes_extraites": nb_depart,
            "lignes_conservees": len(pesees),
            "colonnes_finales": len(pesees.columns),
        },
        "normalisation_libelles": stats_libelles,
        "dedoublonnage": stats_doublons,
        "imputation": stats_imputation,
        **rapport_controle,
    }
    chemin = DATA_OUTPUT / "rapport_qualite.json"
    chemin.write_text(json.dumps(rapport, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")

    ok(f"Rapport de qualite ecrit : data/output/{chemin.name}")
    console.print(
        "\n[dim]Ce rapport est un livrable explicite du sujet 2 : "
        "part de valeurs manquantes et detection d'anomalies de pesee.[/]"
    )


if __name__ == "__main__":
    main()