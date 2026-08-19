"""
Regles de validation et detection d'anomalies du pipeline cacao.

Sept regles sont implementees :

    R1  volume minimal de lignes
    R2  unicite de la cle primaire
    R3  completude des colonnes critiques
    R4  domaines de valeurs (grades et regions connus)
    R5  bornes metier (tonnage, prix, humidite)
    R6  integrite referentielle (planteurs et cooperatives existants)
    R7  coherence hierarchique (region de la pesee = region du planteur)

La detection d'anomalies de pesee est separee des regles : une anomalie est
signalee et comptee, jamais supprimee.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Seuils. Ils sont passables en argument pour pouvoir etre durcis en
# production sans toucher au code des regles.
# ---------------------------------------------------------------------------
SEUIL_LIGNES_MINIMUM = 70_000
COLONNES_CRITIQUES = ["id_pesee", "date", "region", "cooperative", "id_planteur",
                      "tonnage_kg", "qualite", "prix_fcfa_kg"]

GRADES_ATTENDUS = {"Grade A", "Grade B", "Grade C", "Hors grade"}

BORNES = {
    # colonne          minimum  maximum   commentaire
    "tonnage_kg":      (1.0,    5_000.0),   # un apport plausible sur une bascule
    "prix_fcfa_kg":    (200.0,  2_000.0),   # bornes larges autour du bareme CCC
    "humidite_pct":    (0.0,    25.0),      # au-dela, la mesure est invalide
}

# Norme d'exportation ivoirienne : au-dela de 8 %, le lot est refuse a l'export
HUMIDITE_NORME_EXPORT = 8.0


@dataclass
class ResultatQualite:
    """Resultat structure d'un controle qualite."""

    controles: list[str] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    nb_lignes: int = 0

    @property
    def est_valide(self) -> bool:
        """Vrai si aucune regle bloquante n'a echoue."""
        return not self.erreurs

    def resume(self) -> str:
        etat = "QUALITE OK" if self.est_valide else "QUALITE KO"
        return (
            f"{etat} : {len(self.controles)} regle(s) passee(s), "
            f"{len(self.erreurs)} erreur(s), {len(self.avertissements)} avertissement(s) "
            f"sur {self.nb_lignes:,} lignes"
        )

    def en_dict(self) -> dict:
        """Forme serialisable, pour le rapport JSON."""
        return {
            "valide": self.est_valide,
            "nb_lignes": self.nb_lignes,
            "controles_passes": self.controles,
            "erreurs": self.erreurs,
            "avertissements": self.avertissements,
        }


def valider(
    df: pd.DataFrame,
    planteurs: pd.DataFrame | None = None,
    cooperatives: pd.DataFrame | None = None,
    seuil_lignes: int = SEUIL_LIGNES_MINIMUM,
) -> ResultatQualite:
    """
    Applique les sept regles de validation.

    La fonction ne leve jamais d'exception : elle renvoie la liste complete des
    problemes. C'est a l'appelant de decider d'arreter le pipeline. Cela permet
    de voir tous les defauts d'un coup au lieu de s'arreter au premier, et rend
    la fonction testable sans capture d'exception.
    """
    resultat = ResultatQualite(nb_lignes=len(df))

    # --- R1 : volume minimal -----------------------------------------------
    if len(df) < seuil_lignes:
        resultat.erreurs.append(
            f"R1 volume : {len(df):,} lignes, en dessous du seuil de {seuil_lignes:,}. "
            "Extraction probablement incomplete."
        )
    else:
        resultat.controles.append(f"R1 volume : {len(df):,} lignes (seuil {seuil_lignes:,})")

    # --- R2 : unicite de la cle primaire -----------------------------------
    nb_doublons = int(df["id_pesee"].duplicated().sum())
    if nb_doublons:
        resultat.erreurs.append(
            f"R2 unicite : {nb_doublons:,} doublon(s) sur id_pesee. "
            "Le dedoublonnage n'a pas ete applique."
        )
    else:
        resultat.controles.append("R2 unicite : aucun doublon sur id_pesee")

    # --- R3 : completude des colonnes critiques ----------------------------
    manquantes = []
    for colonne in COLONNES_CRITIQUES:
        if colonne not in df.columns:
            resultat.erreurs.append(f"R3 completude : colonne absente '{colonne}'")
            continue
        nb_nan = int(df[colonne].isna().sum())
        if nb_nan:
            manquantes.append(f"{colonne} ({nb_nan:,})")
    if manquantes:
        resultat.erreurs.append(
            f"R3 completude : valeurs manquantes sur {', '.join(manquantes)}"
        )
    else:
        resultat.controles.append(
            f"R3 completude : {len(COLONNES_CRITIQUES)} colonnes critiques sans valeur manquante"
        )

    # --- R4 : domaines de valeurs ------------------------------------------
    grades_inconnus = set(df["qualite"].dropna().unique()) - GRADES_ATTENDUS
    if grades_inconnus:
        resultat.erreurs.append(
            f"R4 domaine : grade(s) inconnu(s) {sorted(grades_inconnus)}. "
            f"Attendus : {sorted(GRADES_ATTENDUS)}"
        )
    else:
        resultat.controles.append("R4 domaine : tous les grades appartiennent au referentiel")

    if cooperatives is not None:
        regions_connues = set(cooperatives["region"])
        regions_inconnues = set(df["region"].dropna().unique()) - regions_connues
        if regions_inconnues:
            resultat.erreurs.append(
                f"R4 domaine : region(s) non normalisee(s) {sorted(regions_inconnues)[:5]}"
            )
        else:
            resultat.controles.append(
                f"R4 domaine : {df['region'].nunique()} regions, toutes normalisees"
            )

    # --- R5 : bornes metier -------------------------------------------------
    hors_bornes = []
    for colonne, (mini, maxi) in BORNES.items():
        if colonne not in df.columns:
            continue
        serie = df[colonne].dropna()
        nb = int(((serie < mini) | (serie > maxi)).sum())
        if nb:
            hors_bornes.append(f"{colonne} ({nb:,} hors [{mini:g} ; {maxi:g}])")
    if hors_bornes:
        resultat.erreurs.append(f"R5 bornes : {', '.join(hors_bornes)}")
    else:
        resultat.controles.append("R5 bornes : tonnage, prix et humidite dans les plages metier")

    # --- R6 : integrite referentielle --------------------------------------
    if planteurs is not None:
        orphelins = int((~df["id_planteur"].isin(planteurs["id_planteur"])).sum())
        if orphelins:
            resultat.erreurs.append(
                f"R6 integrite : {orphelins:,} pesee(s) referencent un planteur absent "
                "du referentiel"
            )
        else:
            resultat.controles.append("R6 integrite : tous les planteurs existent au referentiel")

    if cooperatives is not None:
        orphelines = int((~df["cooperative"].isin(cooperatives["code_cooperative"])).sum())
        if orphelines:
            resultat.erreurs.append(
                f"R6 integrite : {orphelines:,} pesee(s) referencent une cooperative inconnue"
            )
        else:
            resultat.controles.append(
                "R6 integrite : toutes les cooperatives existent au referentiel"
            )

    # --- R7 : coherence hierarchique ---------------------------------------
    # C'est le garde-fou du schema en etoile : la table de faits portera a la
    # fois la region et la cooperative, rien n'interdit techniquement qu'elles
    # se contredisent. Cette regle verifie qu'elles ne le font pas.
    if planteurs is not None and "region" in df.columns:
        reference = planteurs.set_index("id_planteur")["region"]
        attendue = df["id_planteur"].map(reference)
        incoherentes = int((df["region"] != attendue).sum())
        if incoherentes:
            resultat.erreurs.append(
                f"R7 coherence : {incoherentes:,} pesee(s) dont la region differe de celle "
                "du planteur au referentiel"
            )
        else:
            resultat.controles.append(
                "R7 coherence : region de la pesee identique a celle du planteur"
            )

    # --- Avertissements non bloquants --------------------------------------
    for drapeau, libelle in (("prix_impute", "prix"), ("humidite_imputee", "humidite")):
        if drapeau in df.columns:
            part = df[drapeau].mean() * 100
            if part > 5:
                resultat.avertissements.append(
                    f"{part:.1f} % des lignes ont une valeur de {libelle} imputee, "
                    "prudence dans l'interpretation"
                )

    return resultat


def detecter_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detecte les anomalies de pesee sans supprimer aucune ligne.

    Trois familles :
      - tonnage hors de l'intervalle interquartile elargi (methode de Tukey)
      - humidite au-dessus de la norme d'exportation de 8 %
      - prix eloigne de la mediane de son grade de plus de 30 %

    Le resultat est un recapitulatif : combien de lignes, quelle part, quel
    tonnage concerne. C'est le materiau du rapport de qualite des donnees
    demande par le sujet.
    """
    lignes = []

    # --- Tonnages atypiques : methode de Tukey ------------------------------
    # Q1 et Q3 sont les quartiles, l'ecart interquartile mesure la dispersion
    # centrale. Au-dela de 3 ecarts, la valeur est consideree extreme.
    q1, q3 = df["tonnage_kg"].quantile([0.25, 0.75])
    ecart = q3 - q1
    plafond = q3 + 3 * ecart
    masque = df["tonnage_kg"] > plafond
    lignes.append(
        {
            "anomalie": "tonnage extreme",
            "critere": f"> {plafond:,.0f} kg (Q3 + 3 x IQR)",
            "nb_lignes": int(masque.sum()),
            "part_pct": round(masque.mean() * 100, 2),
            "tonnage_concerne_kg": round(float(df.loc[masque, "tonnage_kg"].sum()), 1),
            "action": "signale, conserve",
        }
    )

    # --- Humidite au-dessus de la norme d'export ---------------------------
    masque = df["humidite_pct"] > HUMIDITE_NORME_EXPORT
    lignes.append(
        {
            "anomalie": "humidite hors norme export",
            "critere": f"> {HUMIDITE_NORME_EXPORT} %",
            "nb_lignes": int(masque.sum()),
            "part_pct": round(masque.mean() * 100, 2),
            "tonnage_concerne_kg": round(float(df.loc[masque, "tonnage_kg"].sum()), 1),
            "action": "signale, conserve",
        }
    )

    # --- Prix eloigne de la mediane de son grade ---------------------------
    mediane_grade = df.groupby("qualite")["prix_fcfa_kg"].transform("median")
    ecart_relatif = (df["prix_fcfa_kg"] - mediane_grade).abs() / mediane_grade
    masque = ecart_relatif > 0.30
    lignes.append(
        {
            "anomalie": "prix atypique pour le grade",
            "critere": "ecart > 30 % a la mediane du grade",
            "nb_lignes": int(masque.sum()),
            "part_pct": round(masque.mean() * 100, 2),
            "tonnage_concerne_kg": round(float(df.loc[masque, "tonnage_kg"].sum()), 1),
            "action": "signale, conserve",
        }
    )

    return pd.DataFrame(lignes)


def taux_manquants(df: pd.DataFrame) -> pd.DataFrame:
    """Part de valeurs manquantes par colonne, pour le rapport de qualite."""
    manquants = df.isna().sum()
    tableau = pd.DataFrame(
        {
            "colonne": manquants.index,
            "nb_manquants": manquants.to_numpy(),
            "part_pct": (manquants / len(df) * 100).round(2).to_numpy(),
        }
    )
    return tableau[tableau["nb_manquants"] > 0].reset_index(drop=True)