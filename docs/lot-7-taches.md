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
- Reste à poser à l'utilisateur en ouvrant T-89 : quels crédits se consomment
  en premier, les payés ou les offerts. La réponse fixe le montant remboursable
  à la clôture ; les deux se calculent par sommes par type, sans colonne de plus.

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

**Fait.** Le verrou de la ligne `credits_compte` (T-09) protège déjà la réservation ; T-79 y ajoute la gestion de l'attente. Une réservation n'attend le verrou que dix secondes (`ATTENTE_RESERVATION_MS`, par un `lock_timeout` local rendu ensuite à sa valeur) ; au-delà, elle est refusée par `SoldeOccupe`, que la campagne traduit en « Le solde est occupé par une autre opération en cours. Réessayez dans un instant. ». Les opérations des workers (achat, règlement, libération) attendent leur tour sans borne. À la confirmation, la réservation passe après l'insertion des destinataires, pour que le verrou du solde ne soit tenu qu'en fin de transaction ; un refus annule tout, destinataires compris. Les tests confirment deux campagnes dans deux fils, chacun sur sa connexion : l'une passe, l'autre reçoit « Solde insuffisant : 30 FCFA disponibles, 60 nécessaires. », reste en brouillon sans destinataire ni réservation, et le cache égale le solde recalculé.

Choix fait, à confirmer : le délai de dix secondes, assez long pour une confirmation voisine qui insère des milliers de destinataires.

## T-80 · Rapprochement quotidien du registre

**Produit** Le traitement qui compare le solde recalculé au cache pour chaque client, et l'alerte sur tout écart.

**Critères** Un écart introduit artificiellement d'un franc déclenche l'alerte. Le traitement passe sur mille comptes en moins d'une minute. L'alerte nomme le client et le montant de l'écart.

## T-81 · Grilles tarifaires datées

**Produit** Les grilles, leurs versions avec dates de prise d'effet et de fin, les paliers de volume, le prix par réseau et par pays, et la contrainte d'exclusion qui interdit le chevauchement.

**Critères** Deux versions qui se chevauchent sont refusées par la base. Une grille sans version publiée n'est jamais appliquée. Le prix appliqué à un message est celui en vigueur à sa date de soumission, ce qu'un test vérifie en changeant la grille entre deux envois.

## T-82 · Calcul du coût et gel sur le message

**Produit** Le calcul à la soumission, tenant compte du palier de volume, du réseau et du nombre de segments, avec le coût figé sur la ligne du message.

**Critères** Un message soumis avant un changement de grille conserve son coût d'origine, y compris quand son accusé arrive après. Le passage d'un palier de volume en cours de campagne est traité de façon documentée et le rapport de campagne l'explique.

## T-83 · Grilles négociées et promotionnelles

**Produit** Les grilles propres à un client, les grilles à durée limitée, et la règle de priorité entre grille client, grille promotionnelle et grille publique.

**Critères** La règle de priorité est écrite et testée sur les six combinaisons possibles. Un client sans grille négociée tombe sur la grille publique. La modification d'une grille laisse une écriture au journal d'audit avec l'ancien et le nouveau prix.

## T-84 · Point d'entrée des rappels de paiement

**Produit** La route publique par fournisseur, la liste blanche d'adresses, la vérification de signature et la limite de débit par source.

**Critères** Un rappel non signé est rejeté sans réponse détaillée. Un rappel venu d'une adresse hors liste blanche est rejeté et journalisé. Un client malveillant qui sonde la route n'apprend rien sur l'existence d'une transaction.

## T-85 · Vérification et crédit

**Produit** L'interrogation de l'API de statut du fournisseur avant tout crédit, l'unicité sur fournisseur et référence de transaction, et l'écriture ACHAT au registre.

**Critères** Un rappel forgé avec une référence inexistante ne crédite rien. Le même rappel reçu trois fois ne crédite qu'une fois. Un rappel arrivé après expiration du délai, mais confirmé par l'API de statut, crédite quand même.

## T-86 · Parcours de rechargement

**Produit** Le choix du moyen, la redirection vers l'opérateur, la page d'attente qui interroge le statut, et les pages de résultat en cas de succès comme d'échec.

**Critères** L'utilisateur n'est jamais laissé sur une page qui tourne indéfiniment : au bout du délai, la page annonce l'état réel et propose une suite. Un paiement confirmé pendant que l'utilisateur a fermé son navigateur crédite quand même et notifie par courriel.

## T-87 · Paiements manuels

**Produit** La saisie d'un virement ou d'un versement en espèces par un administrateur, avec justificatif joint, motif et identité de l'agent portés sur l'écriture.

**Critères** Aucune saisie manuelle sans justificatif téléversé. L'écriture au registre porte l'agent. Le journal d'audit enregistre l'opération avec le montant.

## T-88 · Facturation

**Produit** L'émission de la facture normalisée à chaque achat de crédits, la numérotation séquentielle par année, le reçu téléchargeable, et l'export comptable.

**Critères** La numérotation ne présente aucun trou ni doublon, y compris si deux factures sont émises simultanément. Les mentions obligatoires sont présentes et vérifiées par un test sur le contenu du PDF. Une facture émise n'est jamais modifiée, une correction passe par un avoir.

## T-89 · Export des données et clôture de compte

**Produit** L'export des répertoires, du journal et des factures à la demande du client, et la procédure de clôture : arrêt des envois, conservation des pièces comptables, suppression ou anonymisation des contacts, sort du solde.

**Critères** L'export s'obtient sans demander quoi que ce soit au support. Une clôture laisse les pièces comptables intactes et rend les contacts irrécupérables au terme du délai. Le sort du solde suit la règle écrite aux conditions générales, et le test échoue si cette règle n'est pas encore tranchée.

## T-90 · Point de contrôle du lot

**Produit** Un cycle complet : rechargement par mobile money, crédit constaté, campagne consommant une partie du solde, échecs libérant des réservations, facture émise, rapprochement quotidien sans écart.

**Critères** Le solde final égale le solde initial plus l'achat moins la consommation réelle, au franc près. La facture correspond à l'achat. Le rapprochement ne lève aucune alerte. Un rejeu du rappel de paiement ne crédite pas une seconde fois.
