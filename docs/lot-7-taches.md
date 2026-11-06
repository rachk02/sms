# Lot 7, crédits, paiements locaux et facturation

Fiches F-26, F-27, F-28, F-29 et F-45, soit 38 à 59 jours-homme. Quatorze tâches, T-77 à T-90.

Découpage provisoire, à revoir en entrant dans le lot.

C'est le lot où circule l'argent. Une erreur ici ne se rattrape pas par un correctif, elle se rattrape par des avoirs et des explications à des clients.

## Décisions prises avant d'entrer dans le lot

Tranchées avec l'utilisateur le 6 novembre 2026.

**Moment du règlement.** Le pay-per-submitted reste le mode de tous les clients :
un message accepté par le réseau consomme son coût à la soumission, comme
aujourd'hui. Le pay-per-delivered s'active client par client, pour un grand
compte qui l'a négocié. Les deux modes passent par la même réservation ; seul
le moment du règlement change.

**Sans accusé.** En pay-per-delivered, un message resté SANS_ACCUSE est facturé :
l'absence d'accusé n'est pas un échec avéré. Seuls NON_LIVRE, EXPIRE et REJETE
rendent leur réservation.

**Facture et TVA.** La facture s'émet à l'achat des crédits. Les crédits sont
hors taxes : le client achète 10 000 FCFA de crédits et paie 11 800 FCFA, TVA
de 18 % en sus. Les prix des grilles restent hors taxes et entiers.

**Expiration.** Les crédits n'expirent pas. Aucune colonne `expire_le` sur le
registre. Le sort des comptes dormants relève des conditions générales.

**Clôture.** Le solde restant est remboursé sur demande écrite, pour la seule
part payée et non consommée ; les crédits offerts ou recrédités sont perdus. Le
remboursement est saisi par un agent et donne lieu à un avoir. T-89 écrit la
règle de calcul de cette part et la teste.

**Paliers de volume.** Le palier se calcule sur le volume de SMS soumis par le
client depuis le début du mois civil. Le palier atteint au lancement d'une
campagne vaut pour toute la campagne : le coût annoncé ne bouge pas en cours
de diffusion.

**Moyens de paiement.** Aucun contrat marchand ni accès de bac à sable Orange
Money ou Moov Money pour l'instant. Le parcours, les rappels, la vérification
de statut et l'idempotence se construisent contre un fournisseur simulé, derrière
une abstraction ; les adaptateurs réels s'écriront à la réception de la
documentation marchande, avec l'accord de l'utilisateur pour tout compte
externe et tout secret.

**Grilles tarifaires.** Seul un super-administrateur publie une version datée
d'une grille, sans double validation. L'ancien et le nouveau prix vont au
journal d'audit. Une version publiée ne se modifie plus.

Deux décisions de plus, tranchées avec l'utilisateur le 6 novembre 2026 en
ouvrant T-81 :

**Sans grille publiée.** Quand aucune version publiée n'est en vigueur, l'envoi
est refusé avec « Aucun tarif en vigueur pour le réseau ... ». Le prix
provisoire des réglages (`SMS_PRIX_SEGMENT_PROVISOIRE`) ne sert plus qu'en
développement et dans les tests, jamais en recette ni en production.

**Prix d'une campagne.** La version de grille et le palier en vigueur à la
confirmation valent pour tous les messages de la campagne. La réservation est
exacte, et un changement de grille pendant une campagne lissée ou en pause ne
la touche pas.

Et en ouvrant T-83 :

**Priorité entre grilles.** Réseau par réseau, quand la grille négociée du
client ou une promotion qui le vise tarifie le réseau, le client paie le plus
bas de ces prix. Sans l'une ni l'autre, la grille publique s'applique.

**Promotions.** Une promotion est ouverte à tous les clients ou réservée à un
client nommé. Elle est toujours à durée limitée, avec une date de fin
obligatoire.

Et avant T-84, pour le rechargement et la facture :

**Montants et TVA.** Les crédits s'achètent par multiples de 50 FCFA HT : à
18 %, la TVA tombe toujours sur un franc entier (50 HT, 9 de TVA, 59 TTC). La
TVA doit pouvoir se désactiver par configuration : l'utilisateur n'est pas sûr
qu'elle s'applique à ce service. Le pas se déduit du taux (100 divisé par le
plus grand diviseur commun de 100 et du taux), et vaut 1 franc sans TVA.

**Bornes d'un rechargement.** Non fixées par l'utilisateur. Le montant minimal
et le montant maximal sont des réglages vides tant qu'il ne les a pas choisis ;
à fixer avec les plafonds des portefeuilles mobiles.

Et en ouvrant T-89, le 6 novembre 2026 :

**Ordre de consommation.** Les envois consomment d'abord les crédits offerts
(recrédits), puis les crédits payés. À la clôture, la part remboursable est donc
la plus grande possible : ce qui a été payé moins ce que les envois ont pris
au-delà des crédits offerts.

**Effacement des contacts.** Trente jours après la clôture, les contacts du
compte sont effacés pour de bon. Le client garde ce mois pour exporter ses
répertoires ou revenir sur sa décision.

**Factures et administration fiscale.** L'utilisateur vérifie avec un comptable
les mentions obligatoires et l'éventuelle certification des factures par la
DGI. D'ici là, l'émission reste coupée : l'identité de l'émetteur n'est pas
renseignée.

## Choix faits à l'ouverture, à confirmer

Ces points n'ont pas été posés à l'utilisateur. Ce sont des choix techniques
pris pour avancer, à confirmer ou à corriger.

- Le taux de TVA se lit en base, pas dans le code.
- Un envoi hors campagne prend le palier atteint à sa soumission.
- Les identifiants d'EL-IMMEKA portés par la facture (raison sociale, RCCM, IFU,
  régime fiscal, adresse) ne s'inventent pas : ils se lisent dans une
  configuration que l'utilisateur renseignera. Tant qu'elle est vide,
  l'émission d'une facture est refusée, et le test de T-88 vérifie la présence
  de chaque mention à partir de valeurs d'essai.
- Sens des types d'écriture, que la règle de clôture exige de tenir distincts :
  ACHAT est un crédit payé, et seulement cela, y compris un paiement manuel
  (T-87 passe par l'achat, avec l'agent pour auteur, jamais par un ajustement
  positif) ; RECREDIT est un crédit offert, défaillance de la plateforme ou
  geste commercial ; AJUSTEMENT corrige une erreur, il n'offre jamais rien.
- Quels crédits se consomment en premier : tranché le 6 novembre 2026, les
  offerts d'abord (voir les décisions en tête). Le montant remboursable se
  calcule par sommes par type, sans colonne de plus.

## T-77 · Registre de crédits, écritures complètes

**Produit** Les quatre types d'écriture, achat, consommation, recrédit et ajustement, avec le motif obligatoire sur l'ajustement et l'auteur sur toute écriture manuelle.

**Critères** Aucune méthode ne modifie ni ne supprime une écriture. Le solde recalculé depuis le registre égale le cache après mille opérations mêlant les quatre types. Un ajustement sans motif est refusé au niveau du service, pas seulement du formulaire.

**Fait.** L'essentiel tenait depuis T-08 : modèle et déclencheur en écriture seule,
ajustement motivé au service et en base, signe du montant contrôlé en base. T-77
ajoute la trace des écritures manuelles et automatiques :

- un achat renvoie toujours à sa transaction de paiement, refusé sans référence
  au service et en base (`registre_achat_reference`) ; un paiement manuel porte
  l'agent pour auteur ;
- un recrédit est soit automatique, avec la référence du message ou de
  l'incident, soit manuel, avec son agent et son motif ; la base refuse tout
  autre cas (`registre_recredit_trace`) ;
- une insertion qui mettrait à jour en conflit (`bulk_create` avec
  `update_conflicts`) est refusée par le modèle, et par le déclencheur si on
  contourne le modèle ;
- mille opérations tirées au hasard d'une graine fixe, mêlant les quatre types,
  réservations, règlements et libérations, laissent le cache égal au solde
  recalculé, et chaque ligne porte le solde cumulé jusqu'à elle.

## Préalable à T-78 · Constat des messages sans accusé

Jusqu'ici, aucun code ne faisait passer un message en SANS_ACCUSE : l'état et sa
colonne de compteur existaient, la transition jamais. Sans elle, une campagne
réglée à la livraison ne pourrait jamais conclure.

**Fait.** `journal.services.constater_sans_accuse` fait passer SANS_ACCUSE, par
lots, les messages restés SOUMIS au-delà de la validité de la corrélation
(`telecom.correlation.validite()`, 72 heures par défaut), jamais d'une durée
écrite en dur : un seuil plus court refuserait un accusé légitime. La transition
passe par `changer_etat`, qui écrit l'événement et le compteur du suivi. La
recherche borne la date de réception (quatre-vingt-dix jours d'attente au plus
entre réception et soumission) et s'appuie sur un index partiel des messages
SOUMIS. Les lignes verrouillées par un accusé en cours sont sautées et reprises
au passage suivant. L'ordonnanceur la lance chaque minute, jusqu'à vingt lots.
Un accusé arrivé après le constat est refusé, comme toute sortie d'un état
terminal.

## T-78 · Réservation et règlement

**Produit** La réservation à la soumission, le règlement à l'accusé en mode pay-per-delivered, le règlement immédiat en pay-per-submitted, et la libération en cas d'échec.

**Critères** Les deux modèles passent par le même mécanisme, seul le moment du règlement change. Un message en échec définitif libère exactement sa réservation. Un message livré consomme exactement son coût figé à la soumission, même si la grille a changé entretemps.


**Fait.** Le client porte son mode de règlement (`comptes_client.mode_reglement`,
SOUMISSION par défaut), changé par `comptes.services.changer_mode_reglement`,
motivé et tracé au journal d'audit. La réservation fige le mode du client au
moment où elle est prise : un client passé à l'autre mode en cours de campagne
ne change rien à celle-ci.

- `credits.services.solder_message` reste la suite de la soumission : en
  règlement à la soumission, le message consomme ; à la livraison, il attend.
  Un refus avant le réseau ne coûte rien dans les deux modes.
- `credits.services.solder_final` est la suite de l'état final : sans effet en
  règlement à la soumission ; à la livraison, livré ou sans accusé consomme le
  coût figé sur le message, non livré, expiré ou rejeté ne coûte rien, et la
  réservation propre à un envoi unitaire est libérée.
- Une campagne réglée à la livraison se clôt quand tout est soumis, avec un
  coût réel provisoire ; sa réservation attend que plus aucun message ne soit
  SOUMIS. L'ordonnanceur la conclut alors (`campagnes.diffusion.solder`) :
  consommation des livrés et des sans accusé, libération du reliquat, coût réel
  définitif, trace `campagnes.reglement` au journal d'audit.
- L'ordonnanceur règle aussi, chaque minute, les envois unitaires réglés à la
  livraison qui ont leur état final.

Choix faits, à confirmer :

- Le règlement se fait par balayage de l'ordonnanceur, pas au moment même de
  l'accusé : `journal` et `credits` sont de la même couche et ne s'importent
  pas, et un verrou du compte client à chaque accusé ralentirait le traitement
  des accusés. Le client n'y perd rien : le montant est déjà réservé, son solde
  disponible ne bouge qu'à la libération du reliquat.
- Pour une campagne, la consommation s'écrit en une fois, à la conclusion, et
  non au fil des accusés. Le compteur de coût du suivi en direct annonce le
  coût émis, pas le coût facturé ; le coût réel de la campagne, lui, est juste
  à la conclusion.
- Aucun écran ne change encore le mode d'un client : le service existe, l'écran
  du back-office viendra avec la refonte de l'interface.
## T-79 · Concurrence sur le solde

**Produit** Le verrou sur la ligne de cache pendant la réservation, et la gestion de l'attente.

**Critères** Deux campagnes lancées simultanément sur un solde qui n'en couvre qu'une : l'une passe, l'autre est refusée avec un message clair, et le solde final est juste. Le test lance les deux dans deux transactions réelles, pas en simulation.

**Fait.** Le verrou de la ligne `credits_compte` (T-09) protège déjà la réservation ; T-79 y ajoute la gestion de l'attente. Une réservation demandée par un client qui attend la réponse (confirmation d'une campagne, envoi de test) n'attend le verrou que dix secondes (`ATTENTE_RESERVATION_MS`, par un `lock_timeout` local rendu ensuite à sa valeur) ; au-delà, elle est refusée par `SoldeOccupe`, que la campagne traduit en « Le solde est occupé par une autre opération en cours. Réessayez dans un instant. ». Les opérations des workers (achat, règlement, libération, occurrence d'une campagne récurrente confirmée par l'ordonnanceur) attendent leur tour sans borne : l'ordonnanceur ne réessaie pas une occurrence refusée, qui passerait sinon à confirmer par le client pour un simple verrou passager. À la confirmation, la réservation passe après l'insertion des destinataires, pour que le verrou du solde ne soit tenu qu'en fin de transaction ; un refus annule tout, destinataires compris. Les tests confirment deux campagnes dans deux fils, chacun sur sa connexion : l'une passe, l'autre reçoit « Solde insuffisant : 30 FCFA disponibles, 60 nécessaires. », reste en brouillon sans destinataire ni réservation, et le cache égale le solde recalculé.

Choix fait, à confirmer : le délai de dix secondes, assez long pour une confirmation voisine qui insère des milliers de destinataires.

## T-80 · Rapprochement quotidien du registre

**Produit** Le traitement qui compare le solde recalculé au cache pour chaque client, et l'alerte sur tout écart.

**Critères** Un écart introduit artificiellement d'un franc déclenche l'alerte. Le traitement passe sur mille comptes en moins d'une minute. L'alerte nomme le client et le montant de l'écart.

**Fait.** `apps/credits/rapprochement.py` compare, en une seule requête, le solde acquis du cache à la somme du registre et le montant réservé à la somme des réservations actives, pour tous les clients, y compris un client qui a des écritures sans ligne de cache. Une seule requête voit les trois tables dans le même instantané : une opération en cours n'y crée pas de faux écart. Chaque écart donne une ligne d'erreur au journal applicatif, qui nomme le client (raison sociale et identifiant) et les montants, et un événement `credits.rapprochement.ecart` dans la boîte d'envoi. Rien n'est corrigé automatiquement : une correction passe par une écriture d'ajustement motivée. L'ordonnanceur le lance au démarrage puis toutes les vingt-quatre heures. Les tests couvrent un franc d'écart sur le solde, un franc sur le réservé, un cache manquant, et mille comptes rapprochés en moins d'une minute, borne vérifiée par le test.

Choix faits, à confirmer :

- le rapprochement tourne toutes les vingt-quatre heures depuis le démarrage de l'ordonnanceur, pas à heure fixe ; un redémarrage le relance, ce qui peut répéter une alerte ;
- l'alerte est une ligne d'erreur et un événement ; qui la reçoit (courriel aux administrateurs, tableau du back-office) relève du module des notifications, au lot qui le porte.

## T-81 · Grilles tarifaires datées

**Produit** Les grilles, leurs versions avec dates de prise d'effet et de fin, les paliers de volume, le prix par réseau et par pays, et la contrainte d'exclusion qui interdit le chevauchement.

**Critères** Deux versions qui se chevauchent sont refusées par la base. Une grille sans version publiée n'est jamais appliquée. Le prix appliqué à un message est celui en vigueur à sa date de soumission, ce qu'un test vérifie en changeant la grille entre deux envois.

**Fait.** Trois tables, `credits_grille`, `credits_grille_version` et `credits_grille_palier`. Une contrainte d'exclusion (extension `btree_gist`) refuse deux versions publiées d'une même grille dont les périodes `[debut, fin)` se recouvrent ; une seule grille publique. Les services préparent une version en brouillon, posent ses prix, puis la publient : elle prend effet à sa date ou tout de suite, jamais dans le passé, clôt la version en vigueur à cette date, et la publication va au journal d'audit avec les anciens et les nouveaux prix. Une version publiée ne change plus. La grille publique doit tarifer, dès le premier segment du mois, chaque réseau connu des pays qu'elle couvre. `credits.tarifs.prix_segment` lit la version publiée en vigueur à la date demandée, pour le pays du client, le réseau et le volume ; sans version publiée, il refuse (`AucunTarif`), sauf prix de secours posé en développement. La grille publique se lit par tous les clients, une grille négociée par son seul client, versions et prix compris (migration de cloisonnement dédiée dans `core`) ; le rôle applicatif n'y écrit pas. Les tests changent la grille entre deux envois de test : le premier garde 19 FCFA, le second prend 23 FCFA.

Choix faits, à confirmer :

- un brouillon peut recouvrir une version publiée : la contrainte d'exclusion ne porte que sur les versions publiées, sinon aucune version future ne pourrait se préparer tant que la version en vigueur n'a pas de fin ;
- le palier retenu s'applique à tout le segment, pas par tranches : à 10 000 SMS dans le mois, chaque segment suivant coûte le prix du palier 10 000 ;
- la diffusion d'une campagne lit encore la grille message par message ; T-82 fige le prix à la confirmation, comme décidé.

Publication hors des tests : `manage.py publier_grille prix.csv --par <courriel du super-administrateur>` (options `--nature`, `--client`, `--nom`, `--debut`, `--fin`), sous le rôle du back-office, tout ou rien. Seul un super-administrateur crée ou publie une grille, par la commande comme par les services. La recette n'a pas de prix de secours : une grille publique doit y être publiée par cette commande avant tout envoi. Une page de campagne en brouillon sans tarif l'annonce au lieu d'échouer.

Prérequis de production : l'extension PostgreSQL `btree_gist`, créée par la migration sous le rôle propriétaire. Elle est de confiance depuis PostgreSQL 13, le propriétaire de la base peut donc la créer sans super-utilisateur.

## T-82 · Calcul du coût et gel sur le message

**Produit** Le calcul à la soumission, tenant compte du palier de volume, du réseau et du nombre de segments, avec le coût figé sur la ligne du message.

**Critères** Un message soumis avant un changement de grille conserve son coût d'origine, y compris quand son accusé arrive après. Le passage d'un palier de volume en cours de campagne est traité de façon documentée et le rapport de campagne l'explique.

**Fait.** À la confirmation, `campagnes.services.tarifer` lit le volume du mois (`journal.selectors.volume_mensuel`, sur les compteurs pré-agrégés, jamais sur `journal_message`) et, par réseau servi, le prix du palier atteint dans la version publiée en vigueur ; l'aperçu calcule son coût avec ces prix, et `_figer` les fige sur la campagne (`campagnes_campagne.tarification` : date, version, volume du mois, prix et palier par réseau). La diffusion prend ces prix pour chaque message, qui porte ensuite son coût figé jusqu'au règlement, accusé tardif compris (déjà vérifié par T-78). Un palier franchi pendant la diffusion ne change rien à la campagne en cours : ses SMS comptent pour les envois suivants. La page de la campagne confirmée l'explique en clair (`selectors.explication_tarif`). Un envoi de test prend le palier atteint au moment de l'envoi. Les tests franchissent le palier au cinquième message d'une campagne de huit, qui garde son prix, puis la campagne suivante prend le palier ; une grille changée après la confirmation ne touche pas la campagne.

Choix faits, à confirmer :

- le volume du mois compte des SMS soumis (`emis`), un message long comptant pour un, et pas encore les envois hors campagne, que `journal_compteur` ne porte pas ;
- le mois civil suit l'heure de Ouagadougou ;
- une campagne confirmée avant ce changement, sans tarif figé, prend le prix du moment à la diffusion.

## T-83 · Grilles négociées et promotionnelles

**Produit** Les grilles propres à un client, les grilles à durée limitée, et la règle de priorité entre grille client, grille promotionnelle et grille publique.

**Critères** La règle de priorité est écrite et testée sur les six combinaisons possibles. Un client sans grille négociée tombe sur la grille publique. La modification d'une grille laisse une écriture au journal d'audit avec l'ancien et le nouveau prix.

**Fait.** Chaque grille porte sa nature : PUBLIQUE (unique, sans client), NEGOCIEE (une par client) ou PROMOTION (sans client pour tous, ou avec un client nommé), contrôlées par la base. Une promotion ne se publie qu'avec une fin. La règle, écrite en tête de `apps/credits/tarifs.py` : réseau par réseau, le plus bas de la grille négociée et des promotions qui visent le client ; sans elles, la grille publique ; sinon, refus. Les six combinaisons (négociée absente ou présente, promotion absente, plus basse ou plus haute) sont testées une à une, ainsi qu'un client sans grille négociée, une promotion réservée à un autre client, une promotion échue et une grille négociée qui ne tarifie qu'un réseau. La publication d'une nouvelle version d'une grille négociée va au journal d'audit avec l'ancien et le nouveau prix, le client et l'agent. Le tarif figé d'une campagne note, par réseau, la grille appliquée, et la page de la campagne la nomme.

Choix faits, à confirmer :

- la grille publique n'entre pas dans la comparaison : un prix négocié plus haut que le prix public reste le prix du client ;
- à prix égal, la grille négociée l'emporte sur la promotion ;
- une grille négociée ou une promotion peut ne tarifer que certains réseaux ; les autres suivent la grille publique ;
- le palier de volume se lit dans chaque grille avec le même volume du mois.

## T-84 · Point d'entrée des rappels de paiement

**Produit** La route publique par fournisseur, la liste blanche d'adresses, la vérification de signature et la limite de débit par source.

**Critères** Un rappel non signé est rejeté sans réponse détaillée. Un rappel venu d'une adresse hors liste blanche est rejeté et journalisé. Un client malveillant qui sonde la route n'apprend rien sur l'existence d'une transaction.

**Fait.** Nouveau module `apps/paiements` (quatrième couche). La route publique `/rappels/paiements/<fournisseur>` applique, dans cet ordre, la liste blanche des plages d'adresses du fournisseur, la limite de débit par adresse source (compteur Redis par minute) et la signature. Le fournisseur simulé signe comme le fera un vrai : en-tête `X-Signature: t=<horodatage>,v1=<HMAC-SHA256 de « horodatage.corps »>`, refusé au-delà de cinq minutes. Tout refus, fournisseur inconnu compris, reçoit la même réponse nue (403, corps vide, mêmes en-têtes), et sa raison va au journal applicatif. Un rappel accepté est déposé dans la nouvelle file `paiements.rappels` et la réponse part aussitôt : la vue ne lit aucune table de paiement, la réponse est donc la même qu'une transaction existe ou non. Chaque réception a sa propre clé dans la boîte d'envoi : un rappel réémis trois fois arrive trois fois au worker, dont l'idempotence est l'objet de T-85. Sans secret ni plage configurés, tout est refusé ; le simulateur n'existe que si les réglages l'activent (poste de développement), et `prod.py` le coupe.

Choix faits, à confirmer :

- l'adresse source est celle de la connexion ; l'en-tête X-Forwarded-For n'est cru que venant d'un frontal déclaré dans `PROXIES_DE_CONFIANCE`, et seule sa dernière adresse compte ;
- soixante rappels par minute et par adresse source (`PAIEMENTS_RAPPELS_PAR_MINUTE`) ;
- un fournisseur inconnu reçoit le même 403 que les autres refus, plutôt qu'un 404.

Prérequis de déploiement : la file `paiements.rappels` (déclarée par `declarer_files`), les variables `PAIEMENTS_SIMULE_SECRET` et `PAIEMENTS_SIMULE_PLAGES` hors production.

## T-85 · Vérification et crédit

**Produit** L'interrogation de l'API de statut du fournisseur avant tout crédit, l'unicité sur fournisseur et référence de transaction, et l'écriture ACHAT au registre.

**Critères** Un rappel forgé avec une référence inexistante ne crédite rien. Le même rappel reçu trois fois ne crédite qu'une fois. Un rappel arrivé après expiration du délai, mais confirmé par l'API de statut, crédite quand même.

**Fait.** Table `paiements_transaction` (cloisonnée par client, migration dédiée dans `core`, couverte par `verifier_base`), unicité sur `(fournisseur, reference_transaction)`. Le worker `workers.paiements` consomme `paiements.rappels` et appelle `paiements.services.verifier` : l'API de statut du fournisseur est interrogée hors de toute transaction, puis la ligne de la transaction est verrouillée et l'état appliqué selon une table de transitions (EXPIRE peut passer à CONFIRME ; CONFIRME et ECHOUE ne reviennent pas en arrière). Seul un statut CONFIRME, sur une transaction connue, au montant TTC attendu, écrit l'ACHAT des crédits HT au registre, avec la référence `paiement:<id>`, et publie `paiements.transaction.confirmee`. Une référence inconnue ne crédite rien, même payée chez le fournisseur. Un montant différent ou un fournisseur qui confirme un paiement échoué ne crédite rien et lève une alerte (journal en erreur et événement `paiements.transaction.anomalie`). Une erreur du fournisseur renvoie le rappel au palier de réessai. Les tests rejouent le même rappel trois fois de suite, puis trois fois en même temps dans trois transactions réelles : un seul crédit.

Choix faits, à confirmer :

- un paiement ECHOUE ne se reprend pas, même confirmé ensuite par le fournisseur : il alerte, pour instruction par un agent ;
- l'API de statut du fournisseur simulé lit ce que le simulateur a fixé dans Redis, puisqu'il imite un système extérieur.

Prérequis de déploiement : un processus `python -m workers.paiements`.

## T-86 · Parcours de rechargement

**Produit** Le choix du moyen, la redirection vers l'opérateur, la page d'attente qui interroge le statut, et les pages de résultat en cas de succès comme d'échec.

**Critères** L'utilisateur n'est jamais laissé sur une page qui tourne indéfiniment : au bout du délai, la page annonce l'état réel et propose une suite. Un paiement confirmé pendant que l'utilisateur a fermé son navigateur crédite quand même et notifie par courriel.

**Fait.** Écrans minimaux, en attendant la refonte de l'interface. `/paiements/recharger` (permission `credits.recharger`) prend un montant de crédits HT et un moyen de paiement ; `paiements.services.initier` vérifie le pas et les bornes, écrit la transaction INITIE avec son échéance, ouvre le paiement chez le fournisseur hors de toute transaction, puis la passe EN_ATTENTE avec la référence rendue, et l'utilisateur est envoyé chez le fournisseur. Au retour, `/paiements/<id>` et son fragment `/paiements/<id>/fragments/statut` (permission `credits.consulter`) interrogent l'état toutes les deux secondes tant qu'il peut bouger ; à l'échéance, la sonde s'arrête et la page annonce l'état réel (confirmé, refusé, ou toujours sans confirmation, avec ce que cela veut dire pour l'argent) et propose une suite. L'ordonnanceur appelle `paiements.services.relancer` chaque minute : chaque paiement en attente est revérifié auprès de son fournisseur, rappel reçu ou non, puis ce qui attend encore au-delà de l'échéance passe EXPIRE (un rappel tardif confirmé le reprend, T-85). Tous les quarts d'heure, la même relance revoit aussi les paiements expirés depuis moins d'un jour : payé après le délai et sans rappel, un paiement serait sinon perdu pour le client. À la confirmation, un courriel part à la personne qui a lancé le paiement, au commit, que son navigateur soit ouvert ou non. Le fournisseur simulé a sa page (`/paiements/simulateur/<référence>`, absente quand le simulateur est coupé) : payer, payer avec un rappel perdu, ou refuser. Les réglages (TVA active, taux, bornes, délai) sont une ligne unique `paiements_reglage`, lisible de tous, écrite par le back-office.

Choix faits, à confirmer :

- recharger est réservé à l'administrateur du compte ; suivre un paiement est ouvert à tout membre ;
- délai d'attente de quinze minutes avant de déclarer un paiement sans confirmation ;
- relance de chaque paiement en attente toutes les minutes, deux cents au plus par tour ;
- reprise des paiements expirés pendant un jour, tous les quarts d'heure ; au-delà, un agent instruit la réclamation ;
- tant que l'utilisateur n'a pas fixé de bornes, aucun minimum ni maximum n'est appliqué, seul le pas de 50 FCFA HT.

## T-87 · Paiements manuels

**Produit** La saisie d'un virement ou d'un versement en espèces par un administrateur, avec justificatif joint, motif et identité de l'agent portés sur l'écriture.

**Critères** Aucune saisie manuelle sans justificatif téléversé. L'écriture au registre porte l'agent. Le journal d'audit enregistre l'opération avec le montant.

**Fait.** Au back-office, la liste des paiements est en lecture seule et son bouton d'ajout ouvre la saisie d'un paiement manuel : client, moyen (virement ou espèces), référence, crédits HT, motif et justificatif. `paiements.services.saisir_manuel` refuse une saisie sans justificatif, sans motif, sans référence, hors du pas d'achat, ou déjà faite sous la même référence ; il dépose le justificatif (famille PAIEMENT, PDF, JPEG ou PNG, chiffré), écrit la transaction CONFIRME avec l'agent et le motif, puis l'ACHAT au registre par `credits.acheter`, qui porte désormais aussi le motif, avec l'agent pour auteur, et trace l'opération au journal d'audit (`paiements.manuel.saisi`, crédits et montant TTC). Une contrainte de la base refuse un virement ou des espèces sans justificatif, agent ni motif.

Choix faits, à confirmer :

- l'agent saisit les crédits HT, et le montant payé TTC s'en déduit au taux en vigueur ; un virement dont le montant ne tombe pas sur le pas d'achat se saisit au multiple inférieur, l'écart se traite à part ;
- les bornes du rechargement en ligne ne s'appliquent pas à un paiement manuel, le pas d'achat si ;
- la référence (du virement, ou numéro du reçu d'espèces) est obligatoire : c'est elle qui empêche de saisir deux fois le même paiement.

## T-88 · Facturation

**Produit** L'émission de la facture normalisée à chaque achat de crédits, la numérotation séquentielle par année, le reçu téléchargeable, et l'export comptable.

**Critères** La numérotation ne présente aucun trou ni doublon, y compris si deux factures sont émises simultanément. Les mentions obligatoires sont présentes et vérifiées par un test sur le contenu du PDF. Une facture émise n'est jamais modifiée, une correction passe par un avoir.

**Fait.** Table `paiements_facture` (cloisonnée par client) : une facture par achat confirmé, en ligne ou manuel, et des avoirs qui la corrigent. L'émission ne retarde jamais un crédit : l'ordonnanceur, chaque minute après la relance des paiements, facture les achats confirmés qui n'en ont pas, dans l'ordre de leur confirmation, puis archive les PDF manquants hors de tout verrou. Le numéro (`F-2026-000001`, `AV-2026-000001` pour les avoirs) sort d'un compteur par série et par année, `paiements_numerotation`, verrouillé dans la transaction même qui écrit la facture : un échec rend son numéro, deux émissions simultanées s'attendent. Le test lance cinq émissions dans cinq transactions réelles et obtient 1 à 5. Tout le contenu (identité de l'émetteur et du client, ligne, montants HT, TVA et TTC pris sur le paiement, jamais sur les réglages du jour, référence et date du paiement) est figé à l'émission ; un déclencheur refuse toute modification et toute suppression, sauf de rattacher une fois le PDF archivé. Un avoir, partiel ou total, porte l'agent et le motif, se trace à l'audit, et la somme des avoirs ne dépasse jamais la facture. Le PDF s'écrit sans bibliothèque extérieure (polices de base en WinAnsi, qui couvrent le français, œ et l'apostrophe typographique) ; le test en extrait le texte avec `pypdf`, dépendance de développement seulement, et y cherche chaque mention. Le client liste ses factures (`/paiements/factures`) et télécharge chacune en PDF ; le back-office les liste en lecture seule et émet un avoir depuis une facture ; `manage.py exporter_factures --debut --fin` sort l'export comptable en CSV, période obligatoire, pagination par curseur.

Tant que l'identité de l'émetteur (`FACTURATION_RAISON_SOCIALE`, `FACTURATION_RCCM`, `FACTURATION_IFU`, `FACTURATION_REGIME_FISCAL`, `FACTURATION_ADRESSE`) n'est pas renseignée, aucune facture n'est émise : les achats sont crédités et attendent leur facture, émise dans l'ordre dès que l'identité est complète.

Choix faits, à confirmer :

- la facture tient lieu de reçu : elle est émise acquittée, avec la date, le moyen et la référence du paiement ;
- deux séries numérotées à part, F pour les factures et AV pour les avoirs, sur six chiffres, remises à un chaque année ;
- la date de facture est celle de l'émission, la date du paiement figurant à part ;
- sans TVA, la facture porte « TVA non appliquée » : la formule légale est à confirmer ;
- la TVA d'un avoir partiel est arrondie au franc inférieur ; le dernier avoir d'une facture prend le reste exact (changé en T-89, pour qu'un remboursement au franc près reste possible).

Point ouvert, à trancher avec l'utilisateur : les mentions obligatoires d'une facture au Burkina Faso, le format de numérotation, et l'éventuelle obligation de certification ou de transmission des factures à l'administration fiscale (revue d'avant-démarrage). Rien n'est inventé sur ce point : le mécanisme est prêt, et l'émission reste coupée tant que l'identité de l'émetteur est vide.

## T-89 · Export des données et clôture de compte

**Produit** L'export des répertoires, du journal et des factures à la demande du client, et la procédure de clôture : arrêt des envois, conservation des pièces comptables, suppression ou anonymisation des contacts, sort du solde.

**Critères** L'export s'obtient sans demander quoi que ce soit au support. Une clôture laisse les pièces comptables intactes et rend les contacts irrécupérables au terme du délai. Le sort du solde suit la règle écrite aux conditions générales, et le test échoue si cette règle n'est pas encore tranchée.

**Fait.** Export : la page « Mes données » (`/compte/donnees`) mène à l'export du répertoire (existant, par courriel), à l'export du journal d'envois en CSV (`/journal/export`, période obligatoire d'un an au plus, pagination par curseur, client posé par le générateur lui-même) et aux factures. Clôture, au back-office, depuis la fiche du client : `backoffice.services.cloturer_compte` clôt le compte (`comptes.services.clore` : état CLOTURE, date, membres désactivés, trace à l'audit), puis annule chaque campagne en cours ou programmée, ce qui libère ses réservations ; un compte qui n'est plus actif ne peut plus ni confirmer une campagne ni envoyer un test. Le remboursement se saisit à part, sur demande écrite : `credits.part_remboursable` applique la règle tranchée (offerts consommés d'abord, seule la part payée et non consommée se rembourse, un ajustement corrige une consommation) ; `paiements.rembourser_cloture` émet les avoirs, sur les factures les plus récentes d'abord, puis solde le compte par deux ajustements, le remboursement et les crédits offerts perdus. L'ordonnanceur, une fois par jour, efface les contacts des comptes clos depuis trente jours (contacts, listes, segments, champs, imports et exports, et leurs fichiers dans le stockage) ; restent la liste noire, les consentements, les factures, les justificatifs, le registre et l'audit. Les tests suivent la règle du solde pas à pas, le remboursement en deux avoirs, et l'effacement à 29 puis à 31 jours.

Choix faits, à confirmer :

- le remboursement exige que les factures des achats soient émises : tant que l'identité de l'émetteur manque, il est refusé ;
- la TVA d'un avoir partiel est arrondie au franc inférieur, le dernier avoir d'une facture reprenant le reste exact ;
- la liste noire du compte est gardée après l'effacement : elle prouve qu'un abonné s'est opposé aux envois ;
- les messages du journal suivent la durée de rétention du journal, encore à trancher (lot 6), et ne sont pas effacés à la clôture ;
- le remboursement attend que les réservations des campagnes annulées soient libérées.

## T-90 · Point de contrôle du lot

**Produit** Un cycle complet : rechargement par mobile money, crédit constaté, campagne consommant une partie du solde, échecs libérant des réservations, facture émise, rapprochement quotidien sans écart.

**Critères** Le solde final égale le solde initial plus l'achat moins la consommation réelle, au franc près. La facture correspond à l'achat. Le rapprochement ne lève aucune alerte. Un rejeu du rappel de paiement ne crédite pas une seconde fois.
