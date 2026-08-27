"""
Tableau de bord analytique de la filiere cacao.

Six graphiques, dont les trois explicitement demandes par le sujet :
cartographie des regions en barres, evolution des prix, distribution des
qualites.

    1. Production par region (barres horizontales)
    2. Saisonnalite : tonnage par mois de campagne, une serie par saison
    3. Evolution du prix moyen pondere au fil de la campagne
    4. Distribution des qualites et conformite a la norme d'exportation
    5. Effet de la certification bio sur le prix, a grade egal
    6. Classement des cooperatives par tonnage collecte

Les donnees viennent des resultats SQL archives a l'etape 4 : le tableau de
bord se regenere sans solliciter Supabase.

Sorties : data/output/dashboard_cacao.png                  (planche complete)
          data/output/graphiques/g1_*.png ... g6_*.png     (un fichier par graphique)
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")   # backend sans fenetre, indispensable hors notebook

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from etl_common import (
    DATA_OUTPUT,
    charger_etape,
    console,
    etape,
    ok,
    taille_lisible,
    titre,
)

# Resolution imposee par le sujet : au moins 150 dpi
DPI = 150

# Palette inspiree de la filiere : bruns de la feve, vert du cacaoyer
BRUN_FONCE = "#4E342E"
BRUN = "#6D4C41"
OCRE = "#A1887F"
VERT = "#2E7D32"
OR = "#C8A415"
ROUGE = "#C62828"

COULEURS_GRADE = {
    "Grade A": VERT,
    "Grade B": OR,
    "Grade C": OCRE,
    "Hors grade": ROUGE,
}

HUMIDITE_NORME_EXPORT = 8.0

plt.rcParams.update(
    {
        "figure.facecolor": "#FAFAF7",
        "axes.facecolor": "#FFFFFF",
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.axisbelow": True,      # la grille passe derriere les barres
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def numeriser(df: pd.DataFrame, colonnes: list[str]) -> pd.DataFrame:
    """
    Convertit en flottants les colonnes issues du SQL.

    PostgreSQL renvoie les colonnes NUMERIC sous forme d'objets Decimal.
    Pandas les stocke alors en type object, et Matplotlib refuse de les
    tracer. La conversion est sans effet quand les donnees viennent deja de
    DuckDB, qui renvoie des flottants.
    """
    df = df.copy()
    for colonne in colonnes:
        if colonne in df.columns:
            df[colonne] = pd.to_numeric(df[colonne], errors="coerce")
    return df


def annoter_barres(ax, barres, valeurs, format_texte="{:.0f}", decalage=0.01,
                   horizontal=False) -> None:
    """
    Ecrit la valeur au bout de chaque barre.

    Un graphique doit se lire sans que l'oeil ait a estimer une hauteur sur
    l'axe : c'est un des criteres de lisibilite du bareme.
    """
    for barre, valeur in zip(barres, valeurs):
        if horizontal:
            largeur = barre.get_width()
            ax.text(
                largeur * (1 + decalage), barre.get_y() + barre.get_height() / 2,
                format_texte.format(valeur), va="center", fontsize=8,
            )
        else:
            hauteur = barre.get_height()
            ax.text(
                barre.get_x() + barre.get_width() / 2, hauteur * (1 + decalage),
                format_texte.format(valeur), ha="center", fontsize=8,
            )


# ---------------------------------------------------------------------------
# Graphique 1 : production par region
# ---------------------------------------------------------------------------
def graphique_production_region(ax) -> None:
    """Cartographie des regions en barres, demandee explicitement par le sujet."""
    df = numeriser(
        charger_etape("resultat_production_region"),
        ["tonnes", "part_nationale_pct", "prix_moyen_pondere"],
    ).sort_values("tonnes")

    barres = ax.barh(df["nom_region"], df["tonnes"], color=OCRE, alpha=0.9)

    # Les regions du Sud-Ouest, qui exportent par San Pedro, sont distinguees :
    # c'est la zone de production historique du cacao ivoirien.
    for barre, port in zip(barres, df["port_export"]):
        if port == "San Pedro":
            barre.set_color(BRUN_FONCE)

    annoter_barres(
        ax, barres, df["part_nationale_pct"], format_texte="{:.1f} %", horizontal=True
    )

    ax.set_title("Production par region (2 campagnes)")
    ax.set_xlabel("Tonnes collectees")
    ax.set_xlim(0, df["tonnes"].max() * 1.15)
    ax.legend(
        handles=[
            plt.Rectangle((0, 0), 1, 1, color=BRUN_FONCE),
            plt.Rectangle((0, 0), 1, 1, color=OCRE),
        ],
        labels=["Export via San Pedro", "Export via Abidjan"],
        fontsize=8,
        loc="lower right",
    )


# ---------------------------------------------------------------------------
# Graphique 2 : saisonnalite
# ---------------------------------------------------------------------------
def graphique_saisonnalite(ax) -> None:
    """
    Tonnage par mois de campagne, une serie par saison.

    L'axe suit l'ordre de la campagne, d'octobre a septembre, grace a la
    colonne mois_campagne de dim_date. Un axe en mois calendaire couperait la
    campagne principale en deux.
    """
    df = numeriser(charger_etape("resultat_saisonnalite"), ["tonnes", "mois_campagne"])

    pivot = df.pivot_table(
        index="mois_campagne", columns="saison", values="tonnes", aggfunc="sum"
    ).sort_index()
    etiquettes = (
        df.drop_duplicates("mois_campagne")
        .set_index("mois_campagne")["nom_mois"]
        .sort_index()
    )

    positions = np.arange(len(pivot))
    largeur = 0.38
    couleurs = [BRUN_FONCE, OCRE]

    for decalage, (saison, couleur) in enumerate(zip(pivot.columns, couleurs)):
        ax.bar(
            positions + (decalage - 0.5) * largeur,
            pivot[saison],
            width=largeur,
            label=saison,
            color=couleur,
            alpha=0.9,
        )

    # La campagne principale court des positions 0 a 5 (octobre a mars)
    ax.axvspan(-0.5, 5.5, color=VERT, alpha=0.07)
    # On agrandit d'abord l'axe, sinon le libelle chevauche la barre la plus haute
    ax.set_ylim(0, float(np.nanmax(pivot.to_numpy())) * 1.22)
    # Le libelle est place au-dessus des barres, dans l'espace libere ci-dessus
    ax.text(2.5, ax.get_ylim()[1] * 0.96, "Campagne principale",
            ha="center", va="top", fontsize=9, color=VERT, fontweight="bold")

    ax.set_title("Saisonnalite des apports")
    ax.set_ylabel("Tonnes")
    ax.set_xticks(positions)
    ax.set_xticklabels(etiquettes.values, rotation=45, ha="right", fontsize=8)
    ax.legend(fontsize=8)


# ---------------------------------------------------------------------------
# Graphique 3 : evolution des prix
# ---------------------------------------------------------------------------
def graphique_evolution_prix(ax) -> None:
    """
    Prix moyen pondere au fil de la campagne, une courbe par saison.

    Le prix affiche est SUM(montant) / SUM(tonnage), et non la moyenne des
    prix unitaires : une pesee de 12 kg ne doit pas peser autant qu'une pesee
    de 2 000 kg.
    """
    df = numeriser(
        charger_etape("resultat_saisonnalite"),
        ["prix_moyen_pondere", "part_grade_a_pct", "mois_campagne"],
    )

    etiquettes = (
        df.drop_duplicates("mois_campagne")
        .set_index("mois_campagne")["nom_mois"]
        .sort_index()
    )

    for saison, couleur in zip(sorted(df["saison"].unique()), [BRUN_FONCE, OCRE]):
        serie = df[df["saison"] == saison].sort_values("mois_campagne")
        ax.plot(
            serie["mois_campagne"], serie["prix_moyen_pondere"],
            marker="o", markersize=5, linewidth=2, color=couleur, label=saison,
        )

    ax.axvspan(0.5, 6.5, color=VERT, alpha=0.07)
    ax.set_title("Evolution du prix moyen pondere")
    ax.set_ylabel("FCFA par kg")
    ax.set_xticks(sorted(df["mois_campagne"].unique()))
    ax.set_xticklabels(etiquettes.values, rotation=45, ha="right", fontsize=8)
    ax.legend(fontsize=8)


# ---------------------------------------------------------------------------
# Graphique 4 : distribution des qualites
# ---------------------------------------------------------------------------
def graphique_qualites(ax) -> None:
    """
    Tonnage par grade, avec le taux de conformite a la norme d'exportation.

    Deux informations sur un seul graphique : combien pese chaque grade, et
    quelle part respecte le seuil de 8 % d'humidite.
    """
    df = numeriser(
        charger_etape("resultat_prix_qualite"),
        ["tonnes", "conforme_export_pct", "prix_moyen_pondere", "rang"],
    ).sort_values("rang")

    couleurs = [COULEURS_GRADE.get(g, OCRE) for g in df["nom_qualite"]]
    barres = ax.bar(df["nom_qualite"], df["tonnes"], color=couleurs, alpha=0.9)

    for barre, conforme, prix in zip(
        barres, df["conforme_export_pct"], df["prix_moyen_pondere"]
    ):
        ax.text(
            barre.get_x() + barre.get_width() / 2, barre.get_height() * 1.02,
            f"{conforme:.0f} % conformes\n{prix:.0f} FCFA/kg",
            ha="center", fontsize=8,
        )

    ax.set_title("Distribution des qualites et conformite export")
    ax.set_ylabel("Tonnes")
    ax.set_ylim(0, df["tonnes"].max() * 1.25)
    ax.tick_params(axis="x", labelsize=9)


# ---------------------------------------------------------------------------
# Graphique 5 : prime bio
# ---------------------------------------------------------------------------
def graphique_prime_bio(ax) -> None:
    """Prix moyen pondere selon la certification, a grade egal."""
    df = numeriser(charger_etape("resultat_prime_bio"), ["prix_moyen_pondere", "tonnes"])

    # certifie_bio peut arriver en booleen ou en texte selon la source
    df["certifie_bio"] = df["certifie_bio"].astype(str).str.lower().isin(["true", "1", "oui"])

    pivot = df.pivot_table(
        index="nom_qualite", columns="certifie_bio", values="prix_moyen_pondere"
    )
    ordre = ["Grade A", "Grade B", "Grade C", "Hors grade"]
    pivot = pivot.reindex([g for g in ordre if g in pivot.index])

    positions = np.arange(len(pivot))
    largeur = 0.38

    b1 = ax.bar(positions - largeur / 2, pivot[False], largeur,
                label="Conventionnel", color=OCRE, alpha=0.9)
    b2 = ax.bar(positions + largeur / 2, pivot[True], largeur,
                label="Certifie bio", color=VERT, alpha=0.9)

    # La prime en pourcentage est ce qui interesse le planteur
    for position, (sans, avec) in enumerate(zip(pivot[False], pivot[True])):
        prime = (avec / sans - 1) * 100
        ax.text(position, max(sans, avec) * 1.03, f"+{prime:.0f} %",
                ha="center", fontsize=9, fontweight="bold", color=VERT)

    annoter_barres(ax, list(b1) + list(b2), list(pivot[False]) + list(pivot[True]),
                   format_texte="{:.0f}", decalage=-0.14)

    ax.set_title("Effet de la certification bio, a grade egal")
    ax.set_ylabel("FCFA par kg")
    ax.set_xticks(positions)
    ax.set_xticklabels(pivot.index, fontsize=9)
    ax.set_ylim(0, pivot.max().max() * 1.2)
    ax.legend(fontsize=8)


# ---------------------------------------------------------------------------
# Graphique 6 : classement des cooperatives
# ---------------------------------------------------------------------------
def graphique_cooperatives(ax) -> None:
    """Top 10 des cooperatives par tonnage, la couleur indiquant la part bio."""
    df = numeriser(
        charger_etape("resultat_cooperatives"),
        ["tonnes", "part_bio_pct", "kg_par_planteur"],
    ).head(10).sort_values("tonnes")

    # Degrade du clair (peu de bio) au vert fonce (beaucoup de bio).
    # L'echelle est calee sur les valeurs observees, et non sur un maximum
    # arbitraire : sinon toutes les cooperatives peu certifiees recevraient
    # la meme teinte et le degrade n'apprendrait plus rien.
    parts = df["part_bio_pct"]
    etendue = max(parts.max() - parts.min(), 1e-9)
    palette = plt.cm.YlGn(0.25 + 0.7 * (parts - parts.min()) / etendue)
    barres = ax.barh(df["code_cooperative"], df["tonnes"], color=palette)

    annoter_barres(ax, barres, df["part_bio_pct"],
                   format_texte="{:.0f} % bio", horizontal=True)

    ax.set_title("Top 10 des cooperatives collectrices")
    ax.set_xlabel("Tonnes collectees")
    ax.set_xlim(0, df["tonnes"].max() * 1.2)
    ax.tick_params(axis="y", labelsize=8)


# ---------------------------------------------------------------------------
# Assemblage
# ---------------------------------------------------------------------------
GRAPHIQUES = [
    ("g1_production_region", "Production par region", graphique_production_region),
    ("g2_saisonnalite", "Saisonnalite des apports", graphique_saisonnalite),
    ("g3_evolution_prix", "Evolution des prix", graphique_evolution_prix),
    ("g4_qualites", "Distribution des qualites", graphique_qualites),
    ("g5_prime_bio", "Prime a la certification bio", graphique_prime_bio),
    ("g6_cooperatives", "Classement des cooperatives", graphique_cooperatives),
]


def construire_planche() -> plt.Figure:
    """Assemble les six graphiques sur une planche 3 lignes x 2 colonnes."""
    figure = plt.figure(figsize=(16, 17))
    figure.suptitle(
        "Filiere cacao ivoirienne : tableau de bord des pesees",
        fontsize=17,
        fontweight="bold",
        y=0.995,
    )

    for position, (_, _, tracer) in enumerate(GRAPHIQUES, start=1):
        tracer(figure.add_subplot(3, 2, position))

    figure.tight_layout(rect=(0, 0.01, 1, 0.985))
    return figure


def exporter_graphiques_individuels() -> None:
    """Enregistre chaque graphique dans son propre fichier."""
    etape("Export des graphiques individuels")

    dossier = DATA_OUTPUT / "graphiques"
    dossier.mkdir(parents=True, exist_ok=True)

    for nom, libelle, tracer in GRAPHIQUES:
        figure, ax = plt.subplots(figsize=(8, 5.5))
        tracer(ax)
        figure.tight_layout()
        chemin = dossier / f"{nom}.png"
        figure.savefig(chemin, dpi=DPI, bbox_inches="tight",
                       facecolor=figure.get_facecolor())
        plt.close(figure)
        console.print(f"  {libelle:<32} {chemin.name} ({taille_lisible(chemin)})")


def main() -> None:
    titre("ETAPE 5 : tableau de bord", "6 graphiques, le sujet en demande 5")

    etape("Construction de la planche complete")
    figure = construire_planche()

    chemin = DATA_OUTPUT / "dashboard_cacao.png"
    figure.savefig(chemin, dpi=DPI, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    ok(f"Planche enregistree : {chemin.name} ({taille_lisible(chemin)}, {DPI} dpi)")

    exporter_graphiques_individuels()


if __name__ == "__main__":
    main()