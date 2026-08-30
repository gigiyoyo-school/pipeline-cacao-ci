-- models/analytics/perf_cooperatives.sql
--
-- Un modele dbt est un simple SELECT. Pas de CREATE VIEW, pas d'INSERT :
-- dbt cree l'objet, le nomme d'apres le fichier et le rafraichit a chaque
-- `dbt run`. Ce fichier produit donc la vue public.perf_cooperatives.
--
-- source('cacao', 'faits_pesees') plutot que le nom brut de la table : dbt
-- connait ainsi les dependances du modele et peut tester les tables amont.
--
-- Question metier : quelles cooperatives collectent le plus, avec quelle
-- qualite, et ou se situent-elles dans leur region ?

WITH pesees AS (
    SELECT
        f.id_cooperative,
        f.tonnage_kg,
        f.montant_fcfa,
        f.conforme_export,
        p.certifie_bio,
        q.nom_qualite
    FROM {{ source('cacao', 'faits_pesees') }} f
    JOIN {{ source('cacao', 'dim_planteur') }} p ON f.id_planteur = p.id_planteur
    JOIN {{ source('cacao', 'dim_qualite') }} q  ON f.id_qualite  = q.id_qualite
)

SELECT
    c.code_cooperative,
    c.nom_cooperative,
    r.nom_region,
    r.zone_production,
    r.port_export,
    c.nb_planteurs,

    COUNT(*)                                          AS nb_pesees,
    ROUND((SUM(pe.tonnage_kg) / 1000)::numeric, 1)    AS tonnes,
    ROUND((SUM(pe.tonnage_kg) / c.nb_planteurs)::numeric, 0) AS kg_par_planteur,

    -- Prix moyen reellement paye : le prix est une mesure non additive,
    -- AVG(prix) donnerait le meme poids a une pesee de 12 kg et a une de 2 tonnes
    ROUND((SUM(pe.montant_fcfa) / SUM(pe.tonnage_kg))::numeric, 0) AS prix_moyen_pondere,

    ROUND((100.0 * COUNT(*) FILTER (WHERE pe.nom_qualite = 'Grade A')
           / COUNT(*))::numeric, 1)                   AS part_grade_a_pct,
    ROUND((100.0 * COUNT(*) FILTER (WHERE pe.conforme_export)
           / COUNT(*))::numeric, 1)                   AS part_conforme_export_pct,
    ROUND((100.0 * COUNT(*) FILTER (WHERE pe.certifie_bio)
           / COUNT(*))::numeric, 1)                   AS part_bio_pct,

    -- Rang de la cooperative a l'interieur de sa region
    RANK() OVER (PARTITION BY r.nom_region ORDER BY SUM(pe.tonnage_kg) DESC)
                                                      AS rang_regional,
    -- Rang national
    RANK() OVER (ORDER BY SUM(pe.tonnage_kg) DESC)    AS rang_national

FROM pesees pe
JOIN {{ source('cacao', 'dim_cooperative') }} c ON pe.id_cooperative = c.id_cooperative
JOIN {{ source('cacao', 'dim_region') }} r      ON c.id_region       = r.id_region
GROUP BY
    c.code_cooperative, c.nom_cooperative, c.nb_planteurs,
    r.nom_region, r.zone_production, r.port_export
ORDER BY tonnes DESC