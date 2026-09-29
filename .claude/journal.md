# Journal du projet, EL-IMMEKA SMS

Chaque remarque de l'utilisateur, chaque correction ou suggestion appliquée avec Claude Code
est consignée ici, datée et horodatée à la seconde.

Format : `[AAAA-MM-JJ HH:MM:SS] Type - Description`

Types : `Correction`, `Suggestion`, `Remarque`, `Instruction permanente`, `Suivi`

Règles, reprises de `~/.claude/CLAUDE.md` :

- Une entrée par événement notable : une remarque qui déclenche une action, une correction de
  bug, une suggestion appliquée. Pas de granularité excessive, les commandes intermédiaires
  n'ont pas à figurer ici.
- L'horodatage vient toujours de la commande système au moment de l'écriture, jamais d'une
  estimation.
- Les entrées s'ajoutent à la fin, dans l'ordre chronologique. Rien ne se réécrit.
- Ce journal complète la mémoire persistante, il ne la remplace pas : la mémoire garde les
  faits durables sur le projet, le journal garde l'historique brut, lisible tel quel.

---

[2026-09-29 14:36:08] Instruction permanente - Mise en place du journal de projet et d'un `CLAUDE.md` à la racine, à la demande de l'utilisateur, avec les deux règles de travail : journal daté, et aucune attribution d'outil dans les commits et les pull requests. Constat fait au passage : la charte du dépôt existait déjà sous `EL-IMMEKA-SMS-dossiers/00-CLAUDE.md` et annonce être « lue au début de chaque session », ce qui était faux là où elle se trouvait. Claude Code charge `CLAUDE.md` à la racine, pas un fichier numéroté dans un sous-dossier. Le `CLAUDE.md` racine l'importe désormais, sans la déplacer.
[2026-09-29 14:43:40] Instruction permanente - Initialisation du dépôt git (branche main, remote origin https://github.com/rachk02/sms.git) et création d'un .gitignore pour projet Django. Le dossier docs_of_interest/ est exclu du suivi, ainsi que .venv/, .idea/, __pycache__/, .env et .claude/settings.local.json. Aucun commit ni push à ce stade.
[2026-09-29 14:46:03] Instruction permanente - L'utilisateur demande d'ignorer pour l'instant la charte docs_of_interest/00-CLAUDE.md (import cassé dans CLAUDE.md laissé en l'état), puis de faire le premier commit et le push sur origin/main.
[2026-09-29 14:48:02] Instruction permanente - Aucun emoji et jamais de tiret cadratin (em dash), nulle part : code, commentaires, gabarits, documentation, messages de commit, descriptions de PR, journal. À la place du tiret cadratin : tiret simple, virgule, deux-points ou point-virgule.
[2026-09-29 14:50:39] Instruction permanente - Commit et push automatiques : chaque modification terminée est commitée et poussée sur origin sans demander confirmation, journal compris. Règle ajoutée à CLAUDE.md, section « Commit et push automatiques ».
[2026-09-29 14:55:15] Remarque - Analyse des sept fichiers md de docs_of_interest (charte, plan, architecture, support, revue, modèle de données, lot 1). Relevé : version Django 5.1 hors support, contraintes d'unicité incompatibles avec le partitionnement de journal_message, clés étrangères qui remontent les couches, pièges de la politique RLS, incohérences de nommage entre documents.
[2026-09-29 15:07:15] Instruction permanente - Décisions de l'utilisateur : Django 6.1 sur Python 3.14 (passage à 6.2 LTS à sa sortie), prix et montants en FCFA entiers sans aucun prix à virgule, références vers une couche supérieure en UUID simple sans clé étrangère, charte, modèle de données et lot 1 versionnés dans docs/.
[2026-09-29 15:07:15] Correction - docs/ : charte alignée sur le modèle (credits_registre, credits_compte), politique RLS réécrite (NULLIF, FORCE, rôle distinct, comptes_utilisateur hors RLS), journal_message partitionné sur date_reception avec clé composée, unicités déplacées dans journal_destinataire et api_idempotence, réservation par message, motifs de rejet KYC en liste fermée, client_id sur les tables filles, tables support et modèles de courriel ajoutées, lot 1 complété (T-05 avant T-02, T-19 Jasmin, critères T-04, T-06, T-07).
[2026-09-29 15:12:50] Suivi - Implémentation mise en attente : l'utilisateur doit fournir d'autres fichiers avant le démarrage de T-01.
[2026-09-29 15:22:00] Remarque - L'utilisateur a fourni les découpages des lots 2 à 10 (07 à 15 dans docs_of_interest). Analyse faite.
[2026-09-29 15:22:00] Correction - Retrait de la tâche T-19 ajoutée au lot 1 (doublon du déploiement de Jasmin prévu au lot 2 en T-22 et T-23, et collision avec la numérotation du lot 2). Réservation de crédit revue : une réservation par campagne entière ou par envoi unitaire, pour que T-09 et T-79 restent vrais. Index (numero_normalise, date_reception) ajouté sur journal_message pour le rapprochement des STOP.
[2026-09-29 15:26:39] Suivi - Copies périmées 05 et 06 supprimées de docs_of_interest, lots 2 à 10 versionnés dans docs/, CLAUDE.md mis à jour. Démarrage de T-01 validé par l'utilisateur.
[2026-09-29 15:35:23] Correction - lot-1-taches.md : AUTH_USER_MODEL retiré de T-01 et rattaché à T-05, sinon manage.py check échoue tant que l'application comptes n'existe pas.
[2026-09-29 15:35:23] Suivi - T-01 terminée : projet config/ avec réglages base, dev, recette, prod, lecture de l'environnement dans config/env.py, dépendances gérées par uv (Django 6.1.1, psycopg 3.3.6), docker-compose avec PostgreSQL 16, Redis 8, RabbitMQ 4 et MinIO, tous sains. manage.py check sans erreur, 4 tests passent, ruff propre. main.py supprimé.
[2026-09-29 15:35:23] Remarque - MinIO ne publie plus d'image publique (Docker Hub refusé, quay.io exige une authentification). Image Chainguard cgr.dev/chainguard/minio:latest retenue par défaut, remplaçable par MINIO_IMAGE ; elle n'existe qu'en étiquette latest, donc non épinglée.
