"""
Execute le modele dbt et ses tests.

dbt s'installe a part, jamais dans les dependances du projet : il embarque ses
propres versions de plusieurs bibliotheques et entre facilement en conflit
avec pandas.

    uv tool install dbt-postgres

Le script :
  1. verifie que les variables DBT_* sont definies dans .env
  2. lance `dbt run`  : cree ou rafraichit la vue perf_cooperatives
  3. lance `dbt test` : execute les tests declares dans schema.yml
  4. relit la vue pour confirmer qu'elle est exploitable
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pandas as pd

from etl_common import (
    BASE_DIR,
    afficher_df,
    alerte,
    console,
    echec,
    etape,
    get_engine,
    ok,
    titre,
)

DBT_DIR = BASE_DIR / "dbt_projet"
VARIABLES_REQUISES = ["DBT_HOST", "DBT_PORT", "DBT_USER", "DBT_PASSWORD"]


def verifier_configuration() -> bool:
    """Controle la presence des variables lues par profiles.yml."""
    etape("Verification de la configuration dbt")

    absentes = [nom for nom in VARIABLES_REQUISES if not os.getenv(nom)]
    if absentes:
        alerte(f"Variables manquantes dans .env : {absentes}")
        console.print("[dim]Voir le bloc dbt du fichier .env.example[/]")
        return False

    ok("Variables DBT_* presentes")
    return True


def commande_dbt() -> list[str] | None:
    """
    Determine comment appeler dbt.

    S'il est installe globalement (uv tool install), la commande est directe.
    Sinon uvx le telecharge et l'execute dans un environnement temporaire.
    """
    if shutil.which("dbt"):
        return ["dbt"]
    if shutil.which("uvx"):
        return ["uvx", "--from", "dbt-postgres", "dbt"]
    return None


def lancer(base: list[str], sous_commande: str) -> bool:
    """Execute une sous-commande dbt et affiche sa sortie."""
    etape(f"dbt {sous_commande}")

    resultat = subprocess.run(
        [*base, sous_commande,
         "--project-dir", str(DBT_DIR),
         "--profiles-dir", str(DBT_DIR)],
        capture_output=True,
        text=True,
    )
    console.print(f"[dim]{(resultat.stdout + resultat.stderr).strip()}[/]")

    if resultat.returncode == 0:
        ok(f"dbt {sous_commande} termine avec succes")
        return True
    echec(f"dbt {sous_commande} a echoue (code {resultat.returncode})")
    return False


def verifier_vue() -> None:
    """Interroge la vue creee par dbt pour confirmer qu'elle repond."""
    etape("Lecture de la vue perf_cooperatives")

    engine = get_engine()
    try:
        df = pd.read_sql("SELECT * FROM perf_cooperatives ORDER BY rang_national LIMIT 10", engine)
        afficher_df(df, "Vue perf_cooperatives creee par dbt", max_lignes=10)
        ok("Vue visible dans Supabase : Table Editor, section Views")
    except Exception as erreur:
        alerte(f"Vue introuvable : {erreur}")
    finally:
        engine.dispose()


def main() -> None:
    titre("ETAPE 7 : modele dbt", "vue analytique perf_cooperatives")

    if not verifier_configuration():
        return

    base = commande_dbt()
    if base is None:
        alerte("dbt introuvable. Installez-le puis relancez ce script :")
        console.print("  [bold]uv tool install dbt-postgres[/]")
        return

    console.print(f"[dim]commande utilisee : {' '.join(base)}[/]")

    if lancer(base, "run"):
        lancer(base, "test")   # tests unique, not_null, relationships, accepted_values
        verifier_vue()


if __name__ == "__main__":
    main()