"""
Pipeline quotidien de la filiere cacao ivoirienne.

Enchainement : generation -> extraction et audit -> transformation ->
controle qualite bloquant -> chargement du schema en etoile -> requetes
analytiques -> tableau de bord -> archivage.

Principe de conception : le DAG n'implemente aucune logique metier. Chaque
tache importe le module de l'etape correspondante et appelle son main(). Le
code qui tourne la nuit est donc exactement celui qui a ete valide en local.
Reimplementer les traitements ici creerait deux versions du pipeline, qui
divergeraient des la premiere correction.

Seule exception, le controle qualite : il applique les regles de maniere
bloquante. Si une regle echoue, la tache leve une exception, les taches
suivantes ne demarrent pas et l'alerte part. Mieux vaut ne rien charger que
charger des donnees fausses.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chemins d'import
#
# Airflow ajoute automatiquement le dossier des DAG au chemin Python, ce qui
# suffit pour callbacks.py. Les scripts d'etape et les modules partages
# (etl_common, qualite, requetes) vivent a la racine du projet : on l'ajoute
# ici, avant tout import de ces modules.
# ---------------------------------------------------------------------------
PROJET_DIR = os.getenv("PIPELINE_CACAO_DIR", "/opt/airflow/projet")
for dossier in (PROJET_DIR, os.path.join(PROJET_DIR, "dags")):
    if dossier not in sys.path:
        sys.path.insert(0, dossier)

# Import place apres la configuration du chemin, donc volontairement pas en
# tete de fichier : sans cela, callbacks.py resterait introuvable si le
# dossier des DAG n'etait pas encore dans sys.path.
from callbacks import (  # noqa: E402
    on_failure_callback,
    on_retry_callback,
    on_success_callback,
)


def executer_etape(module: str, **_context) -> str:
    """
    Importe un script d'etape et execute son main().

    Les fichiers d'etape commencent par un chiffre, ce qui interdit un import
    classique : importlib permet de les charger par leur nom.

    L'import se fait a l'interieur de la fonction, donc au moment de
    l'execution de la tache et non au moment ou Airflow analyse le fichier du
    DAG. Un DAG doit se parser en quelques millisecondes ; importer pandas et
    matplotlib des l'analyse ralentirait l'ordonnanceur entier.
    """
    logger.info("Execution de %s", module)
    importlib.import_module(module).main()
    return module


def controler_qualite_bloquant(**_context) -> dict:
    """
    Applique les regles de validation et arrete le pipeline en cas d'echec.

    La fonction valider() ne leve jamais d'exception : elle renvoie la liste
    complete des problemes. C'est ici que la decision est prise, ce qui permet
    de journaliser tous les defauts d'un coup plutot que de s'arreter au
    premier rencontre.
    """
    from etl_common import charger_etape
    from qualite import valider

    pesees = charger_etape("pesees_propres")
    planteurs = charger_etape("brut_planteurs")
    cooperatives = charger_etape("brut_cooperatives")

    # Seuil adapte au volume reellement extrait, plutot qu'une valeur figee :
    # le pipeline reste valable si le fichier source change de taille.
    seuil = max(1, int(len(pesees) * 0.9))
    resultat = valider(pesees, planteurs=planteurs, cooperatives=cooperatives,
                       seuil_lignes=seuil)

    for controle in resultat.controles:
        logger.info("OK  %s", controle)
    for avertissement in resultat.avertissements:
        logger.warning("!   %s", avertissement)

    if not resultat.est_valide:
        for erreur in resultat.erreurs:
            logger.error("KO  %s", erreur)
        raise ValueError(f"Controle qualite echoue : {resultat.erreurs}")

    logger.info(resultat.resume())
    # La valeur retournee est poussee en XCom et consultable dans l'interface
    return {"nb_lignes": resultat.nb_lignes, "nb_controles": len(resultat.controles)}


# ---------------------------------------------------------------------------
# Arguments appliques a toutes les taches du DAG
# ---------------------------------------------------------------------------
default_args = {
    "owner": "data_engineering",
    "depends_on_past": False,                 # une execution ratee ne bloque pas la suivante
    "start_date": datetime(2024, 1, 1),       # date de premiere execution possible
    "email_on_failure": False,                # les alertes passent par les callbacks
    "email_on_retry": False,
    "retries": 2,                             # 2 tentatives avant echec definitif
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=1),  # tache tuee au-dela d'une heure
    "on_failure_callback": on_failure_callback,
    "on_retry_callback": on_retry_callback,
}

# ---------------------------------------------------------------------------
# Definition du DAG
# ---------------------------------------------------------------------------
with DAG(
    dag_id="pipeline_cacao_ci",
    description="Pipeline quotidien des pesees de cacao, du CSV au tableau de bord",
    default_args=default_args,
    schedule="0 22 * * *",        # cron : tous les jours a 22h00, apres la fermeture des bascules
    catchup=False,                # ne pas rejouer les dates passees au demarrage
    max_active_runs=1,            # une seule execution a la fois : les etapes partagent des fichiers
    tags=["cacao", "etl", "cote-divoire"],
    on_success_callback=on_success_callback,
) as dag:

    # En production, la generation serait remplacee par la collecte des
    # fichiers de bascule des cooperatives. Elle est conservee ici pour que le
    # pipeline soit executable de bout en bout par n'importe qui.
    t1 = PythonOperator(
        task_id="generation_dataset",
        python_callable=executer_etape,
        op_kwargs={"module": "01_generer_dataset"},
    )

    t2 = PythonOperator(
        task_id="extraction_audit",
        python_callable=executer_etape,
        op_kwargs={"module": "02_extraction_audit"},
    )

    t3 = PythonOperator(
        task_id="transformation",
        python_callable=executer_etape,
        op_kwargs={"module": "03_transformation"},
    )

    # Point de passage oblige : rien ne part en base si la qualite n'est pas au rendez-vous
    t4 = PythonOperator(
        task_id="controle_qualite",
        python_callable=controler_qualite_bloquant,
    )

    t5 = PythonOperator(
        task_id="creation_schema",
        python_callable=executer_etape,
        op_kwargs={"module": "04_creer_schema"},
    )

    t6 = PythonOperator(
        task_id="chargement_etoile",
        python_callable=executer_etape,
        op_kwargs={"module": "05_charger_etoile"},
    )

    t7 = PythonOperator(
        task_id="requetes_analytiques",
        python_callable=executer_etape,
        op_kwargs={"module": "06_requetes_sql"},
    )

    t8 = PythonOperator(
        task_id="tableau_de_bord",
        python_callable=executer_etape,
        op_kwargs={"module": "07_dashboard"},
    )

    t9 = BashOperator(
        task_id="archivage",
        # Purge des fichiers intermediaires de plus de 7 jours : ils se
        # regenerent, inutile de les conserver.
        bash_command=(
            f"find {PROJET_DIR}/data/interim -name '*.parquet' -mtime +7 -delete "
            "&& echo 'archivage termine'"
        ),
    )

    # L'operateur >> definit les dependances : t1 avant t2, et ainsi de suite.
    t1 >> t2 >> t3 >> t4 >> t5 >> t6 >> t7 >> t8 >> t9