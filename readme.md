# Pipeline de données de la filière cacao ivoirienne

Entrepôt de données des pesées de cacao, du fichier de bascule au tableau de bord analytique : nettoyage, modélisation dimensionnelle, SQL analytique, orchestration et conteneurisation.

Projet de fin de module, Data Engineering, Master Data-AI, promotion 2025-2026.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-2.2-150458?logo=pandas&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/Supabase-PostgreSQL%2015-3ECF8E?logo=supabase&logoColor=white)
![Airflow](https://img.shields.io/badge/Airflow-2.10-017CEE?logo=apacheairflow&logoColor=white)
![dbt](https://img.shields.io/badge/dbt-1.8-FF694B?logo=dbt&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9)

---

## Contexte

La Côte d'Ivoire produit environ 45 % du cacao mondial. Le Conseil du Café-Cacao suit les apports des planteurs aux coopératives : chaque livraison donne lieu à une pesée, avec un tonnage, un taux d'humidité, un classement qualité et un prix payé.

Ces données arrivent sous forme de fichiers plats saisis sur les bascules des coopératives, avec les défauts du terrain : capteurs défaillants, prix non saisis, doubles scans, noms tapés à la main. En l'état, aucune analyse consolidée n'est possible.

Ce projet construit le pipeline qui transforme ces fichiers en entrepôt interrogeable, alimenté automatiquement chaque nuit.

## Architecture

```
   pesees_cacao_ci_80k.csv          referentiel_planteurs.csv
   (donnees de bascule)             referentiel_cooperatives.csv
              |                                  |
              +----------------+-----------------+
                               |
                    [1] Extraction et audit  ------> rapport_audit.json
                               |
                    [2] Nettoyage Pandas
                        normalisation des libelles
                        dedoublonnage
                        imputation avec marquage
                               |
                    [3] Enrichissement
                        6 colonnes calculees
                               |
                    [4] Controle qualite  ---------> rapport_qualite.json
                        7 regles bloquantes
                               |
                        echec  |  succes
                        <------+
                               |
                    [5] Schema en etoile (Supabase PostgreSQL)
                        5 dimensions + 1 table de faits
                               |
              +----------------+-----------------+
              |                |                 |
     [6] Requetes SQL    [7] Tableau de bord   [8] Modele dbt
     JOIN, CTE, fenetres  6 graphiques          vue perf_cooperatives

   Orchestration : DAG Airflow quotidien a 22h00, 9 taches enchainees
   Execution     : Docker Compose, 4 services
```

### Schéma en étoile

```
                        dim_date
                           |
        dim_region ---- faits_pesees ---- dim_qualite
                        /         \
           dim_cooperative       dim_planteur
```

La table de faits porte directement les clés de région, coopérative et planteur, malgré la hiérarchie naturelle entre les trois. Cette redondance est assumée : elle permet une jointure unique pour l'analyse régionale, et la cohérence est garantie par une règle de validation automatique du pipeline.

## Stack technique

| Domaine | Outil | Rôle |
|---|---|---|
| Gestion du projet | uv | dépendances et environnement virtuel |
| Transformation | Python 3.12, Pandas 2.2 | ETL, nettoyage, enrichissement |
| Stockage analytique | Supabase (PostgreSQL 15) | schéma en étoile |
| Format Data Lake | Parquet (Snappy) | étapes intermédiaires |
| SQL en local | DuckDB | mise au point des requêtes sans connexion |
| Modèles analytiques | dbt 1.8 | vue `perf_cooperatives` et tests |
| Orchestration | Apache Airflow 2.10 | DAG quotidien, alertes, reprises |
| Conteneurisation | Docker, Docker Compose | environnement reproductible |
| Visualisation | Matplotlib | tableau de bord 6 graphiques |
| Affichage console | rich | sorties lisibles des scripts |

## Résultats clés

| Indicateur | Valeur |
|---|---|
| Pesées traitées | 80 000 extraites, 78 800 conservées |
| Tonnage collecté | 24 345 tonnes sur 2 campagnes |
| Valeur d'achat | 20,73 milliards de FCFA |
| Prix moyen pondéré | 851 FCFA/kg |
| Conformité à la norme d'exportation | 70,5 % des pesées |
| Règles de qualité | 7 règles, 100 % passées |

### Trois constats métier

**Le Grade C est massivement hors norme.** 13,3 % seulement respecte le seuil d'humidité de 8 %, contre 99,8 % pour le Grade A. Environ 4 300 tonnes sont invendables à l'export en l'état. L'écart de prix avec le Grade B étant de 155 FCFA/kg, un programme de séchage ciblé a une valeur chiffrable.

**La logistique se concentre sur un seul port.** Les régions exportant via San Pedro pèsent près de 62 % du tonnage. Une saturation de ce port bloquerait la majorité de la filière.

**La certification bio paie environ 10 %.** À grade égal, 1 101 contre 1 003 FCFA/kg sur le Grade A. C'est le levier de revenu le plus direct pour un planteur, et il ne dépend pas de la qualité du séchage.

## Installation

Prérequis : Python 3.12, [uv](https://docs.astral.sh/uv/), un compte Supabase gratuit, et Docker pour la partie orchestration.

```bash
git clone https://github.com/gigiyoyo-school/pipeline-cacao-ci.git
cd pipeline-cacao-ci

uv python pin 3.12
uv sync --all-groups

cp .env.example .env      # puis renseigner SUPABASE_URL et les variables DBT_*
```

Créer ensuite les tables dans Supabase : SQL Editor, puis coller le contenu de `sql/01_creer_schema_etoile.sql`.

## Exécution

### Pipeline complet, étape par étape

```bash
uv run 01_generer_dataset.py       # genere les 3 fichiers sources
uv run 02_extraction_audit.py      # audit initial, sans modification
uv run 03_transformation.py        # nettoyage, enrichissement, qualite
uv run 04_creer_schema.py          # schema en etoile dans Supabase
uv run 05_charger_etoile.py        # peuplement des dimensions et des faits
uv run 06_requetes_sql.py          # 6 requetes analytiques
uv run 07_dashboard.py             # tableau de bord PNG
uv run 08_dbt_run.py               # modele dbt et ses tests
```

### Sans connexion à Supabase

```bash
uv run 05_charger_etoile.py --dry-run   # construit et valide les tables en local
uv run 06_requetes_sql.py --local       # meme SQL, execute par DuckDB
```

### Orchestration avec Airflow

```bash
mkdir -p logs
docker compose up -d
```

Interface sur `http://localhost:8080`, identifiants `admin` / `admin`. Activer le DAG `pipeline_cacao_ci`, puis le déclencher.

### Notebook

`notebook_projet_cacao.ipynb` reprend le pipeline complet, commenté. Il est autonome : il génère ses propres données et bascule sur DuckDB si aucune chaîne Supabase n'est configurée. `Run all` se termine sans erreur dans les deux cas.

## Structure du dépôt

```
pipeline-cacao-ci/
├── 01_generer_dataset.py .. 08_dbt_run.py   scripts du pipeline, un par etape
├── etl_common.py                            console, chemins, connexion, helpers
├── qualite.py                               7 regles de validation, anomalies
├── requetes.py                              les 6 requetes analytiques
├── notebook_projet_cacao.ipynb              notebook de rendu
├── journal.md                               journal de bord du projet
├── dags/
│   ├── dag_pipeline_cacao.py                DAG Airflow, 9 taches
│   └── callbacks.py                         alertes en cas d'echec
├── dbt_projet/
│   ├── dbt_project.yml, profiles.yml
│   └── models/analytics/                    modele SQL et tests
├── sql/
│   ├── 01_creer_schema_etoile.sql           DDL des 6 tables
│   └── 02_requetes_analytiques.sql          requetes pour le SQL Editor
├── data/
│   ├── raw/                                 fichiers sources (non versionnes)
│   ├── interim/                             etapes intermediaires en Parquet
│   └── output/                              dashboard, rapports JSON
├── Dockerfile, docker-compose.yml, requirements.txt
└── pyproject.toml, uv.lock
```

Les données brutes ne sont pas versionnées : elles se régénèrent avec `01_generer_dataset.py`, à graine fixe, donc à l'identique.

## Choix techniques notables

**Le générateur fourni a été corrigé avant usage.** Son audit a révélé des dates s'étalant jusqu'en 2041, un prix sans lien avec la qualité, et une hiérarchie région-coopérative incohérente. Ces défauts rendaient impossibles trois des analyses demandées.

**Imputation plutôt que suppression.** Une pesée dont le prix n'a pas été saisi reste une pesée dont le tonnage est valide. Les valeurs imputées sont marquées par deux colonnes booléennes, ce qui permet de les écarter d'un simple filtre.

**Moyenne pondérée pour les prix.** Le prix est une mesure non additive : `AVG(prix)` donnerait le même poids à une pesée de 12 kg et à une pesée de 2 tonnes. Le prix réellement payé est `SUM(montant) / SUM(tonnage)`.

**Le DAG n'implémente aucune logique métier.** Chaque tâche importe le script de l'étape correspondante et appelle son `main()`. Le code qui tourne la nuit est exactement celui validé en local.

## Limites connues

Les données sont synthétiques : les ordres de grandeur sont calés sur la filière réelle, mais ne reflètent pas la production nationale, qui dépasse 2 millions de tonnes par an. Les deux campagnes se ressemblent beaucoup, là où des aléas climatiques créeraient des écarts. L'imputation de l'humidité par la médiane du grade est circulaire, puisque c'est l'humidité qui détermine le grade.

## Auteur

Projet réalisé individuellement dans le cadre du module Data Engineering.

Outils d'IA utilisés : Claude (Anthropic), pour la relecture du code, l'aide au débogage et la structuration du projet. Le code a été testé, adapté et compris au préalable.