-- ============================================================
-- REQUETES ANALYTIQUES : FILIERE CACAO IVOIRIENNE
-- A executer dans : Supabase -> SQL Editor
--
-- Fichier genere automatiquement par 06_requetes_sql.py a partir de
-- requetes.py. Ne pas le modifier a la main : les corrections seraient
-- perdues au prochain lancement du script.
-- ============================================================


-- ------------------------------------------------------------
-- REQUETE 1 : PRODUCTION PAR REGION
-- Question metier : Ou se concentre la production, et le prix paye varie-t-il d'une region a l'autre ?
-- ------------------------------------------------------------
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
ORDER BY tonnes DESC;


-- ------------------------------------------------------------
-- REQUETE 2 : PRIX ET CONFORMITE PAR QUALITE
-- Question metier : Combien vaut chaque grade, et quelle part respecte la norme d'exportation ?
-- ------------------------------------------------------------
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
ORDER BY q.rang;


-- ------------------------------------------------------------
-- REQUETE 3 : SAISONNALITE MENSUELLE
-- Question metier : Comment les apports se repartissent-ils dans l'annee, et la qualite suit-elle ?
-- ------------------------------------------------------------
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
ORDER BY d.saison, d.mois_campagne;


-- ------------------------------------------------------------
-- REQUETE 4 : CLASSEMENT DES COOPERATIVES
-- Question metier : Quelles cooperatives collectent le plus, et avec quelle productivite par planteur ?
-- ------------------------------------------------------------
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
LIMIT 15;


-- ------------------------------------------------------------
-- REQUETE 5 (AVANCEE) : CLASSEMENT MENSUEL DES REGIONS
-- Question metier : Quelle region domine chaque mois, et comment son volume evolue-t-il ?
-- ------------------------------------------------------------
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
ORDER BY saison, mois_campagne, rang_mensuel;


-- ------------------------------------------------------------
-- REQUETE 6 : EFFET DE LA CERTIFICATION BIO
-- Question metier : La certification bio se traduit-elle par un meilleur prix a grade egal ?
-- ------------------------------------------------------------
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
ORDER BY q.rang, p.certifie_bio;
