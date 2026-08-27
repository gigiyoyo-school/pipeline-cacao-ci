"""
Execute les six requetes analytiques et archive leurs resultats.

Deux modes :

    uv run 06_requetes_sql.py            sur Supabase, via SQLAlchemy
    uv run 06_requetes_sql.py --local    sur les fichiers Parquet, via DuckDB

Le mode local sert a mettre au point une requete sans solliciter la base : il
lit les copies Parquet des six tables et execute exactement le meme SQL.
DuckDB comprend la syntaxe PostgreSQL utilisee ici, fonctions de fenetre et
CTE comprises.

Le script regenere aussi sql/02_requetes_analytiques.sql a partir du module
requetes.py. Les requetes n'existent donc qu'a un seul endroit : impossible
que le fichier a coller dans le SQL Editor diverge de celui qui tourne.

Sorties : data/interim/resultat_<requete>.parquet
          data/output/resultats_sql.json
          sql/02_requetes_analytiques.sql
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import pandas as pd

from etl_common import (
    DATA_INTERIM,
    DATA_OUTPUT,
    SQL_DIR,
    afficher_df,
    alerte,
    console,
    etape,
    get_engine,
    ok,
    sauver_etape,
    titre,
)
from requetes import REQUETES

TABLES_ETOILE = [
    "dim_region",
    "dim_cooperative",
    "dim_planteur",
    "dim_qualite",
    "dim_date",
    "faits_pesees",
]


def executer_sur_supabase() -> dict[str, pd.DataFrame]:
    """Execute les requetes sur la base, via SQLAlchemy."""
    etape("Execution sur Supabase")
    engine = get_engine()
    resultats = {}
    try:
        for cle, (sql, _, _) in REQUETES.items():
            # pd.read_sql execute la requete et renvoie directement un DataFrame
            resultats[cle] = pd.read_sql(sql, engine)
    finally:
        engine.dispose()   # ferme toutes les connexions du pool
    return resultats


def executer_en_local() -> dict[str, pd.DataFrame]:
    """
    Execute les memes requetes sur les copies Parquet, via DuckDB.

    Les fichiers sont exposes comme des tables portant le nom attendu par le
    SQL : aucune requete n'a besoin d'etre adaptee.
    """
    try:
        import duckdb
    except ImportError:
        alerte("DuckDB n'est pas installe. Ajoutez-le avec : uv add --dev duckdb")
        sys.exit(1)

    etape("Execution en local sur les fichiers Parquet")
    connexion = duckdb.connect()
    for table in TABLES_ETOILE:
        chemin = DATA_INTERIM / f"etoile_{table}.parquet"
        if not chemin.exists():
            alerte(f"Fichier manquant : {chemin.name}. Lancez 05_charger_etoile.py --dry-run")
            sys.exit(1)
        connexion.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{chemin}')")

    return {cle: connexion.execute(sql).df() for cle, (sql, _, _) in REQUETES.items()}


def generer_fichier_sql() -> None:
    """
    Reconstruit sql/02_requetes_analytiques.sql a partir du module requetes.py.

    C'est ce fichier que vous collez dans le SQL Editor de Supabase pour les
    captures d'ecran du rapport.
    """
    lignes = [
        "-- ============================================================",
        "-- REQUETES ANALYTIQUES : FILIERE CACAO IVOIRIENNE",
        "-- A executer dans : Supabase -> SQL Editor",
        "--",
        "-- Fichier genere automatiquement par 06_requetes_sql.py a partir de",
        "-- requetes.py. Ne pas le modifier a la main : les corrections seraient",
        "-- perdues au prochain lancement du script.",
        "-- ============================================================",
        "",
    ]
    for _, (sql, titre_requete, lecture) in REQUETES.items():
        lignes.extend(
            [
                "",
                "-- ------------------------------------------------------------",
                f"-- {titre_requete.upper()}",
                f"-- Question metier : {lecture}",
                "-- ------------------------------------------------------------",
                sql.strip() + ";",
                "",
            ]
        )

    chemin = SQL_DIR / "02_requetes_analytiques.sql"
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    ok(f"Fichier SQL regenere : sql/{chemin.name}")


def main() -> None:
    mode_local = "--local" in sys.argv

    titre(
        "ETAPE 4 : requetes analytiques",
        "DuckDB en local" if mode_local else "Supabase PostgreSQL",
    )

    resultats = executer_en_local() if mode_local else executer_sur_supabase()

    for cle, df in resultats.items():
        _, titre_requete, lecture = REQUETES[cle]
        afficher_df(df, titre_requete, max_lignes=12)
        console.print(f"  [dim]{lecture}[/]\n")

        # Chaque resultat est archive : le tableau de bord de l'etape suivante
        # repart de ces fichiers plutot que de reinterroger la base.
        sauver_etape(df, f"resultat_{cle}")

    generer_fichier_sql()

    rapport = {
        "genere_le": datetime.now().isoformat(timespec="seconds"),
        "source": "duckdb_local" if mode_local else "supabase",
        "resultats": {
            cle: df.to_dict(orient="records") for cle, df in resultats.items()
        },
    }
    chemin = DATA_OUTPUT / "resultats_sql.json"
    chemin.write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    ok(f"Resultats exportes : data/output/{chemin.name}")


if __name__ == "__main__":
    main()