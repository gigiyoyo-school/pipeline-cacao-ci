-- ============================================================
-- SCHEMA EN ETOILE : FILIERE CACAO IVOIRIENNE
--
-- Convention de nommage :
--   id_xxx    cle technique entiere, generee par PostgreSQL (SERIAL)
--   code_xxx  code metier issu de la source (PLT00042, COOP-DAL-003)
-- La table de faits ne stocke que les cles techniques.
--
-- ============================================================

-- ------------------------------------------------------------
-- DIMENSION 1 : region de production
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_region (
    id_region        SERIAL PRIMARY KEY,
    nom_region       TEXT NOT NULL UNIQUE,     -- Soubre, San Pedro, Daloa...
    zone_production  TEXT,                     -- Sud-Ouest, Centre-Ouest, Est...
    port_export      TEXT,                     -- San Pedro ou Abidjan
    nb_cooperatives  INTEGER,                  -- attribut calcule au chargement
    nb_planteurs     INTEGER
);

-- ------------------------------------------------------------
-- DIMENSION 2 : cooperative
-- Elle porte sa region : ce lien permet de verifier cote base que la
-- region d'une pesee correspond bien a celle de sa cooperative.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_cooperative (
    id_cooperative   SERIAL PRIMARY KEY,
    code_cooperative TEXT NOT NULL UNIQUE,     -- COOP-DAL-003
    nom_cooperative  TEXT,
    id_region        INTEGER NOT NULL REFERENCES dim_region(id_region),
    annee_creation   INTEGER,
    certifiee_bio    BOOLEAN DEFAULT FALSE,
    nb_planteurs     INTEGER
);

-- ------------------------------------------------------------
-- DIMENSION 3 : planteur
-- certifie_bio est un attribut de l'exploitation, pas de la livraison :
-- il vit donc ici et non dans la table de faits.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_planteur (
    id_planteur      SERIAL PRIMARY KEY,
    code_planteur    TEXT NOT NULL UNIQUE,     -- PLT00042
    id_cooperative   INTEGER NOT NULL REFERENCES dim_cooperative(id_cooperative),
    id_region        INTEGER NOT NULL REFERENCES dim_region(id_region),
    superficie_ha    NUMERIC(5,1),
    annee_adhesion   INTEGER,
    certifie_bio     BOOLEAN DEFAULT FALSE
);

-- ------------------------------------------------------------
-- DIMENSION 4 : qualite
-- Le bareme de prix et le seuil d'humidite sont des attributs de la
-- dimension : une requete peut donc comparer le prix paye au prix de
-- reference sans qu'aucun seuil ne soit ecrit en dur dans le SQL.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_qualite (
    id_qualite       SERIAL PRIMARY KEY,
    nom_qualite      TEXT NOT NULL UNIQUE,     -- Grade A, Grade B, Grade C, Hors grade
    rang             INTEGER,                  -- 1 = meilleure qualite
    humidite_max_pct NUMERIC(4,1),
    prix_plancher    INTEGER,
    prix_plafond     INTEGER,
    exportable       BOOLEAN                   -- le Hors grade part sur le marche local
);

-- ------------------------------------------------------------
-- DIMENSION 5 : calendrier
-- La colonne campagne evite d'ecrire un CASE WHEN sur les mois dans
-- chaque requete de saisonnalite.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_date (
    id_date        SERIAL PRIMARY KEY,
    date_complete  DATE NOT NULL UNIQUE,
    annee          INTEGER,
    mois           INTEGER,                    -- 1 a 12
    nom_mois       TEXT,
    trimestre      INTEGER,
    semaine        INTEGER,
    jour           INTEGER,
    nom_jour       TEXT,
    est_weekend    BOOLEAN,
    campagne       TEXT,                       -- Principale (oct-mars) ou Intermediaire
    saison         TEXT                        -- 2022-2023, 2023-2024
);

-- ------------------------------------------------------------
-- TABLE DE FAITS : une ligne = une pesee
-- id_pesee est une dimension degeneree : un identifiant metier conserve
-- dans les faits, sans dimension propre.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS faits_pesees (
    id_pesee             TEXT PRIMARY KEY,

    -- Cles etrangeres vers les cinq dimensions
    id_date              INTEGER NOT NULL REFERENCES dim_date(id_date),
    id_region            INTEGER NOT NULL REFERENCES dim_region(id_region),
    id_cooperative       INTEGER NOT NULL REFERENCES dim_cooperative(id_cooperative),
    id_planteur          INTEGER NOT NULL REFERENCES dim_planteur(id_planteur),
    id_qualite           INTEGER NOT NULL REFERENCES dim_qualite(id_qualite),

    -- Mesures additives : SUM a un sens
    tonnage_kg           NUMERIC(10,1) NOT NULL,
    montant_fcfa         BIGINT NOT NULL,

    -- Mesures non additives : uniquement AVG, ou moyenne ponderee par le tonnage
    prix_fcfa_kg         INTEGER NOT NULL,
    humidite_pct         NUMERIC(4,1),
    ecart_prix_grade_pct NUMERIC(6,1),

    -- Attributs de la pesee
    categorie_tonnage    TEXT,
    conforme_export      BOOLEAN,

    -- Tracabilite du nettoyage : ces deux drapeaux permettent d'ecarter
    -- les valeurs imputees de toute analyse d'un simple filtre
    prix_impute          BOOLEAN DEFAULT FALSE,
    humidite_imputee     BOOLEAN DEFAULT FALSE
);

-- ------------------------------------------------------------
-- INDEX
-- PostgreSQL indexe automatiquement les cles primaires, mais pas les
-- cles etrangeres. Ces index accelerent les JOIN de la table de faits.
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_faits_date        ON faits_pesees(id_date);
CREATE INDEX IF NOT EXISTS idx_faits_region      ON faits_pesees(id_region);
CREATE INDEX IF NOT EXISTS idx_faits_cooperative ON faits_pesees(id_cooperative);
CREATE INDEX IF NOT EXISTS idx_faits_planteur    ON faits_pesees(id_planteur);
CREATE INDEX IF NOT EXISTS idx_faits_qualite     ON faits_pesees(id_qualite);

-- ------------------------------------------------------------
-- Verification : les 6 tables du schema en etoile
-- ------------------------------------------------------------
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('dim_region', 'dim_cooperative', 'dim_planteur',
                     'dim_qualite', 'dim_date', 'faits_pesees')
ORDER BY table_name;