"""
Les requetes analytiques du projet, definies une seule fois.

Elles sont importees par le script d'execution, par le notebook de rendu et
par le tableau de bord.

Deux conventions appliquees partout :

1. CAST AVANT ROUND. En PostgreSQL, ROUND(valeur, decimales) n'accepte pas un
   double precision, seulement un numeric. Des qu'une division produit un
   flottant, l'appel echoue avec un message peu explicite. On convertit donc
   explicitement : ROUND((expression)::numeric, 1).

2. MOYENNE PONDEREE POUR LES PRIX. Le prix est une mesure non additive :
   AVG(prix_fcfa_kg) donne le meme poids a une pesee de 12 kg et a une pesee
   de 2 000 kg. Le prix moyen reellement paye est
   SUM(montant_fcfa) / SUM(tonnage_kg).
"""

# ---------------------------------------------------------------------------
# REQUETE 1 : production par region
# Jointure simple entre la table de faits et dim_region.
# Question metier : quelles regions portent la production, et le prix paye
# y varie-t-il ?
# ---------------------------------------------------------------------------
Q1_PRODUCTION_REGION = """
SELECT
    r.nom_region,
    r.zone_production,
    r.port_export,
    COUNT(*)                                        AS nb_pesees,
    ROUND((SUM(f.tonnage_kg) / 1000)::numeric, 1)   AS tonnes,
    ROUND((100.0 * SUM(f.tonnage_kg)
           / SUM(SUM(f.tonnage_kg)) OVER ())::numeric, 1) AS part_nationale_pct,
    ROUND((SUM(f.montant_fcfa) / SUM(f.tonnage_kg))::numeric, 0) AS prix_moyen_pondere,
    ROUND((SUM(f.montant_fcfa) / 1e9)::numeric, 2)  AS valeur_milliards_fcfa
FROM faits_pesees f
JOIN dim_region r ON f.id_region = r.id_region
GROUP BY r.nom_region, r.zone_production, r.port_export
ORDER BY tonnes DESC
"""

# ---------------------------------------------------------------------------
# REQUETE 2 : prix et conformite par qualite
# Jointure avec dim_qualite. Les deux moyennes sont affichees cote a cote
# pour montrer l'ecart entre une moyenne simple et une moyenne ponderee.
# Le seuil d'humidite vient de la dimension, il n'est ecrit nulle part en dur.
# ---------------------------------------------------------------------------
Q2_PRIX_QUALITE = """
SELECT
    q.nom_qualite,
    q.rang,
    q.humidite_max_pct,
    q.exportable,
    COUNT(*)                                        AS nb_pesees,
    ROUND((SUM(f.tonnage_kg) / 1000)::numeric, 1)   AS tonnes,
    ROUND(AVG(f.prix_fcfa_kg)::numeric, 0)          AS prix_moyen_simple,
    ROUND((SUM(f.montant_fcfa) / SUM(f.tonnage_kg))::numeric, 0) AS prix_moyen_pondere,
    ROUND(AVG(f.humidite_pct)::numeric, 2)          AS humidite_moyenne,
    ROUND((100.0 * COUNT(*) FILTER (WHERE f.conforme_export)
           / COUNT(*))::numeric, 1)                 AS conforme_export_pct
FROM faits_pesees f
JOIN dim_qualite q ON f.id_qualite = q.id_qualite
GROUP BY q.nom_qualite, q.rang, q.humidite_max_pct, q.exportable
ORDER BY q.rang
"""

# ---------------------------------------------------------------------------
# REQUETE 3 : saisonnalite mensuelle
# Jointure avec dim_date. Aucun calcul de date dans la requete : la campagne
# et la saison sont des colonnes de la dimension, calculees une fois au
# chargement. C'est tout l'interet d'une dimension calendrier.
# ---------------------------------------------------------------------------
Q3_SAISONNALITE = """
SELECT
    d.saison,
    d.mois_campagne,          -- 1 = octobre, ordre chronologique de la campagne
    d.nom_mois,
    d.campagne,
    COUNT(*)                                        AS nb_pesees,
    ROUND((SUM(f.tonnage_kg) / 1000)::numeric, 1)   AS tonnes,
    ROUND((SUM(f.montant_fcfa) / SUM(f.tonnage_kg))::numeric, 0) AS prix_moyen_pondere,
    ROUND((100.0 * COUNT(*) FILTER (WHERE q.nom_qualite = 'Grade A')
           / COUNT(*))::numeric, 1)                 AS part_grade_a_pct
FROM faits_pesees f
JOIN dim_date d    ON f.id_date    = d.id_date
JOIN dim_qualite q ON f.id_qualite = q.id_qualite
GROUP BY d.saison, d.mois_campagne, d.nom_mois, d.campagne
ORDER BY d.saison, d.mois_campagne
"""

# ---------------------------------------------------------------------------
# REQUETE 4 : classement des cooperatives
# Jointure triple : faits, cooperative, region. La certification bio vient de
# dim_planteur, ce qui oblige une quatrieme jointure : c'est le prix a payer
# pour avoir range l'attribut au bon endroit.
# ---------------------------------------------------------------------------
Q4_COOPERATIVES = """
SELECT
    c.code_cooperative,
    r.nom_region,
    c.nb_planteurs,
    COUNT(*)                                        AS nb_pesees,
    ROUND((SUM(f.tonnage_kg) / 1000)::numeric, 1)   AS tonnes,
    ROUND((SUM(f.tonnage_kg) / c.nb_planteurs)::numeric, 0) AS kg_par_planteur,
    ROUND((SUM(f.montant_fcfa) / SUM(f.tonnage_kg))::numeric, 0) AS prix_moyen_pondere,
    ROUND((100.0 * COUNT(*) FILTER (WHERE p.certifie_bio)
           / COUNT(*))::numeric, 1)                 AS part_bio_pct
FROM faits_pesees f
JOIN dim_cooperative c ON f.id_cooperative = c.id_cooperative
JOIN dim_region r      ON c.id_region      = r.id_region
JOIN dim_planteur p    ON f.id_planteur    = p.id_planteur
GROUP BY c.code_cooperative, r.nom_region, c.nb_planteurs
ORDER BY tonnes DESC
LIMIT 15
"""

# ---------------------------------------------------------------------------
# REQUETE 5 : requete avancee, CTE et fonctions de fenetre
#
# Trois fonctions de fenetre y travaillent ensemble :
#   RANK() OVER (PARTITION BY mois ...)  classe les regions dans chaque mois
#   LAG() OVER (PARTITION BY region ...) va chercher le mois precedent
#   SUM() OVER (PARTITION BY mois)       donne le total du mois, denominateur
#                                        de la part de marche
#
# La CTE nomme le calcul intermediaire : sans elle, il faudrait repeter
# l'agregation dans chaque sous-requete.
# ---------------------------------------------------------------------------
Q5_CLASSEMENT_MENSUEL = """
WITH volume_mensuel AS (
    SELECT
        d.saison,
        d.mois_campagne,
        d.nom_mois,
        r.nom_region,
        SUM(f.tonnage_kg)  AS tonnage,
        SUM(f.montant_fcfa) AS montant
    FROM faits_pesees f
    JOIN dim_date d   ON f.id_date   = d.id_date
    JOIN dim_region r ON f.id_region = r.id_region
    GROUP BY d.saison, d.mois_campagne, d.nom_mois, r.nom_region
)
SELECT
    saison,
    mois_campagne,
    nom_mois,
    nom_region,
    ROUND((tonnage / 1000)::numeric, 1) AS tonnes,

    -- Rang de la region a l'interieur de chaque mois
    RANK() OVER (PARTITION BY saison, mois_campagne ORDER BY tonnage DESC) AS rang_mensuel,

    -- Part du mois portee par la region
    ROUND((100.0 * tonnage
           / SUM(tonnage) OVER (PARTITION BY saison, mois_campagne))::numeric, 1) AS part_du_mois_pct,

    -- Tonnage du mois precedent pour la meme region
    ROUND((LAG(tonnage) OVER (PARTITION BY nom_region ORDER BY saison, mois_campagne)
           / 1000)::numeric, 1) AS tonnes_mois_precedent,

    -- Variation par rapport au mois precedent
    -- NULLIF evite la division par zero quand le mois precedent est vide
    ROUND((100.0 * (tonnage - LAG(tonnage) OVER (PARTITION BY nom_region ORDER BY saison, mois_campagne))
           / NULLIF(LAG(tonnage) OVER (PARTITION BY nom_region ORDER BY saison, mois_campagne), 0)
          )::numeric, 1) AS variation_pct
FROM volume_mensuel
ORDER BY saison, mois_campagne, rang_mensuel
"""

# ---------------------------------------------------------------------------
# REQUETE 6 : prime bio, pour completer l'analyse economique
# ---------------------------------------------------------------------------
Q6_PRIME_BIO = """
SELECT
    q.nom_qualite,
    p.certifie_bio,
    COUNT(*)                                        AS nb_pesees,
    ROUND((SUM(f.tonnage_kg) / 1000)::numeric, 1)   AS tonnes,
    ROUND((SUM(f.montant_fcfa) / SUM(f.tonnage_kg))::numeric, 0) AS prix_moyen_pondere
FROM faits_pesees f
JOIN dim_qualite q  ON f.id_qualite  = q.id_qualite
JOIN dim_planteur p ON f.id_planteur = p.id_planteur
GROUP BY q.nom_qualite, q.rang, p.certifie_bio
ORDER BY q.rang, p.certifie_bio
"""


# Chaque entree : cle technique, requete, titre affiche, lecture metier
REQUETES = {
    "production_region": (
        Q1_PRODUCTION_REGION,
        "Requete 1 : production par region",
        "Ou se concentre la production, et le prix paye varie-t-il d'une region a l'autre ?",
    ),
    "prix_qualite": (
        Q2_PRIX_QUALITE,
        "Requete 2 : prix et conformite par qualite",
        "Combien vaut chaque grade, et quelle part respecte la norme d'exportation ?",
    ),
    "saisonnalite": (
        Q3_SAISONNALITE,
        "Requete 3 : saisonnalite mensuelle",
        "Comment les apports se repartissent-ils dans l'annee, et la qualite suit-elle ?",
    ),
    "cooperatives": (
        Q4_COOPERATIVES,
        "Requete 4 : classement des cooperatives",
        "Quelles cooperatives collectent le plus, et avec quelle productivite par planteur ?",
    ),
    "classement_mensuel": (
        Q5_CLASSEMENT_MENSUEL,
        "Requete 5 (avancee) : classement mensuel des regions",
        "Quelle region domine chaque mois, et comment son volume evolue-t-il ?",
    ),
    "prime_bio": (
        Q6_PRIME_BIO,
        "Requete 6 : effet de la certification bio",
        "La certification bio se traduit-elle par un meilleur prix a grade egal ?",
    ),
}