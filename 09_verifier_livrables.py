"""
Controle l'arborescence et les livrables avant le depot.

Trois verifications :
  1. chaque fichier attendu par le bareme est present, et au bon endroit
  2. aucun identifiant en clair ne traine dans les fichiers versionnes
  3. les fichiers qui ne doivent pas etre versionnes sont bien ignores
"""

from __future__ import annotations

import re
import subprocess

import pandas as pd

from etl_common import (
    BASE_DIR,
    afficher_df,
    alerte,
    console,
    echec,
    etape,
    ok,
    taille_lisible,
    titre,
)

# ---------------------------------------------------------------------------
# Livrables attendus, avec le critere du bareme correspondant
# ---------------------------------------------------------------------------
LIVRABLES = {
    # Code du pipeline
    "01_generer_dataset.py": "Critere 1 : generation du dataset",
    "02_extraction_audit.py": "Critere 1 : extraction et audit",
    "03_transformation.py": "Critere 1 : nettoyage et enrichissement",
    "qualite.py": "Critere 1 : 7 regles de validation",
    "04_creer_schema.py": "Critere 2 : schema en etoile",
    "05_charger_etoile.py": "Critere 2 : peuplement des dimensions et des faits",
    "requetes.py": "Critere 2 : requetes analytiques",
    "06_requetes_sql.py": "Critere 2 : execution des requetes",
    "07_dashboard.py": "Critere 3 : tableau de bord",
    "08_dbt_run.py": "Critere 2 bonus : modele dbt",
    "etl_common.py": "boite a outils partagee",

    # SQL
    "sql/01_creer_schema_etoile.sql": "Critere 2 : DDL du schema en etoile",
    "sql/02_requetes_analytiques.sql": "Critere 2 : requetes pour le SQL Editor",

    # Orchestration et conteneurisation
    "dags/dag_pipeline_cacao.py": "Critere 3 : DAG Airflow",
    "dags/callbacks.py": "Critere 3 : alertes Airflow",
    "Dockerfile": "Critere 3 : conteneurisation",
    "docker-compose.yml": "Critere 3 : orchestration des conteneurs",
    "requirements.txt": "Livrable 1 : dependances du conteneur",

    # dbt
    "dbt_projet/dbt_project.yml": "Critere 2 bonus : configuration dbt",
    "dbt_projet/profiles.yml": "Critere 2 bonus : connexion dbt",
    "dbt_projet/models/analytics/perf_cooperatives.sql": "Critere 2 bonus : modele dbt",
    "dbt_projet/models/analytics/schema.yml": "Critere 2 bonus : tests dbt",

    # Documentation et rendu
    "README.md": "Critere 4 : README professionnel",
    "notebook_projet_cacao.ipynb": "Livrable 1 : notebook executable",
    "journal.md": "matiere premiere du rapport",
    "pyproject.toml": "Livrable 1 : dependances du projet",
    ".env.example": "modele de configuration, sans secret",
    ".gitignore": "exclusion des secrets et des donnees",

    # Sorties a conserver dans le depot
    "data/output/dashboard_cacao.png": "Critere 3 : tableau de bord PNG",
    "data/output/rapport_qualite.json": "Critere 1 : rapport de qualite",
    "data/output/rapport_audit.json": "Critere 1 : rapport d'audit",
}

# Fichiers qui ne doivent JAMAIS partir sur un depot public
NE_DOIT_PAS_ETRE_VERSIONNE = [
    ".env",
    "data_notebook",
    "__pycache__",
    "logs",
    "dbt_projet/target",
    "data/raw",
    "data/interim",
]

# Motifs revelant un identifiant en clair
MOTIFS_SECRETS = [
    (r"postgresql(\+psycopg2)?://[^\s'\"]*:[^\s'\"@]+@", "chaine de connexion avec mot de passe"),
    (r"(?i)password\s*[:=]\s*['\"][^'\"{}$]{4,}", "mot de passe en clair"),
    (r"eyJ[A-Za-z0-9_-]{20,}", "jeton JWT"),
]

# Valeurs d'exemple, volontairement neutres : ce ne sont pas des fuites
MARQUES_EXEMPLE = ("xxxx", "MOT_DE_PASSE", "VOTRE", "airflow_secret", "env_var")

EXTENSIONS_A_SCANNER = {".py", ".yml", ".yaml", ".md", ".txt", ".sql", ".ipynb", ".toml"}


def controler_livrables() -> bool:
    """Confronte les fichiers presents a la liste du bareme."""
    etape("Livrables attendus")

    lignes = []
    for relatif, critere in LIVRABLES.items():
        chemin = BASE_DIR / relatif
        lignes.append(
            {
                "livrable": relatif,
                "critere": critere,
                "taille": taille_lisible(chemin) if chemin.exists() else "-",
                "present": "oui" if chemin.exists() else "NON",
            }
        )

    tableau = pd.DataFrame(lignes)
    manquants = tableau.query("present == 'NON'")

    if manquants.empty:
        afficher_df(tableau, "Livrables du projet", max_lignes=40)
        ok(f"Les {len(tableau)} livrables attendus sont presents")
        return True

    afficher_df(manquants, "LIVRABLES MANQUANTS", max_lignes=20)
    echec(f"{len(manquants)} livrable(s) manquant(s) sur {len(tableau)}")
    return False


def chercher_secrets() -> None:
    """Cherche des identifiants en clair dans les fichiers texte du depot."""
    etape("Recherche de secrets en clair")

    trouvailles = []
    for chemin in BASE_DIR.rglob("*"):
        if not chemin.is_file() or chemin.suffix not in EXTENSIONS_A_SCANNER:
            continue
        # On ignore ce qui n'est de toute facon pas versionne
        if any(exclu in chemin.parts for exclu in ("__pycache__", ".venv", "data_notebook",
                                                   "target", "logs")):
            continue

        contenu = chemin.read_text(encoding="utf-8", errors="ignore")
        for motif, libelle in MOTIFS_SECRETS:
            for correspondance in re.finditer(motif, contenu):
                extrait = correspondance.group(0)
                if any(marque in extrait for marque in MARQUES_EXEMPLE):
                    continue
                trouvailles.append(
                    {
                        "fichier": str(chemin.relative_to(BASE_DIR)),
                        "type": libelle,
                        "extrait": extrait[:45] + "...",
                    }
                )

    if trouvailles:
        afficher_df(pd.DataFrame(trouvailles), "SECRETS POTENTIELS")
        echec("Retirez ces valeurs avant de pousser le depot")
    else:
        ok("Aucun identifiant en clair detecte")


def controler_gitignore() -> None:
    """
    Verifie que git ignore bien ce qui ne doit pas etre publie.

    git check-ignore repond 0 si le chemin est ignore, 1 sinon.
    """
    etape("Verification du .gitignore")

    if not (BASE_DIR / ".git").exists():
        alerte("Pas de depot git ici, verification ignoree")
        return

    lignes = []
    for chemin in NE_DOIT_PAS_ETRE_VERSIONNE:
        resultat = subprocess.run(
            ["git", "check-ignore", "-q", chemin],
            cwd=BASE_DIR, capture_output=True,
        )
        lignes.append({"chemin": chemin, "ignore_par_git": "oui" if resultat.returncode == 0 else "NON"})

    tableau = pd.DataFrame(lignes)
    afficher_df(tableau, "Fichiers qui ne doivent pas etre versionnes", max_lignes=20)

    exposes = tableau.query("ignore_par_git == 'NON'")
    if exposes.empty:
        ok("Tout ce qui doit rester local est bien ignore")
    else:
        alerte(f"{len(exposes)} chemin(s) non ignore(s), verifiez le .gitignore")

    # Fichiers deja suivis par git alors qu'ils ne devraient pas l'etre
    suivis = subprocess.run(
        ["git", "ls-files"], cwd=BASE_DIR, capture_output=True, text=True
    ).stdout.splitlines()
    indesirables = [f for f in suivis
                    if any(f.startswith(prefixe) for prefixe in NE_DOIT_PAS_ETRE_VERSIONNE)]
    if indesirables:
        alerte(f"{len(indesirables)} fichier(s) deja suivis par git alors qu'ils devraient etre exclus")
        console.print("  [dim]Corriger avec : git rm -r --cached <dossier>[/]")
        for fichier in indesirables[:5]:
            console.print(f"    {fichier}")


def afficher_rappels() -> None:
    """Rappelle ce qui ne peut pas etre verifie automatiquement."""
    etape("A verifier manuellement")
    for rappel in [
        "Nom, numero d'etudiant et lien GitHub renseignes en page de garde du notebook",
        "VOTRE_COMPTE remplace par le vrai compte GitHub dans le README",
        "Depot GitHub public et lien communique a l'enseignant",
        "Captures Supabase : Table Editor, 2 requetes SQL, vue perf_cooperatives",
        "Captures Airflow : vue Graph du DAG, docker compose ps",
        "Rapport PDF de 10 pages minimum, mention des outils d'IA en introduction",
        "Slides de soutenance, 10 a 15 pages",
    ]:
        console.print(f"  [ ] {rappel}")


def main() -> None:
    titre("VERIFICATION DES LIVRABLES", "a lancer avant le depot")

    complet = controler_livrables()
    chercher_secrets()
    controler_gitignore()
    afficher_rappels()

    console.print()
    if complet:
        ok("Arborescence conforme")
    else:
        echec("Arborescence incomplete, voir la liste ci-dessus")


if __name__ == "__main__":
    main()