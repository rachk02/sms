# Lot 9, ouverture aux systèmes tiers

Fiches F-34, F-35, F-36 et F-46, soit 26 à 40 jours-homme. Dix tâches, T-105 à T-114.

Découpage provisoire, à revoir en entrant dans le lot.

Le serveur SMPP client, la fiche F-36, pèse 8 à 12 jours-homme pour une demande client non encore démontrée. C'est le premier candidat à sortir du périmètre de lancement, et cette décision se prend avant d'entamer le lot.

## T-105 · Politique de version de l'API

**Produit** La règle écrite de ce qui constitue une rupture, la version dans le chemin, et le mécanisme de dépréciation avec en-tête d'avertissement.

**Critères** Le document est écrit avant la première route, pas après. Ajouter un champ facultatif ne casse rien. La documentation demande explicitement aux intégrateurs d'ignorer les champs inconnus plutôt que de valider strictement.

## T-106 · Authentification par clé

**Produit** La création de clés avec préfixe en clair et secret condensé en Argon2id, l'affichage unique du secret, la révocation, et la vérification à chaque appel.

**Critères** Le secret complet n'est stocké nulle part, ce qu'un test vérifie en fouillant la base. Une clé révoquée est refusée immédiatement, sans attendre l'expiration d'un cache. La vérification d'une clé prend moins de dix millisecondes malgré Argon2id, grâce au préfixe qui évite de tester toutes les clés.

## T-107 · Quotas à deux niveaux

**Produit** Le seau à jetons dans Redis, la limite en rafale et la limite soutenue par clé, les plafonds horaire et journalier par compte, et les en-têtes de quota restant.

**Critères** Les en-têtes annoncent le restant pour les deux niveaux. Un dépassement au niveau compte lève une alerte à l'exploitation en plus du refus. Redis indisponible ne fait pas tomber l'API, il fait tomber le quota en mode permissif avec alerte, décision à documenter.

## T-108 · Route d'envoi

**Produit** La soumission unitaire et par lot, l'en-tête d'idempotence, la réponse en acceptation immédiate avec un identifiant par message, et le suivi asynchrone.

**Critères** Un rejeu avec la même clé d'idempotence rend la même réponse sans envoyer une seconde fois. Un lot de mille messages est accepté en moins de deux secondes. Un message invalide dans un lot n'annule pas les autres, la réponse détaille les rejets.

## T-109 · Routes de consultation

**Produit** Le statut d'un message, le journal filtré avec pagination par curseur, le solde, et la liste des identifiants d'expéditeur avec leur état par réseau.

**Critères** Le journal impose une plage de dates comme l'interface. La pagination par curseur est stable même si des messages sont insérés pendant le parcours. Le solde distingue acquis, réservé et disponible.

## T-110 · Documentation interactive

**Produit** La documentation générée depuis le code, un environnement d'essai, et des exemples dans les langages courants localement, PHP, Python, JavaScript et cURL.

**Critères** La documentation ne peut pas diverger du code puisqu'elle en est issue. Chaque route porte au moins un exemple complet. Un intégrateur réussit un premier envoi en bac à sable sans poser de question, ce qui se teste avec une personne qui ne connaît pas le projet.

## T-111 · Webhooks sortants

**Produit** La déclaration des points de réception, la signature HMAC sur horodatage et corps, les quatre événements, la réémission échelonnée et la file de rebut.

**Critères** Une signature valide avec un horodatage de plus de cinq minutes est rejetée par le récepteur de test. Un point de réception qui répond en erreur cinq fois est désactivé et son propriétaire prévenu. Le journal de livraison est consultable par le client lui-même.

## T-112 · Serveur SMPP pour les clients

**Produit** Un compte SMPP par grand compte, le filtrage par adresse IP, la fenêtre d'émission réglable, les limites de débit et la supervision des sessions ouvertes.

**Critères** Un client se lie en transceiver, soumet et reçoit ses accusés sur la même session. Une adresse hors liste blanche est refusée au bind. Le débit configuré est respecté. Si cette fiche sort du périmètre, la tâche saute entièrement.

## T-113 · Bac à sable pour intégrateurs

**Produit** Un environnement où une clé d'essai envoie vers des numéros de test sans consommer de crédit, avec des accusés simulés paramétrables.

**Critères** Un intégrateur teste les cas de livraison, d'échec et de sans accusé sans dépenser un franc. Les réponses ont exactement la même forme qu'en production. Aucun message ne part vers un vrai numéro depuis le bac à sable.

## T-114 · Point de contrôle du lot

**Produit** Une intégration complète réalisée depuis un script externe : création de clé, envoi par lot, réception des webhooks, consultation du journal, gestion d'un dépassement de quota.

**Critères** Le script tourne sans accéder à l'interface web. Une coupure réseau en plein envoi, rejouée avec la même clé d'idempotence, ne produit aucun doublon. Les webhooks reçus correspondent aux messages envoyés, un pour un.
