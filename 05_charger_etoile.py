"""
Peuple les cinq dimensions puis la table de faits dans Supabase.

L'ordre n'est pas negociable : une cle etrangere ne peut pointer que vers une
ligne qui existe deja. Les dimensions d'abord, les faits ensuite, et parmi les
dimensions, dim_region avant dim_cooperative avant dim_planteur.

Les cles techniques sont generees par PostgreSQL (SERIAL). Le script les relit
donc apres insertion : ce sont les identifiants reellement attribues par la
base qui font foi, pas ceux qu'on aurait calcules en memoire.

Mode sans base :
    uv run 05_charger_etoile.py --dry-run
construit les six tables, simule l'attribution des cles, controle l'integrite
et affiche le resultat, sans se connecter a Supabase. Utile pour valider la
logique avant d'avoir configure la connexion.

Sorties : les 6 tables dans Supabase, et une copie en Parquet dans data/interim/
"""

from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

from etl_common import (
    afficher_df,
    alerte,
    charger_etape,
    console,
    echec,
    etape,
    get_engine,
    ok,
    sauver_etape,
    titre,
)

# ---------------------------------------------------------------------------
# Bareme de qualite.
# En production, ce referentiel viendrait d'un fichier officiel du Conseil du
# Cafe-Cacao. Il est reproduit ici a l'identique de celui utilise par le
# generateur : humidite maximale toleree et bornes de prix par grade.
# ---------------------------------------------------------------------------
REFERENTIEL_QUALITES = [
    # nom          rang  humidite_max  plancher  plafond  exportable
    ("Grade A",    1,    7.0,          900,      1100,    True),
    ("Grade B",    2,    8.0,          750,       900,    True),
    ("Grade C",    3,    9.0,          600,       750,    True),
    ("Hors grade", 4,   99.0,          400,       600,    False),
]

NOMS_MOIS = {
    1: "Janvier", 2: "Fevrier", 3: "Mars", 4: "Avril", 5: "Mai", 6: "Juin",
    7: "Juillet", 8: "Aout", 9: "Septembre", 10: "Octobre", 11: "Novembre",
    12: "Decembre",
}
NOMS_JOURS = {
    0: "Lundi", 1: "Mardi", 2: "Mercredi", 3: "Jeudi",
    4: "Vendredi", 5: "Samedi", 6: "Dimanche",
}
MOIS_CAMPAGNE_PRINCIPALE = {10, 11, 12, 1, 2, 3}

CHUNK_SIZE = 5_000

# Ordre de chargement, impose par les cles etrangeres
ORDRE_DIMENSIONS = ["dim_region", "dim_cooperative", "dim_planteur", "dim_qualite", "dim_date"]
TOUTES_LES_TABLES = ["faits_pesees", *ORDRE_DIMENSIONS]

CLES_FAITS = ["id_date", "id_region", "id_cooperative", "id_planteur", "id_qualite"]


# ---------------------------------------------------------------------------
# Construction des dimensions (aucune connexion a la base a ce stade)
# ---------------------------------------------------------------------------
def construire_dim_region(cooperatives: pd.DataFrame, planteurs: pd.DataFrame) -> pd.DataFrame:
    """
    Une ligne par region, avec deux attributs calcules.

    Une dimension qui ne contient qu'un identifiant et un nom n'apporte rien :
    le nombre de cooperatives et de planteurs rattaches permet de relativiser
    les volumes de production region par region.
    """
    regions = (
        cooperatives.groupby("region")
        .agg(
            zone_production=("zone_production", "first"),
            port_export=("port_export", "first"),
            nb_cooperatives=("code_cooperative", "count"),
        )
        .reset_index()
        .rename(columns={"region": "nom_region"})
    )
    planteurs_par_region = planteurs.groupby("region").size()
    regions["nb_planteurs"] = regions["nom_region"].map(planteurs_par_region).astype(int)

    return regions.sort_values("nom_region").reset_index(drop=True)


def construire_dim_cooperative(cooperatives: pd.DataFrame, planteurs: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par cooperative, avec son nombre d'adherents."""
    coops = cooperatives.copy()
    adherents = planteurs.groupby("code_cooperative").size()
    coops["nb_planteurs"] = coops["code_cooperative"].map(adherents).fillna(0).astype(int)

    colonnes = ["code_cooperative", "nom_cooperative", "region",
                "annee_creation", "certifiee_bio", "nb_planteurs"]
    return coops[colonnes].sort_values("code_cooperative").reset_index(drop=True)


def construire_dim_planteur(planteurs: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par planteur. Le code metier devient code_planteur."""
    dim = planteurs.rename(columns={"id_planteur": "code_planteur"}).copy()
    colonnes = ["code_planteur", "code_cooperative", "region",
                "superficie_ha", "annee_adhesion", "certifie_bio"]
    return dim[colonnes].sort_values("code_planteur").reset_index(drop=True)


def construire_dim_qualite() -> pd.DataFrame:
    """Les quatre grades du bareme, avec leurs seuils et leurs bornes de prix."""
    return pd.DataFrame(
        REFERENTIEL_QUALITES,
        columns=["nom_qualite", "rang", "humidite_max_pct",
                 "prix_plancher", "prix_plafond", "exportable"],
    )


def construire_dim_date(pesees: pd.DataFrame) -> pd.DataFrame:
    """
    Calendrier continu couvrant la periode des pesees.

    Tous les jours y figurent, y compris ceux sans aucune pesee : c'est ce qui
    permet ensuite de reperer les creux d'activite. Un calendrier construit a
    partir des seules dates presentes dans les faits les rendrait invisibles.
    """
    debut = pd.Timestamp(pesees["date"].min()).normalize()
    fin = pd.Timestamp(pesees["date"].max()).normalize()
    jours = pd.date_range(debut, fin, freq="D")

    dim = pd.DataFrame(
        {
            "date_complete": jours.date,
            "annee": jours.year,
            "mois": jours.month,
            "nom_mois": jours.month.map(NOMS_MOIS),
            "trimestre": jours.quarter,
            "semaine": jours.isocalendar().week.astype(int),
            "jour": jours.day,
            "nom_jour": jours.dayofweek.map(NOMS_JOURS),
            "est_weekend": jours.dayofweek >= 5,
        }
    )

    # Campagne et saison : calcules une fois ici, jamais dans les requetes
    dim["campagne"] = np.where(
        dim["mois"].isin(MOIS_CAMPAGNE_PRINCIPALE), "Principale", "Intermediaire"
    )
    annee_saison = np.where(dim["mois"] >= 10, dim["annee"], dim["annee"] - 1)
    dim["saison"] = [f"{a}-{a + 1}" for a in annee_saison]

    # Rang du mois dans la campagne : octobre = 1, septembre = 12.
    # Sans cette colonne, un tri par mois calendaire placerait janvier avant
    # octobre a l'interieur d'une meme saison, ce qui fausse toute comparaison
    # d'un mois au precedent.
    dim["mois_campagne"] = ((dim["mois"] - 10) % 12) + 1

    return dim


def construire_dimensions(
    pesees: pd.DataFrame, planteurs: pd.DataFrame, cooperatives: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Assemble les cinq dimensions dans l'ordre de chargement."""
    etape("Construction des dimensions")

    dimensions = {
        "dim_region": construire_dim_region(cooperatives, planteurs),
        "dim_cooperative": construire_dim_cooperative(cooperatives, planteurs),
        "dim_planteur": construire_dim_planteur(planteurs),
        "dim_qualite": construire_dim_qualite(),
        "dim_date": construire_dim_date(pesees),
    }
    for nom, dim in dimensions.items():
        console.print(f"  {nom:<18} : {len(dim):>5,} lignes x {len(dim.columns)} colonnes")

    return dimensions


# ---------------------------------------------------------------------------
# Resolution des cles etrangeres a l'interieur des dimensions
# ---------------------------------------------------------------------------
def resoudre_cles_dimensions(
    dimensions: dict[str, pd.DataFrame], lookups: dict[str, dict]
) -> dict[str, pd.DataFrame]:
    """
    Remplace les libelles par les cles techniques dans dim_cooperative et
    dim_planteur, qui portent la region et la cooperative de rattachement.
    """
    coops = dimensions["dim_cooperative"].copy()
    coops["id_region"] = coops["region"].map(lookups["region"])
    dimensions["dim_cooperative"] = coops.drop(columns="region")

    planteurs = dimensions["dim_planteur"].copy()
    planteurs["id_region"] = planteurs["region"].map(lookups["region"])
    planteurs["id_cooperative"] = planteurs["code_cooperative"].map(lookups["cooperative"])
    dimensions["dim_planteur"] = planteurs.drop(columns=["region", "code_cooperative"])

    return dimensions


def construire_faits(pesees: pd.DataFrame, lookups: dict[str, dict]) -> pd.DataFrame:
    """
    Remplace chaque libelle de la pesee par la cle technique de sa dimension.

    Le controle qui suit est essentiel : une cle non resolue signifie qu'un
    libelle n'existe pas dans la dimension correspondante. Supprimer ces lignes
    sans les compter ferait disparaitre du tonnage en silence.
    """
    etape("Construction de la table de faits")

    faits = pd.DataFrame(
        {
            "id_pesee": pesees["id_pesee"],
            "id_date": pd.to_datetime(pesees["date"]).dt.date.map(lookups["date"]),
            "id_region": pesees["region"].map(lookups["region"]),
            "id_cooperative": pesees["cooperative"].map(lookups["cooperative"]),
            "id_planteur": pesees["id_planteur"].map(lookups["planteur"]),
            "id_qualite": pesees["qualite"].map(lookups["qualite"]),
            "tonnage_kg": pesees["tonnage_kg"],
            "montant_fcfa": pesees["montant_fcfa"],
            "prix_fcfa_kg": pesees["prix_fcfa_kg"],
            "humidite_pct": pesees["humidite_pct"],
            "ecart_prix_grade_pct": pesees["ecart_prix_grade_pct"],
            "categorie_tonnage": pesees["categorie_tonnage"],
            "conforme_export": pesees["conforme_export"],
            "prix_impute": pesees["prix_impute"],
            "humidite_imputee": pesees["humidite_imputee"],
        }
    )

    non_resolues = faits[faits[CLES_FAITS].isna().any(axis=1)]
    if non_resolues.empty:
        ok(f"{len(faits):,} lignes, toutes les cles etrangeres sont resolues")
    else:
        alerte(f"{len(non_resolues):,} ligne(s) sans correspondance, elles seront ecartees")
        detail = (
            non_resolues[CLES_FAITS].isna().sum().to_frame("lignes_sans_cle").reset_index()
        )
        detail.columns = ["cle_etrangere", "lignes_sans_cle"]
        afficher_df(detail, "Detail des cles non resolues")
        faits = faits.dropna(subset=CLES_FAITS)

    faits[CLES_FAITS] = faits[CLES_FAITS].astype(int)
    return faits


# ---------------------------------------------------------------------------
# Chargement dans Supabase
# ---------------------------------------------------------------------------
def vider_etoile(conn) -> None:
    """
    Vide les six tables en une seule instruction.

    TRUNCATE refuse de vider une table referencee par une autre, sauf si cette
    autre table est citee dans la meme instruction : on les liste donc toutes.
    RESTART IDENTITY remet les compteurs SERIAL a 1, pour que les cles soient
    identiques a chaque execution du pipeline.
    """
    from sqlalchemy import text

    conn.execute(
        text(f"TRUNCATE {', '.join(TOUTES_LES_TABLES)} RESTART IDENTITY")
    )


def charger_dimensions(engine, dimensions: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """
    Insere les dimensions dans l'ordre, puis relit les cles attribuees.

    Le chargement se fait dimension par dimension, en relisant les cles au fur
    et a mesure : dim_cooperative a besoin des cles de dim_region avant d'etre
    inseree.
    """
    from sqlalchemy import text

    etape("Chargement des dimensions dans Supabase")
    lookups: dict[str, dict] = {}

    with engine.begin() as conn:
        vider_etoile(conn)

        # --- dim_region ---------------------------------------------------
        dimensions["dim_region"].to_sql(
            "dim_region", conn, if_exists="append", index=False, method="multi"
        )
        lookups["region"] = lire_lookup(conn, "dim_region", "nom_region", "id_region")
        ok(f"dim_region      : {len(dimensions['dim_region']):>5,} lignes")

        # --- dim_cooperative ----------------------------------------------
        coops = dimensions["dim_cooperative"].copy()
        coops["id_region"] = coops["region"].map(lookups["region"])
        coops = coops.drop(columns="region")
        coops.to_sql("dim_cooperative", conn, if_exists="append", index=False, method="multi")
        lookups["cooperative"] = lire_lookup(
            conn, "dim_cooperative", "code_cooperative", "id_cooperative"
        )
        ok(f"dim_cooperative : {len(coops):>5,} lignes")

        # --- dim_planteur --------------------------------------------------
        planteurs = dimensions["dim_planteur"].copy()
        planteurs["id_region"] = planteurs["region"].map(lookups["region"])
        planteurs["id_cooperative"] = planteurs["code_cooperative"].map(lookups["cooperative"])
        planteurs = planteurs.drop(columns=["region", "code_cooperative"])
        planteurs.to_sql(
            "dim_planteur", conn, if_exists="append", index=False,
            method="multi", chunksize=1_000,
        )
        lookups["planteur"] = lire_lookup(conn, "dim_planteur", "code_planteur", "id_planteur")
        ok(f"dim_planteur    : {len(planteurs):>5,} lignes")

        # --- dim_qualite ----------------------------------------------------
        dimensions["dim_qualite"].to_sql(
            "dim_qualite", conn, if_exists="append", index=False, method="multi"
        )
        lookups["qualite"] = lire_lookup(conn, "dim_qualite", "nom_qualite", "id_qualite")
        ok(f"dim_qualite     : {len(dimensions['dim_qualite']):>5,} lignes")

        # --- dim_date -------------------------------------------------------
        dimensions["dim_date"].to_sql(
            "dim_date", conn, if_exists="append", index=False,
            method="multi", chunksize=1_000,
        )
        dates = pd.read_sql(text("SELECT date_complete, id_date FROM dim_date"), conn)
        dates["date_complete"] = pd.to_datetime(dates["date_complete"]).dt.date
        lookups["date"] = dict(zip(dates["date_complete"], dates["id_date"]))
        ok(f"dim_date        : {len(dimensions['dim_date']):>5,} lignes")

    return lookups


def lire_lookup(conn, table: str, colonne_code: str, colonne_cle: str) -> dict:
    """Relit les couples code metier / cle technique attribues par PostgreSQL."""
    from sqlalchemy import text

    df = pd.read_sql(text(f"SELECT {colonne_code}, {colonne_cle} FROM {table}"), conn)
    return dict(zip(df[colonne_code], df[colonne_cle]))


def charger_faits(engine, faits: pd.DataFrame) -> None:
    """Insere la table de faits par lots, avec barre de progression."""
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    etape(f"Chargement de faits_pesees ({len(faits):,} lignes)")

    lots = [faits[i : i + CHUNK_SIZE] for i in range(0, len(faits), CHUNK_SIZE)]
    debut = time.time()

    with engine.begin() as conn:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed:,}/{task.total:,} lignes"),
            TimeElapsedColumn(),
            console=console,
        ) as progression:
            tache = progression.add_task("Insertion", total=len(faits))
            for lot in lots:
                lot.to_sql(
                    "faits_pesees", conn, if_exists="append", index=False,
                    method="multi", chunksize=500,
                )
                progression.update(tache, advance=len(lot))

    ok(f"{len(faits):,} lignes chargees en {time.time() - debut:.1f}s ({len(lots)} lots)")


def verifier(engine) -> None:
    """Compte les lignes et controle l'integrite cote base."""
    from sqlalchemy import text

    etape("Verification cote Supabase")

    with engine.connect() as conn:
        for table in TOUTES_LES_TABLES:
            nb = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            console.print(f"  {table:<18} : [bold]{nb:,}[/] ligne(s)")

    # Jointure de controle sur les cinq dimensions : si les cles sont bonnes,
    # le compte est identique a celui de la table de faits.
    requete = """
        SELECT COUNT(*) AS nb
        FROM faits_pesees f
        JOIN dim_date d        ON f.id_date        = d.id_date
        JOIN dim_region r      ON f.id_region      = r.id_region
        JOIN dim_cooperative c ON f.id_cooperative = c.id_cooperative
        JOIN dim_planteur p    ON f.id_planteur    = p.id_planteur
        JOIN dim_qualite q     ON f.id_qualite     = q.id_qualite
    """
    nb_joint = pd.read_sql(requete, engine)["nb"].iloc[0]
    ok(f"Jointure sur les 5 dimensions : {nb_joint:,} lignes")

    # Controle de coherence : la region de la pesee doit etre celle de la
    # cooperative. C'est la contrepartie assumee du choix d'etoile.
    requete = """
        SELECT COUNT(*) AS nb
        FROM faits_pesees f
        JOIN dim_cooperative c ON f.id_cooperative = c.id_cooperative
        WHERE f.id_region <> c.id_region
    """
    incoherentes = pd.read_sql(requete, engine)["nb"].iloc[0]
    if incoherentes:
        echec(f"{incoherentes:,} pesee(s) dont la region contredit celle de la cooperative")
    else:
        ok("Region de la pesee coherente avec celle de la cooperative")

    # Premier resultat metier, pour verifier que le schema repond
    requete = """
        SELECT
            r.nom_region,
            COUNT(*)                        AS nb_pesees,
            ROUND(SUM(f.tonnage_kg) / 1000) AS tonnes,
            ROUND(SUM(f.montant_fcfa) / SUM(f.tonnage_kg)) AS prix_moyen_pondere
        FROM faits_pesees f
        JOIN dim_region r ON f.id_region = r.id_region
        GROUP BY r.nom_region
        ORDER BY tonnes DESC
    """
    afficher_df(pd.read_sql(requete, engine), "Production par region (controle)")


# ---------------------------------------------------------------------------
# Mode sans base
# ---------------------------------------------------------------------------
def simuler_lookups(dimensions: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """
    Attribue les cles techniques comme le ferait PostgreSQL : 1, 2, 3...

    Sert uniquement au mode --dry-run, pour valider la logique de resolution
    des cles etrangeres sans connexion a la base.
    """
    return {
        "region": {v: i for i, v in enumerate(dimensions["dim_region"]["nom_region"], 1)},
        "cooperative": {
            v: i for i, v in enumerate(dimensions["dim_cooperative"]["code_cooperative"], 1)
        },
        "planteur": {
            v: i for i, v in enumerate(dimensions["dim_planteur"]["code_planteur"], 1)
        },
        "qualite": {v: i for i, v in enumerate(dimensions["dim_qualite"]["nom_qualite"], 1)},
        "date": {v: i for i, v in enumerate(dimensions["dim_date"]["date_complete"], 1)},
    }


def ajouter_cles_simulees(
    dimensions: dict[str, pd.DataFrame], lookups: dict[str, dict]
) -> dict[str, pd.DataFrame]:
    """
    Ajoute la colonne de cle technique aux dimensions, en mode --dry-run.

    Sans elle, la copie Parquet ne refleterait pas ce que contient la base :
    on ne pourrait pas rejouer les requetes analytiques en local sur ces
    fichiers pour verifier une jointure.
    """
    correspondances = {
        "dim_region": ("id_region", "nom_region", "region"),
        "dim_cooperative": ("id_cooperative", "code_cooperative", "cooperative"),
        "dim_planteur": ("id_planteur", "code_planteur", "planteur"),
        "dim_qualite": ("id_qualite", "nom_qualite", "qualite"),
        "dim_date": ("id_date", "date_complete", "date"),
    }
    for table, (colonne_cle, colonne_code, cle_lookup) in correspondances.items():
        dim = dimensions[table].copy()
        dim.insert(0, colonne_cle, dim[colonne_code].map(lookups[cle_lookup]).astype(int))
        dimensions[table] = dim
    return dimensions


def main() -> None:
    mode_seul = "--dry-run" in sys.argv

    titre(
        "ETAPE 3B : peuplement du schema en etoile",
        "mode sans base" if mode_seul else "Supabase PostgreSQL",
    )

    pesees = charger_etape("pesees_propres")
    planteurs = charger_etape("brut_planteurs")
    cooperatives = charger_etape("brut_cooperatives")

    dimensions = construire_dimensions(pesees, planteurs, cooperatives)

    if mode_seul:
        lookups = simuler_lookups(dimensions)
        dimensions = resoudre_cles_dimensions(dimensions, lookups)
        faits = construire_faits(pesees, lookups)

        # Les tables sont larges : on n'affiche que les colonnes utiles au
        # controle visuel, la copie Parquet contient tout.
        apercus = {
            "dim_region": ["nom_region", "zone_production", "port_export", "nb_planteurs"],
            "dim_cooperative": ["code_cooperative", "id_region", "nb_planteurs"],
            "dim_planteur": ["code_planteur", "id_cooperative", "id_region", "superficie_ha"],
            "dim_qualite": ["nom_qualite", "rang", "humidite_max_pct", "exportable"],
            "dim_date": ["date_complete", "nom_mois", "campagne", "saison"],
        }
        for nom, colonnes in apercus.items():
            afficher_df(dimensions[nom][colonnes].head(4), f"{nom} (extrait)", max_lignes=4)

        afficher_df(
            faits[["id_pesee", *CLES_FAITS, "tonnage_kg", "montant_fcfa"]].head(4),
            "faits_pesees (extrait)",
            max_lignes=4,
        )

        alerte("Mode sans base : rien n'a ete ecrit dans Supabase.")
        dimensions = ajouter_cles_simulees(dimensions, lookups)
    else:
        engine = get_engine()
        try:
            lookups = charger_dimensions(engine, dimensions)
            faits = construire_faits(pesees, lookups)
            charger_faits(engine, faits)
            verifier(engine)
        finally:
            engine.dispose()

        # Les dimensions archivees portent les memes cles que celles chargees
        dimensions = resoudre_cles_dimensions(dimensions, lookups)

    # Copie locale : elle sert de reference pour les etapes suivantes et evite
    # de tout recharger pour une simple verification.
    for nom, dim in dimensions.items():
        sauver_etape(dim, f"etoile_{nom}")
    sauver_etape(faits, "etoile_faits_pesees")
    console.print("[dim]Copie des six tables archivee dans data/interim/[/]")


if __name__ == "__main__":
    main()