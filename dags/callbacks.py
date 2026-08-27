"""
Fonctions d'alerte appelees automatiquement par Airflow.

Airflow passe a ces fonctions un dictionnaire `context` decrivant l'execution
en cours : le DAG, la tache, l'identifiant de run, l'exception levee. On s'en
sert pour produire un message lisible dans les logs.

En production, le logging serait remplace par un envoi Slack, Teams ou e-mail :
le code est prepare pour, il suffit de renseigner l'URL du webhook.
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _formater_alerte(context: dict) -> str:
    """Construit le message d'alerte a partir du contexte Airflow."""
    dag_id = context["dag"].dag_id
    tache = context["task_instance"].task_id
    run_id = context.get("run_id", "inconnu")
    erreur = str(context.get("exception", "non precisee"))
    heure = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Message construit ligne par ligne : pas de chaine multiligne imbriquee,
    # qui poserait probleme si ce fichier etait genere depuis une autre chaine.
    lignes = [
        "ALERTE PIPELINE CACAO",
        f"  DAG    : {dag_id}",
        f"  Tache  : {tache}",
        f"  Run    : {run_id}",
        f"  Heure  : {heure}",
        f"  Erreur : {erreur}",
        "  Action : consulter les logs Airflow de la tache concernee",
    ]
    return "\n".join(lignes)


def on_failure_callback(context: dict) -> None:
    """Appelee quand une tache echoue definitivement, apres toutes ses tentatives."""
    logger.error(_formater_alerte(context))

    # En production, decommenter et renseigner le webhook :
    # import requests
    # requests.post(SLACK_WEBHOOK_URL, json={"text": _formater_alerte(context)}, timeout=10)


def on_retry_callback(context: dict) -> None:
    """Appelee a chaque nouvelle tentative, avant l'echec definitif."""
    tache = context["task_instance"].task_id
    tentative = context["task_instance"].try_number
    logger.warning("Nouvelle tentative %s pour la tache %s", tentative, tache)


def on_success_callback(context: dict) -> None:
    """Appelee quand le DAG entier se termine correctement."""
    logger.info("SUCCES : le DAG %s s'est termine sans erreur", context["dag"].dag_id)