# EL-IMMEKA SMS, conventions du dépôt

Ce fichier est lu au début de chaque session. Il porte les règles qui ne se
rediscutent pas. Quand une consigne ici contredit une habitude, c'est la
consigne qui gagne.

## Le produit en trois phrases

Plateforme professionnelle d'envoi de SMS en masse pour le Burkina Faso, vendue
à des entreprises et des organisations. Les SMS partent vers les trois réseaux
nationaux par liaison SMPP directe, sans agrégateur, à travers la passerelle
Jasmin. Les clients achètent des crédits d'avance et les consomment à l'envoi.

## Pile technique

Python 3.14, Django 6.1 (puis 6.2 LTS à sa sortie), PostgreSQL 16, Redis, RabbitMQ,
MinIO, Jasmin, Docker Compose.
Interface rendue au serveur avec gabarits Django, HTMX, Alpine et Tailwind.
Pas d'application monopage, pas de React sauf sur deux écrans nommés plus bas.

## Règles de structure

Le code vit dans quinze modules sous `apps/`, découpés sur les frontières
métier. Un module possède ses tables, ses règles, ses vues et ses gabarits.

    core  comptes  fichiers  conformite  telecom  expediteurs  journal
    credits  contacts  campagnes  paiements  notifications  support
    api  backoffice

Les dépendances ne remontent jamais. L'ordre des couches, du bas vers le haut :

1. `core`, `comptes`
2. `telecom`, `conformite`, `fichiers`
3. `journal`, `credits`, `expediteurs`
4. `campagnes`, `contacts`, `paiements`
5. `notifications`, `support`
6. `api`, `backoffice`

Un module n'importe que ce qui est sous lui. Quand une couche basse doit
signaler quelque chose à une couche haute, elle publie un événement par
`apps.core.evenements.publier`, elle n'importe rien. Concrètement, `telecom` ne
connaît pas `campagnes` : il reçoit un ordre de soumission et publie un accusé.

Si vous êtes tenté d'écrire un import qui remonte, c'est que le découpage est
en cause. Arrêtez-vous et posez la question plutôt que de contourner.

La règle vaut aussi pour les clés étrangères. Une table qui référence une table
d'un module plus haut porte un UUID simple, indexé, sans `ForeignKey` Django :
`journal_message.campagne_id`, `credits_reservation.campagne_id`,
`telecom_correlation.message_id`, `conformite_consentement.contact_id`. La
cohérence de ces références est tenue par les services, pas par la base.

## Règles par fichier

- `models.py` : données et contraintes. Aucune logique métier, aucun appel réseau.
- `services.py` : la seule porte d'écriture du module. Toute mutation passe ici.
- `selectors.py` : lectures complexes. Aucune écriture.
- `views.py` : appelle un service ou un selector, rend un gabarit. Ne décide rien.
- `fragments.py` : vues HTMX. Mêmes contrôles de droits que les vues de page.
- `tasks.py` : consommateurs de file. Appellent les mêmes services que les vues.
- `forms.py` : validation de saisie. La règle métier reste dans `services.py`.

Un envoi déclenché par l'interface et un envoi déclenché par l'API doivent
traverser exactement la même fonction de service. Si le chemin diffère, les deux
divergeront en silence.

## Conventions de nommage

Le domaine métier est en français : `Campagne`, `Expediteur`, `credits_registre`,
`soumettre_campagne`. Les noms de modules et de champs sont sans accents, pour
éviter tout problème d'encodage en base et en ligne de commande. Les concepts
techniques standard gardent leur nom usuel : `request`, `queryset`, `settings`.

Les états sont en majuscules, en français, et définis comme `TextChoices` :
`RECU`, `EN_ATTENTE`, `EN_FILE`, `SOUMIS`, `LIVRE`, `NON_LIVRE`, `EXPIRE`,
`SANS_ACCUSE`, `REJETE`, `ANNULE`.

Chaque écran vivant expose deux entrées, la page et son fragment :

    /campagnes/<id>
    /campagnes/<id>/fragments/suivi

## Interdits

- Pas de tiret cadratin ni d'emoji, nulle part : ni dans le code, ni dans les
  commentaires, ni dans les gabarits, ni dans les messages de commit, ni dans
  la documentation. Cette règle est absolue.
- Pas de logique métier dans un gabarit ni dans une vue.
- Pas de clé séquentielle exposée. Les identifiants publics sont des UUID.
- Pas de secret en dur. `prod.py` ne lit que des variables d'environnement.
- Pas de `SELECT` sans borne sur `journal_message` : toute recherche porte une
  plage de dates, sinon elle balaie toutes les partitions.
- Pas de `OFFSET` pour paginer. Pagination par curseur sur (date, id).
- Pas de compteur calculé à la volée sur le journal. Les tableaux de bord lisent
  des compteurs pré-agrégés écrits par les workers.
- Pas de `LIKE '%...%'` sur un numéro. La recherche porte sur la colonne
  normalisée.
- Pas de migration de schéma qui touche aux politiques de cloisonnement. Ces
  politiques vivent dans des migrations dédiées de `core`.

## Points délicats, à ne pas improviser

Trois zones se spécifient au détail près et se relisent ligne à ligne. Si une
tâche vous y emmène sans instruction précise, demandez avant d'écrire.

**Le registre de crédits.** `credits_registre` est en écriture seule : aucune
ligne n'est modifiée ni supprimée, jamais. Le solde est la somme des écritures.
`credits_compte` est un cache, verrouillé par `SELECT ... FOR UPDATE` pendant
une réservation. Tous les montants et tous les prix sont des FCFA entiers :
aucun prix à virgule, nulle part. Deux campagnes lancées en même temps ne doivent pas pouvoir
réserver le même crédit.

**La corrélation des accusés.** L'identifiant rendu par l'opérateur à la
soumission est propre au SMSC, parfois hexadécimal, parfois décimal. Il se
range en Redis avec 72 heures de validité, avec une reprise en base. Un accusé
sans correspondance va dans la table des accusés orphelins, il n'est jamais jeté.

**Le cloisonnement par ligne.** Chaque table métier porte `client_id` et une
politique :

    USING (client_id = NULLIF(current_setting('app.client', true), '')::uuid)

Le middleware pose ce réglage à chaque requête, local à la transaction. Sans
client posé, une requête voit zéro ligne : c'est voulu, l'absence de client
n'ouvre jamais l'accès à tout. Trois détails font que cela tient vraiment :

- `current_setting(..., true)` et `NULLIF` : sans eux, un réglage absent ou
  vidé lève une erreur au lieu de rendre zéro ligne.
- `FORCE ROW LEVEL SECURITY` sur chaque table, et un rôle applicatif distinct
  du propriétaire des tables : le propriétaire échappe sinon à la politique.
- `comptes_utilisateur` n'est pas cloisonnée : la connexion cherche un
  utilisateur par son courriel avant que le client soit connu.

## Encodage des SMS

Sont dans la table GSM 03.38, donc sans surcoût : é, è, à, ù, ì, ò, ä, ö, ü, ñ,
Ç majuscule, ß, æ. Font basculer en UCS-2, ce qui fait tomber la capacité de 160
à 70 caractères : ê, â, î, ô, û, ë, ï, ç minuscule, œ, les guillemets
typographiques, les tirets longs, les points de suspension en un caractère, et
les emoji. Les caractères de la table d'extension comptent double : €, accolades,
crochets, tilde, accent circonflexe seul, barre verticale, barre oblique inverse.

Un message long se découpe en segments de 153 caractères en GSM et de 67 en
UCS-2, à cause de l'en-tête de concaténation.

Le compteur affiché au client doit annoncer le cas le plus long après
personnalisation, jamais la moyenne, sinon le coût affiché est sous-estimé.

Voir `apps/core/encodage.py`.

## Tests

Chaque service écrit reçoit son test. Pas de test d'interface au lot 1.

Les trois zones délicates ci-dessus reçoivent des tests de concurrence et de
rejeu, pas seulement des tests de cas nominal : deux réservations simultanées,
un accusé rejoué deux fois, une requête sans client posé.

    uv run python manage.py migrate --database proprietaire
    uv run pytest
    uv run ruff check .

Les migrations passent toujours par la connexion `proprietaire`, qui possède
les tables. `default` est le rôle applicatif, soumis au cloisonnement ;
`worker` le contourne. Les tests créent leur base par `proprietaire` (voir
`conftest.py`) et y raccrochent les deux autres.

## Ce qui est décidé et ne se rediscute pas

Interconnexion directe avec les trois opérateurs, sans agrégateur. Jasmin comme
passerelle, derrière une couche d'abstraction. Pas d'authentification par un
fournisseur tiers. Rendu serveur, pas d'application monopage. Le modèle de
données porte le pays dès le départ, même si un seul est servi au lancement.

Les deux seuls écrans candidats à un îlot React sont le constructeur de segments
et le compositeur de campagne. Partout ailleurs, HTMX et Alpine.

## Où trouver le reste

- `docs/modele-de-donnees.md` : tables, champs, types, contraintes, index
- `docs/lot-1-taches.md` : le lot courant découpé avec ses critères d'acceptation

Le plan d'implémentation complet, l'architecture backend et le document sur le
support et les notifications sont tenus hors du dépôt. Demandez-les si une
décision vous manque, ne l'inventez pas.
