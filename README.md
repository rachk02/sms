# EL-IMMEKA SMS

Plateforme professionnelle d'envoi de SMS en masse pour le Burkina Faso, en
liaison SMPP directe avec les trois opérateurs nationaux. Les conventions de
code sont dans [`docs/charte.md`](docs/charte.md), le modèle de données dans
[`docs/modele-de-donnees.md`](docs/modele-de-donnees.md), le lot en cours dans
[`docs/lot-1-taches.md`](docs/lot-1-taches.md).

## Démarrer

Prérequis : Docker avec Compose, et [uv](https://docs.astral.sh/uv/).

    cp .env.example .env                   # puis remplacer chaque a-remplacer
    docker compose up -d --wait            # PostgreSQL, Redis, RabbitMQ, MinIO
    uv sync
    uv run python manage.py migrate --database proprietaire
    uv run python manage.py declarer_files

Au premier démarrage, PostgreSQL crée trois rôles (voir
`docker/postgres/init/01-roles.sh`) : le propriétaire des tables, qui passe les
migrations, le rôle applicatif du service web, soumis au cloisonnement par
ligne, et le rôle des workers, qui le contourne. Un quatrième, celui du
back-office des agents, le contourne aussi (`docker/postgres/ajouter-role-backoffice.sh`
le crée sur un volume déjà initialisé).

## Vérifier

    uv run ruff check . && uv run ruff format --check .
    uv run python scripts/verifier_style.py
    uv run pytest

La même chaîne tourne sur GitHub Actions à chaque envoi sur `main`.

## Lancer

    uv run python manage.py runserver                  # service web des clients
    SMS_ESPACE=backoffice SMS_CONNEXION_PAR_DEFAUT=BACKOFFICE         uv run python manage.py runserver 127.0.0.1:8001  # back-office des agents
    uv run python -m workers.ordonnanceur              # relais des événements, partitions
    uv run python -m workers.envoi                     # et accuses, import_contacts, webhook

## Exploiter

- [Sauvegardes et restauration](docs/exploitation/sauvegardes.md)
- [Environnement de recette](docs/exploitation/recette.md)
- [Point de contrôle du lot 2](docs/exploitation/point-de-controle-lot2.md), contre Jasmin et SMPPSim
- Métriques au format Prometheus sur `/metriques`, avec le jeton `METRIQUES_JETON`.
