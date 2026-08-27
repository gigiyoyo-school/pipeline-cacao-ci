"""
Cree le schema en etoile dans Supabase et verifie sa structure.

Le script est idempotent : toutes les instructions sont en CREATE ... IF NOT EXISTS.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from etl_common import (
    SQL_DIR,
    afficher_df,
    alerte,
    console,
    echec,
    etape,
    get_engine,
    ok,
    titre,
)

TABLES_ATTENDUES = [
    "dim_region",
    "dim_cooperative",
    "dim_planteur",
    "dim_qualite",
    "dim_date",
    "faits_pesees",
]


def creer(engine) -> None:
    """Execute le fichier DDL complet."""
    etape("Creation des tables")

    fichier = SQL_DIR / "01_creer_schema_etoile.sql"
    sql = fichier.read_text(encoding="utf-8")

    # engine.begin() ouvre une transaction et valide automatiquement en sortie.
    # En cas d'erreur, tout est annule : pas de schema a moitie cree.
    with engine.begin() as conn:
        conn.execute(text(sql))

    ok(f"{fichier.name} execute")


def verifier_tables(engine) -> bool:
    """Confronte les tables presentes a celles attendues."""
    etape("Verification des tables")

    presentes = set(
        pd.read_sql(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
            engine,
        )["table_name"]
    )

    lignes = [
        {"table": t, "present": "oui" if t in presentes else "NON"} for t in TABLES_ATTENDUES
    ]
    afficher_df(pd.DataFrame(lignes), "Tables du schema en etoile")

    manquantes = [t for t in TABLES_ATTENDUES if t not in presentes]
    if manquantes:
        echec(f"Tables manquantes : {manquantes}")
        return False

    ok("Les 6 tables du schema en etoile sont en place")
    return True


def verifier_cles_etrangeres(engine) -> None:
    """
    Liste les contraintes de cle etrangere reellement creees.

    C'est la preuve que le schema est un vrai schema en etoile et non six
    tables independantes : le bareme evalue explicitement des cles etrangeres
    correctes. Cette sortie est a inserer dans le rapport.
    """
    etape("Verification des cles etrangeres")

    requete = """
        SELECT
            tc.table_name        AS table_source,
            kcu.column_name      AS colonne,
            ccu.table_name       AS table_cible,
            ccu.column_name      AS colonne_cible
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
             ON tc.constraint_name = kcu.constraint_name
        JOIN information_schema.constraint_column_usage ccu
             ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name IN ('dim_cooperative', 'dim_planteur', 'faits_pesees')
        ORDER BY tc.table_name, kcu.column_name
    """
    contraintes = pd.read_sql(requete, engine)
    afficher_df(contraintes, "Contraintes de cle etrangere", max_lignes=20)

    nb_faits = int((contraintes["table_source"] == "faits_pesees").sum())
    if nb_faits == 5:
        ok("La table de faits pointe vers les 5 dimensions")
    else:
        alerte(f"La table de faits ne declare que {nb_faits} cle(s) etrangere(s) sur 5")


def verifier_structure_faits(engine) -> None:
    """Affiche la structure de la table de faits, utile pour le rapport."""
    requete = """
        SELECT column_name AS colonne, data_type AS type, is_nullable AS nullable
        FROM information_schema.columns
        WHERE table_name = 'faits_pesees'
        ORDER BY ordinal_position
    """
    afficher_df(pd.read_sql(requete, engine), "Structure de faits_pesees", max_lignes=20)


def main() -> None:
    titre("ETAPE 3A : creation du schema en etoile", "Supabase PostgreSQL")

    engine = get_engine()
    try:
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
        ok(f"Connexion Supabase etablie : {version[:40]}...")

        creer(engine)
        if verifier_tables(engine):
            verifier_cles_etrangeres(engine)
            verifier_structure_faits(engine)
    finally:
        engine.dispose()   # ferme proprement toutes les connexions du pool
        console.print("[dim]Connexion fermee.[/]")


if __name__ == "__main__":
    main()