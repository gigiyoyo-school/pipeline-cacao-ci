"""
Boite a outils partagee par tous les scripts du projet.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ---------------------------------------------------------------------------
# Console rich partagee par tous les scripts
# ---------------------------------------------------------------------------
console = Console()

# ---------------------------------------------------------------------------
# Chemins du projet
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_RAW = BASE_DIR / "data" / "raw"          # donnees generees, sources du pipeline
DATA_INTERIM = BASE_DIR / "data" / "interim"  # etapes intermediaires (Parquet)
DATA_OUTPUT = BASE_DIR / "data" / "output"    # livrables (dashboard, rapports)
SQL_DIR = BASE_DIR / "sql"

for dossier in (DATA_RAW, DATA_INTERIM, DATA_OUTPUT):
    dossier.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# Noms des trois fichiers sources produits par 01_generer_dataset.py
FICHIER_PESEES = DATA_RAW / "pesees_cacao_ci_80k.csv"
FICHIER_PLANTEURS = DATA_RAW / "referentiel_planteurs.csv"
FICHIER_COOPERATIVES = DATA_RAW / "referentiel_cooperatives.csv"


def get_engine():
    """Moteur SQLAlchemy vers Supabase, construit a partir de SUPABASE_URL."""
    import sqlalchemy

    url = os.getenv("SUPABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "SUPABASE_URL absente. Copiez .env.example en .env et collez votre "
            "chaine de connexion Supabase."
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return sqlalchemy.create_engine(url, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# Normalisation des libelles
# ---------------------------------------------------------------------------
def normaliser(texte) -> str:
    """
    Reduit un libelle a une forme canonique servant de cle de comparaison.

    Accents supprimes, minuscules, tout caractere non alphanumerique remplace
    par une espace, espaces multiples reduits a une seule.

    Exemple: 'San Pedro', ' SAN  PEDRO ' et 'San-Pedro' donnent tous 'san pedro'.
    """
    if not isinstance(texte, str):
        return ""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", sans_accent.lower()).strip()


def construire_lookup(valeurs) -> dict[str, str]:
    """
    Construit un dictionnaire forme normalisee -> libelle officiel.

    Les valeurs de reference viennent des referentiels : ce sont elles qui
    font foi, pas les libelles saisis sur la bascule.
    """
    return {normaliser(valeur): valeur for valeur in valeurs}


# ---------------------------------------------------------------------------
# Helpers d'affichage
# ---------------------------------------------------------------------------
def titre(texte: str, sous_titre: str = "") -> None:
    console.print()
    console.print(Panel.fit(f"[bold cyan]{texte}[/]", subtitle=sous_titre))


def etape(texte: str) -> None:
    console.print(f"[bold blue]>[/] {texte}")


def ok(texte: str) -> None:
    console.print(f"[bold green]OK[/] {texte}")


def alerte(texte: str) -> None:
    console.print(f"[bold yellow]! [/] {texte}")


def echec(texte: str) -> None:
    console.print(f"[bold red]KO[/] {texte}")


def format_valeur(valeur) -> str:
    """Formate une valeur pour l'affichage dans une table rich."""
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return "-"
    if isinstance(valeur, float):
        return f"{valeur:,.2f}"
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, int):
        # Separateur de milliers au-dela de 9999 : sinon une annee s'afficherait "2,023"
        return f"{valeur:,}" if abs(valeur) >= 10_000 else str(valeur)
    return str(valeur)


def afficher_df(df: pd.DataFrame, titre_table: str, max_lignes: int = 15) -> None:
    """Affiche un DataFrame sous forme de table rich."""
    table = Table(title=titre_table, header_style="bold magenta")
    for colonne in df.columns:
        justify = "right" if pd.api.types.is_numeric_dtype(df[colonne]) else "left"
        table.add_column(str(colonne), justify=justify, overflow="fold")
    for _, ligne in df.head(max_lignes).iterrows():
        table.add_row(*[format_valeur(v) for v in ligne])
    console.print(table)
    if len(df) > max_lignes:
        console.print(f"[dim]... {len(df) - max_lignes} ligne(s) non affichee(s)[/]")


def taille_lisible(chemin: Path) -> str:
    """Taille d'un fichier en Ko ou Mo."""
    octets = chemin.stat().st_size
    if octets < 1024**2:
        return f"{octets / 1024:.1f} Ko"
    return f"{octets / 1024**2:.1f} Mo"


# ---------------------------------------------------------------------------
# Etapes intermediaires en Parquet
# ---------------------------------------------------------------------------
def sauver_etape(df: pd.DataFrame, nom: str) -> Path:
    chemin = DATA_INTERIM / f"{nom}.parquet"
    df.to_parquet(chemin, engine="pyarrow", compression="snappy", index=False)
    return chemin


def charger_etape(nom: str) -> pd.DataFrame:
    chemin = DATA_INTERIM / f"{nom}.parquet"
    if not chemin.exists():
        raise FileNotFoundError(
            f"Fichier intermediaire manquant : {chemin}\n"
            "Lancez d'abord le script de l'etape precedente."
        )
    return pd.read_parquet(chemin, engine="pyarrow")