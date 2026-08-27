# ============================================================
# Image du pipeline cacao : Airflow et les dependances du projet
#
# Regle Dockerfile a retenir : un commentaire occupe une ligne entiere.
# Ecrire "ENV CLE=valeur  # explication" ferait entrer le commentaire dans la
# valeur, et un diese place apres un antislash de continuation casse la
# commande RUN en deux.
# ============================================================

# ---- IMAGE DE BASE ----
# On part de l'image officielle Airflow plutot que de python:slim. Elle
# contient deja Airflow, son utilisateur, ses dossiers et son point d'entree.
# Installer Airflow a la main dans une image Python nue echoue presque
# toujours : plus de 600 dependances transitives a concilier.
FROM apache/airflow:2.10.5-python3.11

# ---- METADONNEES ----
LABEL maintainer="etudiant@example.com"
LABEL description="Pipeline ETL de la filiere cacao ivoirienne"
LABEL version="1.0.0"

# ---- VARIABLES D'ENVIRONNEMENT ----
# PYTHONDONTWRITEBYTECODE : ne pas ecrire de fichiers .pyc dans l'image
# PYTHONUNBUFFERED : afficher les logs Python immediatement
# MPLBACKEND : Matplotlib sans fenetre graphique, indispensable en conteneur
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg
ENV PIPELINE_CACAO_DIR=/opt/airflow/projet

# ---- DEPENDANCES SYSTEME ----
# Passage temporaire en root : seul lui peut installer des paquets systeme.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ---- DEPENDANCES PYTHON ----
# Retour a l'utilisateur airflow : pip ne doit jamais tourner en root ici.
USER airflow

# requirements.txt est copie seul et en premier : tant qu'il ne change pas,
# Docker reutilise le cache de cette couche et le build reste rapide.
COPY --chown=airflow:root requirements.txt /tmp/requirements.txt

# Le fichier de contraintes officiel fixe les versions compatibles avec cette
# version d'Airflow, SQLAlchemy en particulier. Sans lui, l'installation
# casse l'environnement Airflow de l'image.
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt"

# ---- CODE DU PROJET ----
# Copie dans l'image pour que le conteneur soit autonome. En developpement,
# docker-compose monte le dossier local par-dessus : editer un script sur la
# machine hote suffit alors, sans reconstruire l'image.
COPY --chown=airflow:root . /opt/airflow/projet

# Airflow definit deja son propre ENTRYPOINT, on ne le remplace pas.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD airflow jobs check --job-type SchedulerJob --hostname "$(hostname)" || exit 1
