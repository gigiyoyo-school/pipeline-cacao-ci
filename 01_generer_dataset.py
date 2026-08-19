"""
Generation des donnees sources de la filiere cacao ivoirienne.

Le sujet fournit un generateur de depart et invite explicitement a l'enrichir.
Ce script corrige quatre incoherences du generateur d'origine et ajoute la
logique metier qui rend les analyses possibles.

Ce qui a change, et pourquoi :

1. PERIODE. L'original espace 80 000 pesees de 2 heures depuis janvier 2023,
   ce qui etale les dates jusqu'en 2041. Ici la campagne couvre deux saisons
   completes (octobre 2022 a septembre 2024), avec une saisonnalite reelle :
   la campagne principale (octobre a mars) concentre environ 70 % des apports.

2. PRIX. L'original tire un second echantillon de qualites, independant de la
   colonne qualite, pour calculer le prix. Consequence mesuree : le Hors grade
   se vendait plus cher que le Grade A. Ici le prix decoule du grade reellement
   attribue, avec une prime bio et un effet campagne.

3. HIERARCHIE. L'original tire la cooperative independamment de la region : une
   pesee a Daloa pouvait etre rattachee a une cooperative de San Pedro. Ici
   chaque cooperative appartient a une region, chaque planteur a une
   cooperative, et la pesee herite de cette chaine.

4. PLANTEURS. L'original tire un identifiant de planteur au hasard sur chaque
   ligne : les 5 000 planteurs livraient dans les 8 regions. Ici un planteur a
   une plantation, une cooperative et une certification stables, decrites dans
   un referentiel separe.

Trois fichiers sont produits dans data/raw/ :
    pesees_cacao_ci_80k.csv      les donnees de terrain, avec leurs defauts
    referentiel_planteurs.csv    5 000 planteurs et leurs attributs
    referentiel_cooperatives.csv 40 cooperatives et leurs attributs

Les defauts sont injectes volontairement : ce sont eux que le pipeline devra
detecter et corriger, et le rapport de qualite des donnees les recensera.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from etl_common import (
    DATA_RAW,
    afficher_df,
    alerte,
    console,
    etape,
    ok,
    taille_lisible,
    titre,
)

# ---------------------------------------------------------------------------
# Parametres generaux
# ---------------------------------------------------------------------------
GRAINE = 42                    # graine fixe : le dataset est reproductible a l'identique
N_PESEES = 80_000              # volume total demande par le sujet
N_PLANTEURS = 5_000
COOP_PAR_REGION = 5

DEBUT = pd.Timestamp("2022-10-01")   # debut de la campagne principale 2022-2023
FIN = pd.Timestamp("2024-09-30")     # fin de la campagne intermediaire 2023-2024

# Proportion de lignes affectees par chaque defaut
TAUX_HUMIDITE_MANQUANTE = 0.04
TAUX_PRIX_ABERRANT = 0.02
TAUX_DOUBLONS = 0.015
TAUX_ERREURS_SAISIE = 0.03

# ---------------------------------------------------------------------------
# Referentiel des regions
# poids : part approximative de la region dans la production nationale.
# Le cacao ivoirien se concentre au Sud-Ouest ; Bondoukou est surtout
# une zone anacarde, d'ou son faible poids.
# ---------------------------------------------------------------------------
REGIONS = {
    # nom            zone de production   port d'export   poids   superficie (ha)
    "Soubre":       ("Sud-Ouest",         "San Pedro",    0.20,   410_000),
    "San Pedro":    ("Sud-Ouest",         "San Pedro",    0.16,   330_000),
    "Daloa":        ("Centre-Ouest",      "San Pedro",    0.14,   295_000),
    "Divo":         ("Centre-Sud",        "Abidjan",      0.13,   240_000),
    "Gagnoa":       ("Centre-Ouest",      "San Pedro",    0.12,   225_000),
    "Abengourou":   ("Est",               "Abidjan",      0.11,   190_000),
    "Aboisso":      ("Sud-Est",           "Abidjan",      0.09,   150_000),
    "Bondoukou":    ("Nord-Est",          "Abidjan",      0.05,    70_000),
}

# ---------------------------------------------------------------------------
# Referentiel des qualites
# Le bareme de prix est exprime en FCFA par kilo. Le taux d'humidite maximum
# de 8 % est la norme d'exportation appliquee en Cote d'Ivoire.
# ---------------------------------------------------------------------------
QUALITES = {
    # grade         rang  humidite cible  prix min  prix max  exportable
    "Grade A":      (1,   6.6,            900,      1100,     True),
    "Grade B":      (2,   7.6,            750,       900,     True),
    "Grade C":      (3,   8.6,            600,       750,     True),
    "Hors grade":   (4,  10.2,            400,       600,     False),
}

# Probabilites des grades selon la campagne. Le cacao seche pendant
# l'harmattan (campagne principale) est de meilleure qualite.
PROBA_QUALITE = {
    "principale":    [0.38, 0.40, 0.18, 0.04],
    "intermediaire": [0.28, 0.38, 0.26, 0.08],
}

# Poids relatif de chaque mois dans les apports annuels.
# Octobre a mars : campagne principale. Avril a septembre : intermediaire.
POIDS_MOIS = {
    10: 0.16, 11: 0.18, 12: 0.15, 1: 0.12, 2: 0.06, 3: 0.04,   # principale
    4: 0.04, 5: 0.06, 6: 0.07, 7: 0.05, 8: 0.03, 9: 0.04,      # intermediaire
}

MOIS_CAMPAGNE_PRINCIPALE = {10, 11, 12, 1, 2, 3}

PRIME_BIO = 1.10               # le cacao certifie bio se paie 10 % de plus
COEF_CAMPAGNE_INTERMEDIAIRE = 0.96   # prix garanti legerement inferieur

# Prime de volume : un gros lot coute moins cher a manipuler et a transporter,
# la cooperative le paie donc legerement mieux. Cet effet cree une correlation
# entre prix et tonnage, sans laquelle la moyenne ponderee par le tonnage
# donnerait exactement le meme resultat que la moyenne simple.
SEUIL_GROS_LOT = 500.0         # kg
SEUIL_PETIT_LOT = 100.0        # kg
PRIME_VOLUME = 1.03
DECOTE_PETIT_LOT = 0.97


# ---------------------------------------------------------------------------
# 1. Referentiel des cooperatives
# ---------------------------------------------------------------------------
def generer_cooperatives(rng: np.random.Generator) -> pd.DataFrame:
    """
    Cree 5 cooperatives par region, soit 40 au total.

    Le code porte le prefixe de sa region : COOP-DAL-003 appartient a Daloa.
    C'est ce lien, absent du generateur d'origine, qui rend la hierarchie
    region / cooperative / planteur exploitable.
    """
    lignes = []
    for region, (zone, port, _, _) in REGIONS.items():
        prefixe = region[:3].upper()
        for numero in range(1, COOP_PAR_REGION + 1):
            lignes.append(
                {
                    "code_cooperative": f"COOP-{prefixe}-{numero:03d}",
                    "nom_cooperative": f"Cooperative {region} {numero}",
                    "region": region,
                    "zone_production": zone,
                    "port_export": port,
                    "annee_creation": int(rng.integers(1995, 2020)),
                    "certifiee_bio": bool(rng.random() < 0.25),
                }
            )
    return pd.DataFrame(lignes)


# ---------------------------------------------------------------------------
# 2. Referentiel des planteurs
# ---------------------------------------------------------------------------
def generer_planteurs(rng: np.random.Generator, cooperatives: pd.DataFrame) -> pd.DataFrame:
    """
    Cree 5 000 planteurs, chacun rattache a une seule cooperative.

    La cooperative est tiree selon le poids de sa region : les regions qui
    produisent le plus comptent le plus de planteurs.

    La certification bio est un attribut du planteur, pas de la livraison :
    une exploitation est certifiee ou elle ne l'est pas. Le generateur
    d'origine la tirait sur chaque ligne de pesee, rendant le meme planteur
    bio un jour et conventionnel le lendemain.
    """
    poids_region = {region: infos[2] for region, infos in REGIONS.items()}
    poids_coop = cooperatives["region"].map(poids_region).to_numpy(dtype=float)
    poids_coop = poids_coop / poids_coop.sum()

    indices = rng.choice(len(cooperatives), size=N_PLANTEURS, p=poids_coop)
    coop_choisies = cooperatives.iloc[indices].reset_index(drop=True)

    # Superficie : loi gamma, moyenne autour de 4 ha, typique des plantations
    # familiales ivoiriennes. On borne a 30 ha pour rester realiste.
    superficie = (rng.gamma(3.0, 1.3, N_PLANTEURS) + 0.5).clip(0.5, 30).round(1)

    # Un planteur d'une cooperative certifiee a plus de chances de l'etre lui-meme
    proba_bio = np.where(coop_choisies["certifiee_bio"], 0.45, 0.06)

    return pd.DataFrame(
        {
            "id_planteur": [f"PLT{i:05d}" for i in range(1, N_PLANTEURS + 1)],
            "code_cooperative": coop_choisies["code_cooperative"],
            "region": coop_choisies["region"],
            "superficie_ha": superficie,
            "annee_adhesion": rng.integers(2005, 2024, N_PLANTEURS),
            "certifie_bio": rng.random(N_PLANTEURS) < proba_bio,
        }
    )


# ---------------------------------------------------------------------------
# 3. Calendrier pondere
# ---------------------------------------------------------------------------
def construire_calendrier(rng: np.random.Generator) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """
    Prepare les jours de la periode et leur probabilite d'apport.

    Deux effets se combinent : la saisonnalite mensuelle (campagne principale
    contre intermediaire) et le rythme hebdomadaire (les cooperatives pesent
    peu le dimanche).
    """
    jours = pd.date_range(DEBUT, FIN, freq="D")

    poids = jours.month.map(POIDS_MOIS).to_numpy(dtype=float)
    poids = poids * np.where(jours.dayofweek == 6, 0.25, 1.0)   # dimanche creux
    poids = poids / poids.sum()

    return jours, poids


# ---------------------------------------------------------------------------
# 4. Les pesees
# ---------------------------------------------------------------------------
def generer_pesees(
    rng: np.random.Generator, planteurs: pd.DataFrame, cooperatives: pd.DataFrame
) -> pd.DataFrame:
    """
    Genere les pesees en suivant l'ordre reel des evenements :
    qui livre, quand, avec quelle qualite, donc quelle humidite et quel prix.
    """
    n_uniques = N_PESEES - int(N_PESEES * TAUX_DOUBLONS)

    # --- Qui livre ---------------------------------------------------------
    # Un planteur avec une grande plantation livre plus souvent.
    poids_planteur = planteurs["superficie_ha"].to_numpy(dtype=float)
    poids_planteur = poids_planteur / poids_planteur.sum()
    indices = rng.choice(len(planteurs), size=n_uniques, p=poids_planteur)
    livreurs = planteurs.iloc[indices].reset_index(drop=True)

    # --- Quand -------------------------------------------------------------
    jours, poids_jours = construire_calendrier(rng)
    dates = jours[rng.choice(len(jours), size=n_uniques, p=poids_jours)]
    est_principale = np.isin(dates.month, list(MOIS_CAMPAGNE_PRINCIPALE))

    # --- Quelle qualite ----------------------------------------------------
    # Deux tirages separes, un par campagne, puis on recombine : c'est ce qui
    # cree la difference de qualite entre les deux saisons.
    grades = np.array(list(QUALITES))
    qualite = np.empty(n_uniques, dtype=object)
    for campagne, masque in (("principale", est_principale), ("intermediaire", ~est_principale)):
        n = int(masque.sum())
        if n:
            qualite[masque] = rng.choice(grades, size=n, p=PROBA_QUALITE[campagne])

    # --- Quelle humidite ---------------------------------------------------
    # L'humidite decoule du grade : c'est elle qui determine le classement
    # sur le terrain, et la correlation doit donc exister dans les donnees.
    cible = np.array([QUALITES[g][1] for g in qualite])
    dispersion = np.where(qualite == "Hors grade", 1.2, 0.5)
    humidite = np.clip(rng.normal(cible, dispersion), 3.5, 16).round(1)

    # --- Quel tonnage ------------------------------------------------------
    # Loi gamma, moyenne autour de 300 kg par apport, modulee par la taille
    # de la plantation. Le tonnage est calcule avant le prix, car il influe
    # sur lui : voir la prime de volume ci-dessous.
    facteur = (livreurs["superficie_ha"].to_numpy() / 4.0) ** 0.6
    tonnage = (rng.gamma(2.0, 130.0, n_uniques) * facteur).clip(5, None).round(1)

    # --- Quel prix ---------------------------------------------------------
    # Prix tire dans les bornes du bareme du grade, puis ajuste par trois
    # effets : la prime bio, le decrochage de la campagne intermediaire, et
    # une prime de volume. Les gros lots coutent moins cher a manipuler et a
    # transporter, la cooperative les paie donc un peu mieux ; les tres petits
    # apports subissent l'effet inverse.
    prix_min = np.array([QUALITES[g][2] for g in qualite])
    prix_max = np.array([QUALITES[g][3] for g in qualite])
    prix = rng.uniform(prix_min, prix_max)
    prix = prix * np.where(livreurs["certifie_bio"].to_numpy(), PRIME_BIO, 1.0)
    prix = prix * np.where(est_principale, 1.0, COEF_CAMPAGNE_INTERMEDIAIRE)
    prix = prix * np.select(
        [tonnage >= SEUIL_GROS_LOT, tonnage < SEUIL_PETIT_LOT],
        [PRIME_VOLUME, DECOTE_PETIT_LOT],
        default=1.0,
    )
    prix = prix.round(0).astype(int)

    pesees = pd.DataFrame(
        {
            "id_pesee": [f"PES{i:07d}" for i in range(1, n_uniques + 1)],
            "date": dates.date,
            "region": livreurs["region"],
            "cooperative": livreurs["code_cooperative"],
            "id_planteur": livreurs["id_planteur"],
            "tonnage_kg": tonnage,
            "qualite": qualite,
            "prix_fcfa_kg": prix,
            "humidite_pct": humidite,
        }
    )

    return pesees.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 5. Injection des defauts
# ---------------------------------------------------------------------------
def deformer_libelle(texte: str, rng: np.random.Generator) -> str:
    """Reproduit une erreur de saisie plausible sur un nom."""
    variantes = [
        texte.upper(),
        texte.lower(),
        f" {texte}",
        f"{texte} ",
        texte.replace(" ", "  "),
        texte.replace(" ", "-"),
    ]
    return str(rng.choice(variantes))


def injecter_defauts(pesees: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Ajoute les defauts que le pipeline devra traiter.

    Chaque defaut correspond a une situation reelle de collecte sur bascule :
    capteur d'humidite en panne, prix non saisi, double scan d'un ticket,
    saisie manuelle du nom de la region.
    """
    etape("Injection des defauts de terrain")

    n = len(pesees)

    # --- Humidite manquante : capteur defaillant ou mesure oubliee ---------
    idx = rng.choice(n, size=int(n * TAUX_HUMIDITE_MANQUANTE), replace=False)
    pesees.loc[pesees.index[idx], "humidite_pct"] = np.nan
    console.print(f"  humidite manquante   : {len(idx):,} lignes")

    # --- Prix aberrant : -1 est le code d'erreur du logiciel de bascule ----
    idx = rng.choice(n, size=int(n * TAUX_PRIX_ABERRANT), replace=False)
    pesees.loc[pesees.index[idx], "prix_fcfa_kg"] = -1
    console.print(f"  prix aberrant (-1)   : {len(idx):,} lignes")

    # --- Erreurs de saisie : region et cooperative tapees a la main --------
    idx = rng.choice(n, size=int(n * TAUX_ERREURS_SAISIE), replace=False)
    for position in idx:
        colonne = "region" if rng.random() < 0.6 else "cooperative"
        valeur = pesees.at[pesees.index[position], colonne]
        pesees.at[pesees.index[position], colonne] = deformer_libelle(valeur, rng)
    console.print(f"  erreurs de saisie    : {len(idx):,} lignes")

    # --- Doublons de scan : le meme ticket pese deux fois -------------------
    # Copie a l'identique, identifiant compris : c'est ce qui permet de les
    # detecter par id_pesee.
    n_doublons = N_PESEES - n
    idx = rng.choice(n, size=n_doublons, replace=False)
    doublons = pesees.iloc[idx].copy()
    pesees = pd.concat([pesees, doublons], ignore_index=True)
    console.print(f"  doublons de scan     : {n_doublons:,} lignes")

    return pesees.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# 6. Controles et resume
# ---------------------------------------------------------------------------
def resumer(pesees: pd.DataFrame, planteurs: pd.DataFrame, cooperatives: pd.DataFrame) -> None:
    """Verifie que les corrections apportees produisent bien l'effet attendu."""
    etape("Verification des corrections")

    propres = pesees[pesees["prix_fcfa_kg"] > 0]

    # Correction 2 : le prix doit decroitre avec le grade
    prix = (
        propres.groupby("qualite")
        .agg(
            nb=("id_pesee", "count"),
            prix_moyen=("prix_fcfa_kg", "mean"),
            humidite_moyenne=("humidite_pct", "mean"),
        )
        .round(1)
        .reset_index()
        .sort_values("prix_moyen", ascending=False)
    )
    afficher_df(prix, "Prix et humidite par grade (le prix doit decroitre)")

    # Correction 1 : la saisonnalite doit etre visible
    saison = propres.copy()
    saison["mois"] = pd.to_datetime(saison["date"]).dt.month
    saison["campagne"] = np.where(
        saison["mois"].isin(MOIS_CAMPAGNE_PRINCIPALE), "principale", "intermediaire"
    )
    par_campagne = (
        saison.groupby("campagne")
        .agg(nb_pesees=("id_pesee", "count"), tonnage_kg=("tonnage_kg", "sum"))
        .reset_index()
    )
    par_campagne["part_pct"] = (
        par_campagne["tonnage_kg"] / par_campagne["tonnage_kg"].sum() * 100
    ).round(1)
    afficher_df(par_campagne, "Repartition par campagne")

    # Correction 3 et 4 : la hierarchie doit etre stricte.
    # On ecarte les lignes volontairement deformees par l'injection d'erreurs
    # de saisie : ce sont elles que le pipeline devra normaliser plus tard.
    saines = pesees[
        pesees["region"].isin(REGIONS)
        & pesees["cooperative"].isin(cooperatives["code_cooperative"])
    ]
    couples = saines.groupby(["region", "cooperative"]).size().shape[0]
    multi_region = (planteurs.groupby("id_planteur")["region"].nunique() > 1).sum()

    console.print(
        f"  lignes exploitables telles quelles   : {len(saines):,} sur {len(pesees):,}"
    )
    console.print(f"  couples region-cooperative distincts  : {couples} (attendu 40)")
    console.print(f"  planteurs presents dans plusieurs regions : {multi_region} (attendu 0)")

    if couples != len(cooperatives) or multi_region:
        alerte("La hierarchie n'est pas stricte, verifiez les referentiels")
    else:
        ok("Hierarchie region / cooperative / planteur coherente")


def main() -> None:
    titre("ETAPE 1 : generation du dataset", "filiere cacao ivoirienne")

    rng = np.random.default_rng(GRAINE)   # generateur moderne, remplace np.random.seed

    etape("Construction des referentiels")
    cooperatives = generer_cooperatives(rng)
    planteurs = generer_planteurs(rng, cooperatives)
    ok(f"{len(cooperatives)} cooperatives et {len(planteurs):,} planteurs")

    etape("Generation des pesees")
    pesees = generer_pesees(rng, planteurs, cooperatives)
    ok(f"{len(pesees):,} pesees generees du {pesees['date'].min()} au {pesees['date'].max()}")

    pesees = injecter_defauts(pesees, rng)

    etape("Ecriture des fichiers")
    fichiers = {
        "pesees_cacao_ci_80k.csv": pesees,
        "referentiel_planteurs.csv": planteurs,
        "referentiel_cooperatives.csv": cooperatives,
    }
    for nom, df in fichiers.items():
        chemin = DATA_RAW / nom
        df.to_csv(chemin, index=False, encoding="utf-8")
        ok(f"{nom:<32} {len(df):>7,} lignes  {taille_lisible(chemin)}")

    resumer(pesees, planteurs, cooperatives)

    console.print(
        "\n[dim]Le dataset est reproductible : meme graine, memes donnees. "
        "Les fichiers restent hors du depot Git, ce script suffit a les recreer.[/]"
    )


if __name__ == "__main__":
    main()