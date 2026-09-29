# Lot 1, socle technique, découpé en tâches

Le lot 1 couvre les fiches F-01, F-02, F-03, F-04 et F-42, soit 26 à 42
jours-homme. Il est ici découpé en dix-huit tâches d'une demi-journée à deux
jours, chacune avec ce qu'elle produit et ce qui doit être vrai pour la
déclarer finie.

Une tâche se prend dans l'ordre. Une tâche n'est finie que si ses critères
d'acceptation passent, pas si le code semble écrit.

## T-01 · Environnement local qui démarre

**Produit** `docker-compose.yml` fonctionnel, `.env` renseigné, dépendances
installées, `python manage.py check` qui passe.

**Critères** `docker compose up -d` démarre PostgreSQL, Redis, RabbitMQ et
MinIO, et les quatre répondent à leur contrôle de santé. `python manage.py
check` ne renvoie aucune erreur. `pytest` s'exécute et collecte les tests du
socle.

## T-02 · Modèles abstraits et middleware de cloisonnement

**Produit** `apps/core/models.py` avec `Horodate`, `Identifiable`, `Cloisonne`,
`Base` et `BaseClient`. `apps/core/middleware.py` posant `app.client`.

**Critères** Un test vérifie que le middleware pose bien le réglage sur la
connexion pour un utilisateur authentifié, et le vide pour un anonyme. Le
réglage est local à la transaction, ce qui se vérifie en ouvrant deux
connexions.

## T-03 · Normalisation des numéros

**Produit** `apps/core/numeros.py`, déjà esquissé, complété et testé.

**Critères** Les formes 70123456, 22670123456, +22670123456, 0022670123456 et
les mêmes avec espaces, points et tirets donnent toutes `+22670123456`. Une
chaîne vide, une chaîne non numérique, un numéro trop court et un numéro trop
long lèvent `NumeroInvalide` avec un message qui nomme la cause.

## T-04 · Encodage GSM et décompte des segments

**Produit** `apps/core/encodage.py`, complété et testé.

**Critères** Un texte de 160 caractères GSM donne un segment, 161 en donnent
deux de 153. Un texte contenant un e accent circonflexe bascule en UCS-2 et
tombe à 70 caractères. Un euro compte pour deux unités. Les accents de la table
GSM, é è à ù ì ò ä ö ü ñ, ne font pas basculer en UCS-2 : c'est le test qui
rattrape l'erreur présente dans le cahier des charges.

## T-05 · Modèle Client et Utilisateur

**Produit** `apps/comptes/models.py` avec `Client` et `Utilisateur`, ce dernier
remplaçant le modèle utilisateur de Django, avec le courriel comme identifiant.

**Critères** La migration passe. `createsuperuser` fonctionne avec un courriel
et sans nom d'utilisateur. Deux utilisateurs ne peuvent pas partager un
courriel. Le condensat de mot de passe est produit par Argon2id, ce qu'un test
vérifie en inspectant son préfixe.

## T-06 · Politiques de cloisonnement par ligne

**Produit** Une migration dédiée dans `apps/core/migrations/`, séparée de toute
migration de schéma, activant la sécurité par ligne sur les tables métier et
posant la politique `USING (client_id = current_setting('app.client')::uuid)`.

**Critères** Un test crée deux clients avec des données, pose `app.client` sur
le premier, et vérifie qu'aucune ligne du second n'est visible. Un second test
vérifie qu'une requête sans client posé voit zéro ligne, et non toutes les
lignes. C'est le test le plus important du lot.

## T-07 · Partitionnement du journal

**Produit** `apps/journal/models.py` avec `Message` et `Evenement`, et la
migration qui crée le partitionnement mensuel par plage sur `date_soumission`,
plus la fonction de création des partitions à venir.

**Critères** Trois partitions existent après migration, celle du mois courant
et les deux suivantes. Une insertion datée du mois prochain atterrit dans la
bonne partition, vérifié par une requête sur `pg_class`. Une requête bornée sur
un mois ne lit qu'une partition, vérifié par un plan d'exécution.

## T-08 · Registre de crédits

**Produit** `apps/credits/models.py` et `services.py` avec les écritures
d'achat, de consommation, de recrédit et d'ajustement, la table de réservation
et le cache de solde.

**Critères** Aucune méthode ne modifie ni ne supprime une ligne du registre,
ce qu'un test vérifie en tentant les deux. Le solde recalculé depuis les
écritures est égal au cache après une série d'opérations. Un ajustement sans
motif est refusé.

## T-09 · Réservation sous concurrence

**Produit** La réservation avec verrou `SELECT ... FOR UPDATE` sur la ligne de
cache, et la libération en cas d'échec.

**Critères** Un test lance deux réservations simultanées dans deux
transactions sur un solde qui n'en couvre qu'une : l'une réussit, l'autre est
refusée, et le solde final est correct. Sans ce test, la tâche n'est pas finie.

## T-10 · Bus d'événements

**Produit** `apps/core/evenements.py` complété, publication vers RabbitMQ,
consommation idempotente.

**Critères** Un événement publié deux fois avec la même clé d'idempotence n'est
traité qu'une fois. Un consommateur qui échoue après traitement et avant
acquittement ne produit pas de doublon au rejeu.

## T-11 · Files et échange

**Produit** La déclaration de l'échange `sms`, des douze files et de l'échange
de rebut, avec les paliers de réessai.

**Critères** Les files existent après démarrage. Un message qui échoue cinq
fois atterrit dans `rebut` et non dans une boucle. Les délais entre tentatives
suivent la progression prévue.

## T-12 · Squelette des workers

**Produit** Les cinq points d'entrée sous `workers/`, chargeant Django,
consommant leur file, traitant l'arrêt propre.

**Critères** Un worker reçoit le signal d'arrêt, termine le message en cours,
l'acquitte et sort avant le délai de grâce. Un test d'intégration le vérifie
avec un message artificiellement lent.

## T-13 · Connecteur simulé

**Produit** Un adaptateur de test dans `apps/telecom/` qui accepte une
soumission, rend un identifiant, et produit un accusé après un délai réglable,
avec un taux d'échec paramétrable.

**Critères** Une soumission traverse la couche d'abstraction, reçoit un
identifiant, et l'accusé remonte et change l'état du message. Le taux d'échec
configuré se retrouve dans les états finaux sur mille messages.

## T-14 · Corrélation des accusés

**Produit** La correspondance en Redis avec 72 heures de validité, la reprise
en base, et la table des accusés orphelins.

**Critères** Un accusé arrivé après expiration de la clé Redis est retrouvé en
base. Un accusé sans correspondance atterrit dans la table des orphelins et
n'est pas jeté. Un accusé rejoué deux fois ne produit qu'une transition d'état.

## T-15 · Intégration continue

**Produit** Le fichier de chaîne d'intégration exécutant `ruff`, `pytest` et la
construction de l'image.

**Critères** Un envoi sur la branche principale déclenche la chaîne. Un test
qui échoue fait échouer la chaîne. L'image se construit et démarre.

## T-16 · Journaux et métriques

**Produit** Journalisation structurée portant l'identifiant de requête, le
client et l'utilisateur sur chaque ligne. Exposition des métriques
d'exploitation : débit d'envoi, profondeur des files, état des liaisons.

**Critères** Une requête produit des lignes de journal corrélées par un même
identifiant. Les métriques sont lisibles sur un point d'entrée dédié, protégé.

## T-17 · Sauvegardes et restauration

**Produit** Le script de sauvegarde quotidienne de PostgreSQL et la procédure
de restauration écrite.

**Critères** Une restauration a été effectuée pour de bon sur un environnement
de recette, et la base restaurée passe les tests. Une sauvegarde non testée ne
compte pas.

## T-18 · Environnement de recette et jeu d'essai

**Produit** Un environnement de recette isolé, avec sa base et son connecteur
simulé, et un jeu de données de démonstration.

**Critères** Dix mille messages sont soumis au connecteur simulé sans perte,
les compteurs correspondent au nombre envoyé, et aucun message ne reste bloqué
dans un état intermédiaire.

## Ce qui n'est pas dans ce lot

Aucune interface, aucun écran, aucun gabarit autre que `base.html`. Aucune
liaison réseau réelle. Aucune authentification à deux facteurs. Ces sujets
appartiennent aux lots suivants et les traiter ici allongerait le lot 1 sans
rien sécuriser.

Le point de contrôle du lot est T-18 : dix mille messages traversent la chaîne
complète contre un simulateur, sans interface et sans opérateur réel.
