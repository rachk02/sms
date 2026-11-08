# Lot 9, ouverture aux systèmes tiers

Fiches F-34, F-35, F-36 et F-46, soit 26 à 40 jours-homme. Dix tâches, T-105 à T-114.

Découpage provisoire, à revoir en entrant dans le lot.

Le serveur SMPP client, la fiche F-36, pèse 8 à 12 jours-homme pour une demande client non encore démontrée. C'est le premier candidat à sortir du périmètre de lancement, et cette décision se prend avant d'entamer le lot.

## T-105 · Politique de version de l'API

**Produit** La règle écrite de ce qui constitue une rupture, la version dans le chemin, et le mécanisme de dépréciation avec en-tête d'avertissement.

**Critères** Le document est écrit avant la première route, pas après. Ajouter un champ facultatif ne casse rien. La documentation demande explicitement aux intégrateurs d'ignorer les champs inconnus plutôt que de valider strictement.

**Fait.** `docs/api/politique-de-version.md` : la version majeure dans le chemin (`/v1/`), une seule en service hors transition, l'en-tête `Api-Version` sur chaque réponse ; le tableau de ce qui est une rupture et de ce qui ne l'est pas ; le texte, à reprendre tel quel en tête de la documentation (T-110), qui demande d'ignorer les champs inconnus et de prévoir une branche par défaut pour toute liste fermée ; le cycle en service, dépréciée, retirée, avec les en-têtes `Deprecation` (RFC 9745), `Sunset` (RFC 8594) et `Link` de relation `deprecation`, puis `410 VERSION_RETIREE` ; la version appelée notée sur chaque clé (T-106) pour savoir qui casserait. Le journal des modifications est ouvert (`docs/api/journal-des-modifications.md`).

Choix faits, à confirmer : douze mois entre l'annonce et le retrait (le point que le plan laisse à trancher) ; relances à quatre-vingt-dix, trente et sept jours ; pas de version datée ni d'en-tête de version en requête.

## T-106 · Authentification par clé

**Produit** La création de clés avec préfixe en clair et secret condensé en Argon2id, l'affichage unique du secret, la révocation, et la vérification à chaque appel.

**Critères** Le secret complet n'est stocké nulle part, ce qu'un test vérifie en fouillant la base. Une clé révoquée est refusée immédiatement, sans attendre l'expiration d'un cache. La vérification d'une clé prend moins de dix millisecondes malgré Argon2id, grâce au préfixe qui évite de tester toutes les clés.

**Fait.** `comptes_cle_api` (modèle `CleApi`) et les services `creer_cle`, `revoquer_cle`, `verifier_cle`. La clé complète, `sk_<live|test>_<préfixe>_<secret>`, est rendue une fois par `creer_cle` et affichée une fois par l'écran « Clés d'API » du compte (administrateurs seulement, réponse non mise en cache) ; seul le condensat Argon2id du secret est conservé. La vérification lit la clé par son préfixe, grâce à une seconde politique de cloisonnement en lecture seule sur `app.cle_prefixe` (choix de l'utilisateur, `core` 0040), relit l'état en base à chaque appel, sans cache, puis pose le client de la clé. Les routes publiques passent par `api.auth.route_api`, qui ignore la session du navigateur (une session ouverte sans clé reçoit 401, une clé pose toujours son propre client), rend les erreurs sous une forme unique (`{"erreur": {"code", "message", "requete_id"}}`) et pose `Api-Version`. La création et la révocation se tracent au journal d'audit. Les tests fouillent toutes les colonnes de texte de la base et Redis à la recherche du secret, refusent une clé révoquée à l'appel suivant, mesurent la vérification (médiane de vingt appels sous dix millisecondes, parmi cinquante et une clés) et vérifient qu'une session n'ouvre rien.

Choix faits, à confirmer : Argon2id à coût réduit pour les clés (deux passes, 1 Mio), justifié par un secret aléatoire de 256 bits ; la dernière utilisation n'est réécrite qu'une fois par minute ; seuls les administrateurs du compte gèrent les clés ; une clé n'a pas d'expiration, elle se révoque.

## T-107 · Quotas à deux niveaux

**Produit** Le seau à jetons dans Redis, la limite en rafale et la limite soutenue par clé, les plafonds horaire et journalier par compte, et les en-têtes de quota restant.

**Critères** Les en-têtes annoncent le restant pour les deux niveaux. Un dépassement au niveau compte lève une alerte à l'exploitation en plus du refus. Redis indisponible ne fait pas tomber l'API, il fait tomber le quota en mode permissif avec alerte, décision à documenter.

**Fait.** `api.quotas` : par clé, deux seaux à jetons tenus ensemble dans Redis par un script Lua atomique, la rafale par seconde et le soutenu par minute (`API_QUOTAS`, provisoirement vingt et trois cents) ; un appel coûte un jeton quelle que soit sa taille, un refus rend `429 QUOTA_CLE_DEPASSE` avec `Retry-After`. Par compte, le plafond horaire et journalier du T-101, compté en messages ; `conformite.plafonds.consommer_lot` compte un lot d'un coup, laisse passer ce qui tient sous les deux plafonds et refuse la suite, et l'alerte part au premier franchissement (critique au seuil critique, même franchi par un seul lot). Chaque réponse porte les restants des deux niveaux : `Quota-Cle-Seconde-Limite` et `-Restant`, `Quota-Cle-Minute-Limite` et `-Restant`, `Quota-Compte-Heure-Limite` et `-Restant`, `Quota-Compte-Jour-Limite` et `-Restant`, ces derniers lus après la vue pour compter l'envoi qu'elle vient de faire.

Décision documentée, à confirmer : Redis indisponible fait passer le quota en mode permissif. L'appel est servi sans en-têtes de quota, le journal écrit l'erreur et l'événement `api.quota.permissif` part à l'exploitation, une fois par minute et par processus au plus. Le plafond du compte, lui aussi dans Redis, tombe avec ; ce qui part reste borné par le crédit du compte. Couper toute l'API pour une panne de cache coûterait plus cher à tous les clients que le risque d'une heure sans quota.

Autres choix, à confirmer : des limites communes à toutes les clés, sans réglage par clé ni par palier pour l'instant ; des en-têtes nommés en clair plutôt que les champs structurés du brouillon IETF `RateLimit`.

## T-108 · Route d'envoi

**Produit** La soumission unitaire et par lot, l'en-tête d'idempotence, la réponse en acceptation immédiate avec un identifiant par message, et le suivi asynchrone.

**Critères** Un rejeu avec la même clé d'idempotence rend la même réponse sans envoyer une seconde fois. Un lot de mille messages est accepté en moins de deux secondes. Un message invalide dans un lot n'annule pas les autres, la réponse détaille les rejets.

**Fait.** `POST /v1/messages` accepte un message (l'objet seul) ou un lot (sous `messages`, mille au plus) et répond `202` dès l'acceptation, avec pour chaque demande, dans l'ordre et par son rang, l'identifiant du message, son état (`RECU`), son réseau, ses segments et son coût, ou son refus nommé (`NUMERO_INVALIDE`, `RESEAU_INCONNU`, `TEXTE_VIDE`, `TEXTE_TROP_LONG` au-delà de dix segments, `EXPEDITEUR_NON_AUTORISE`, `TARIF_ABSENT`, `PLAFOND_COMPTE`, `SOLDE_INSUFFISANT`) ; rien d'accepté rend `422` avec le même détail. La vue ne décide rien : elle appelle `campagnes.services.accepter_unitaires`, la porte des envois hors campagne, qui contrôle chaque demande hors de tout verrou (numéro, réseau par la table des préfixes lue une fois, texte, expéditeur approuvé sur le réseau, tarif lu une fois par réseau), compte le lot contre le plafond du compte (`conformite.plafonds.consommer_lot`, les premières passent), réserve le crédit en une fois (`credits.reserver_messages`, décision de l'utilisateur : un verrou, une réservation par message, les premières passent tant que le disponible suffit), écrit les messages et leurs premiers événements en trois insertions groupées (`journal.services.enregistrer_messages`, dont `enregistrer_message` n'est plus qu'un cas particulier) et les dépose dans la file prioritaire de leur réseau en une insertion dans la boîte d'envoi (`mettre_en_file_lot`, mêmes clés `envoi:<id>`). La liste noire, le bac à sable et l'état de l'expéditeur au départ restent contrôlés par le moteur d'envoi, comme pour tout message ; le suivi se lit ensuite par message (T-109) et par webhook (T-111).

L'en-tête `Idempotency-Key` (255 caractères au plus) réserve la clé au début de la requête, dans sa transaction (`api_idempotence`, insertion qui ignore le conflit) : un rejeu identique rend la réponse gardée avec `Idempotent-Rejoue: true` sans rien réexécuter, ni plafond ni crédit ; un rejeu concurrent attend la fin de la première requête puis lit sa réponse ; la même clé sur un autre corps rend `422 IDEMPOTENCE_REUTILISEE`. Un corps mal formé (`400`) et un solde occupé (`503 SOLDE_OCCUPE`, `Retry-After`) ne sont pas gardés. Les clés vivent vingt-quatre heures et l'ordonnanceur efface chaque jour les expirées.

Les tests couvrent l'acceptation unitaire (journal, réservation, file prioritaire, quota du compte décompté), le rejeu sans second envoi, le refus d'une clé réutilisée, un lot mêlant sept cas dont cinq refus, le plafond puis le solde qui refusent la suite d'un lot, les corps mal formés, et le lot de mille : accepté en moins de deux secondes, avec autant de requêtes en base que pour un lot de dix. La réservation groupée a son test de concurrence : deux lots simultanés de sept messages contre un solde de dix ne réservent jamais plus que le solde.

Choix faits, à confirmer : l'en-tête d'idempotence est facultatif (recommandé dans la documentation) ; un lot accepté en partie rend `202` ; dix segments au plus par message ; l'expéditeur par défaut est le numéro court ; un expéditeur non approuvé sur le réseau du numéro est refusé sans repli ; la liste noire n'est pas vérifiée à l'acceptation (le moteur la vérifie au départ, le message passe alors en rejet et son crédit est rendu).

## T-109 · Routes de consultation

**Produit** Le statut d'un message, le journal filtré avec pagination par curseur, le solde, et la liste des identifiants d'expéditeur avec leur état par réseau.

**Critères** Le journal impose une plage de dates comme l'interface. La pagination par curseur est stable même si des messages sont insérés pendant le parcours. Le solde distingue acquis, réservé et disponible.

**Fait.** Quatre routes en lecture, derrière la même authentification et les mêmes quotas, qui appellent les selectors de l'interface :

- `GET /v1/messages/<id>` : le statut d'un message, ses segments, son coût, ses instants de soumission et d'accusé, le diagnostic de la fiche (T-94) et son historique d'états daté. L'identifiant est un UUID version 7 dont les premiers bits datent la réception : la recherche se borne à un jour de part et d'autre (`journal.selectors.fiche_par_identifiant`), jamais un balayage du journal. Inconnu, d'un autre compte ou d'une autre version : `404 MESSAGE_INCONNU`.
- `GET /v1/messages` : le journal par `journal.selectors.lire_filtres` et `rechercher`, les mêmes que l'écran. La plage `debut`, `fin` est imposée (la dernière semaine sans aucune des deux, une seule borne ou une plage à l'envers refusées, 31 jours au plus), filtres `etat`, `numero` (sur la colonne normalisée), `campagne` ; `limite` de 1 à 200 ; pagination par curseur sur (date de réception, id), rendu dans `suivant` et repris par `apres`.
- `GET /v1/solde` : `acquis`, `reserve`, `disponible`, en FCFA entiers (`XOF`).
- `GET /v1/expediteurs` : chaque identifiant, son état et son état sur chaque réseau (`null` : jamais soumis), par `expediteurs.selectors.du_compte`.

Les tests couvrent le statut et l'historique, le message d'un autre compte rendu inconnu, la plage imposée et ses refus, la pagination par pages de deux qui ne saute ni ne répète rien quand trois messages arrivent pendant le parcours, le curseur abîmé refusé, le solde après trois envois réservés, et les expéditeurs par réseau.

Choix faits, à confirmer : le texte du message n'est pas rendu par l'API (seuls son encodage, ses segments et son coût) ; la plage par défaut d'une semaine est reprise de l'écran.

## T-110 · Documentation interactive

**Produit** La documentation générée depuis le code, un environnement d'essai, et des exemples dans les langages courants localement, PHP, Python, JavaScript et cURL.

**Critères** La documentation ne peut pas diverger du code puisqu'elle en est issue. Chaque route porte au moins un exemple complet. Un intégrateur réussit un premier envoi en bac à sable sans poser de question, ce qui se teste avec une personne qui ne connaît pas le projet.

**Fait.** Une seule source, `apps/api/documentation.py`, d'où sortent le schéma OpenAPI 3.1 (`GET /v1/openapi.json`, public) et la page lisible (`/documentation/api`, publique, rendue au serveur). Les chemins viennent des routes déclarées dans `apps/api/urls.py`, les méthodes de la vue elle-même (le décorateur `route_api` les expose), les codes de refus d'un envoi de `campagnes.services`, les événements de webhook du modèle des points de réception, la version de `api.auth`. Chaque route porte son exemple complet en cURL, PHP, Python et JavaScript, généré depuis sa fiche (adresse, clé, en-tête d'idempotence pour un envoi, corps), avec un exemple de réponse ; la page reprend en tête, tel quel, le texte demandé aux intégrateurs par la politique de version (T-105), puis l'authentification, les quotas, l'idempotence, les codes d'erreur et leur code HTTP, les refus par message, les états, et les webhooks avec la vérification de signature dans les quatre langages.

Les tests (`apps/api/tests/test_documentation.py`) refusent une route publique sans fiche ou une fiche sans route, des méthodes documentées qui diffèrent de celles de la vue, un code d'erreur rendu par le code mais absent de la documentation, un code documenté jamais rendu, un code HTTP documenté qui diffère de celui rendu, un refus d'envoi non documenté, une liste d'états ou d'événements qui diverge, un texte d'en-tête qui s'écarte de la politique de version, une route sans exemple complet dans un des quatre langages ; ils lisent enfin le schéma et la page sans compte.

Reste à faire avec l'utilisateur : le troisième critère se vérifie avec une personne qui ne connaît pas le projet, sur le bac à sable de T-113. Choix faits, à confirmer : pas d'interface interactive de type Swagger (la page est rendue au serveur, sans script tiers) ; le schéma porte les exemples sous une extension `x-exemples`.

## T-111 · Webhooks sortants

**Produit** La déclaration des points de réception, la signature HMAC sur horodatage et corps, les quatre événements, la réémission échelonnée et la file de rebut.

**Critères** Une signature valide avec un horodatage de plus de cinq minutes est rejetée par le récepteur de test. Un point de réception qui répond en erreur cinq fois est désactivé et son propriétaire prévenu. Le journal de livraison est consultable par le client lui-même.

**Fait.** Quatre événements, publiés par le module qui les constate, dans sa transaction, par la boîte d'envoi :

- `client.message.livre` et `client.message.echec` (non livré, expiré, rejeté) : à l'état final d'un envoi hors campagne, dans `journal.services.changer_etat` (message, état, numéro, réseau, motif d'échec, horodatage) ; une clé par message et par état, donc une seule annonce même si l'accusé est rejoué.
- `client.message.entrant` : un SMS reçu, hors désinscription, attribué au client qui a écrit à ce numéro s'il est seul (`journal.services.attribuer_entrant`) ; un STOP est annoncé à chacun des clients rapprochés.
- `client.solde.bas` (décision de l'utilisateur : seuil réglé par le client) : `comptes_client.seuil_alerte_solde`, en FCFA entiers, zéro pour aucune alerte. Après chaque mouvement du disponible, sous le verrou du solde (`credits.services._surveiller_seuil`), l'alerte part une fois au passage sous le seuil et se désarme ; elle se réarme quand le disponible repasse au-dessus du seuil majoré de vingt pour cent, pour qu'un solde qui oscille ne produise pas une alerte par recharge.

La file `webhooks.evenements` reçoit les événements `client.#`. Le worker webhook (`workers/webhook.py`) répartit chacun, une fois (`traiter_une_fois`), en une livraison par point actif abonné (`api.webhooks.repartir`, unicité par point et par événement) et dépose un ordre par livraison dans `webhooks.sortants`. Chaque tentative (`api.webhooks.livrer`) envoie un POST JSON (`id`, `type`, `cree_le`, `donnees`), sans suivre de redirection, délai de dix secondes, avec les en-têtes `Signature: t=<horodatage>,v1=<HMAC-SHA256 de "<t>.<corps>">`, `Webhook-Id` et `Webhook-Evenement`. Une réponse 2xx livre ; toute autre réponse, une redirection ou une absence de réponse renvoie l'ordre au palier suivant (une minute, cinq, trente, deux heures, six heures), puis au rebut, où la livraison est marquée abandonnée. Cinq échecs consécutifs sur un point le désactivent et préviennent par courriel celui qui l'a créé (à défaut, les administrateurs du compte) ; les livraisons encore en attente vers lui sont abandonnées sans tentative. `api.webhooks.verifier_signature` est l'implémentation de référence côté récepteur : comparaison en temps constant, horodatage refusé au-delà de cinq minutes dans un sens comme dans l'autre.

Un point se déclare à l'écran `Webhooks` du compte (administrateurs, permission `webhooks.gerer`) : adresse en https dont l'hôte résout vers des adresses publiques seulement (vérifié à la déclaration et à chaque livraison, contre le relais vers le réseau interne), événements choisis, dix points actifs au plus. Le secret de signature (`whsec_...`) est montré une fois et gardé chiffré par la clé applicative. Le même écran désactive et réactive un point (compteur remis à zéro) et règle le seuil d'alerte ; le journal des livraisons montre les cent dernières, avec état, tentatives et dernière réponse. Le réglage `WEBHOOKS_ADRESSES_PRIVEES=1` accepte http et les adresses internes, pour le poste de développement et les tests ; `prod.py` le force à faux. Tables `api_point_reception` et `api_livraison`, cloisonnées (`core` 0042).

Les tests couvrent la signature (acceptée à moins de cinq minutes, refusée au-delà dans les deux sens, corps retouché, autre secret, en-têtes abîmés), la déclaration (secret chiffré, refus d'adresses, d'événements et d'hôtes internes), la répartition (un point abonné, ni le point d'un autre événement ni le point désactivé, aucun doublon au rejeu), une livraison reçue par un récepteur de test qui vérifie la signature puis refuse le même envoi rejoué six minutes plus tard, la remise à zéro du compteur au succès, les cinq échecs (erreurs serveur, redirection, page absente) qui désactivent le point et préviennent son propriétaire, le point injoignable, le seuil de solde bas qui part une fois puis se réarme (réservation unitaire et groupée), et l'écran du compte (déclaration, journal limité au compte, désactivation, réactivation, point d'un autre compte introuvable, seuil).

Côté producteur (`apps/journal/tests/test_annonces_webhooks.py`) : le sort final d'un envoi hors campagne annoncé une seule fois, l'échec avec son motif, rien pour un message de campagne, un message système ou un état intermédiaire, l'entrant attribué au seul client qui a écrit au numéro, rien pour un entrant ambigu ou sans émetteur, et le STOP annoncé à chacun des clients rapprochés. Le worker écrit l'entrant et l'attribue dans une seule transaction, pour qu'une panne entre les deux ne perde pas l'annonce.

Choix faits, à confirmer : format d'en-tête `Signature` sur le modèle courant ; dix secondes de délai par tentative ; dix points actifs au plus par compte ; une livraison abandonnée ne se relance pas à la main (pas encore d'écran de relance) ; le journal montre les cent dernières livraisons sans pagination ; réarmement de l'alerte à 120 % du seuil ; le texte d'un SMS entrant est transmis dans l'événement ; la file `courriels.sortants` reste sans producteur ni traitement, les courriels partant toujours par `apps.core.courriels`.

## T-112 · Serveur SMPP pour les clients

**Produit** Un compte SMPP par grand compte, le filtrage par adresse IP, la fenêtre d'émission réglable, les limites de débit et la supervision des sessions ouvertes.

**Critères** Un client se lie en transceiver, soumet et reçoit ses accusés sur la même session. Une adresse hors liste blanche est refusée au bind. Le débit configuré est respecté. Si cette fiche sort du périmètre, la tâche saute entièrement.

**Sortie du périmètre de lancement** (décision de l'utilisateur du 8 novembre 2026). La tâche saute ; elle se reprendra si une demande client se démontre.

## T-113 · Bac à sable pour intégrateurs

**Produit** Un environnement où une clé d'essai envoie vers des numéros de test sans consommer de crédit, avec des accusés simulés paramétrables.

**Critères** Un intégrateur teste les cas de livraison, d'échec et de sans accusé sans dépenser un franc. Les réponses ont exactement la même forme qu'en production. Aucun message ne part vers un vrai numéro depuis le bac à sable.

## T-114 · Point de contrôle du lot

**Produit** Une intégration complète réalisée depuis un script externe : création de clé, envoi par lot, réception des webhooks, consultation du journal, gestion d'un dépassement de quota.

**Critères** Le script tourne sans accéder à l'interface web. Une coupure réseau en plein envoi, rejouée avec la même clé d'idempotence, ne produit aucun doublon. Les webhooks reçus correspondent aux messages envoyés, un pour un.
