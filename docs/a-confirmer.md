# Choix en attente de confirmation, lots 7 à 9

Ce registre rassemble, pour l'utilisateur, les choix faits pendant les lots 7
à 9 sans lui avoir été posés. Chacun reste à confirmer ou à corriger : rien ici
n'est une décision de l'utilisateur. La source de chaque point est la section
de la tâche dans `docs/lot-N-taches.md`, qui fait foi ; ce fichier ne fait que
les regrouper. Quand le lot n'écrit pas de justification, la ligne le dit, rien
n'est ajouté après coup.

Établi le 8 novembre 2026. Les décisions déjà prises par l'utilisateur (en tête
de chaque lot, ou marquées comme telles dans une tâche) n'y figurent pas.

## En tête : point de sécurité

**T-107, quotas, mode permissif quand Redis tombe** (`docs/lot-9-taches.md`).

- Choix retenu : si Redis est indisponible, l'appel à l'API est servi sans
  quota ni en-têtes de quota ; l'erreur s'écrit au journal applicatif et
  l'événement `api.quota.permissif` part à l'exploitation, une fois par minute
  et par processus au plus. Le plafond du compte, lui aussi dans Redis, tombe
  avec.
- Justification écrite : ce qui part reste borné par le crédit du compte ;
  couper toute l'API pour une panne de cache coûterait plus cher à tous les
  clients que le risque d'une heure sans quota.
- Pourquoi c'est un point de sécurité : pendant la panne, une clé volée ou un
  intégrateur en boucle n'est plus freiné que par le solde du compte, et le
  plafond qui déclenche la suspension automatique (T-101) ne compte plus rien.
  Les deux voies sont défendables (servir ou refuser) ; le choix revient à
  l'utilisateur.

**Redis indisponible, deux comportements opposés à mettre côte à côte.**

| Chemin | Redis indisponible | Effet |
|---|---|---|
| Quotas et plafond de l'API (T-107) | ouvert : l'appel est servi sans quota | l'API continue de servir, sans frein autre que le solde |
| Connexion à l'interface (verrouillage par courriel) | fermé : l'erreur remonte | aucune nouvelle connexion, agents du back-office compris |
| Second facteur (compteur des codes refusés) | fermé : l'erreur remonte | aucun code vérifié, donc aucune session ne passe le second facteur |

Les sessions vivent en base, pas dans Redis : une session déjà ouverte et
déjà vérifiée continue de servir pendant la panne. Ce sont les nouvelles
connexions qui sont refusées. Le refus fermé est le choix sûr pour la
connexion et le second facteur ; mais pendant une panne de Redis, l'API
reste ouverte quand l'interface ne l'est plus, et les agents qui devraient
intervenir ne peuvent pas se connecter. Une règle commune est à décider par
l'utilisateur. Relevé par la session responsable en l'absence de
l'utilisateur, sans changement de code.

## Points ouverts, à trancher par l'utilisateur

Ce ne sont pas des choix faits, mais des décisions qui manquent.

- **T-88, factures** : mentions obligatoires d'une facture au Burkina Faso,
  format de numérotation, obligation éventuelle de certification ou de
  transmission à l'administration fiscale (DGI). L'émission reste coupée tant
  que l'identité de l'émetteur (raison sociale, RCCM, IFU, régime fiscal,
  adresse) n'est pas renseignée. La formule à porter sans TVA (« TVA non
  appliquée ») est à valider avec le comptable.
- **T-103, page d'état** : l'hébergeur statique, séparé de la plateforme, et
  son compte. La page reste en `noindex` tant qu'elle n'a pas son domaine.
- **T-89, clôture** : la durée de rétention du journal des messages, laissée à
  trancher depuis le lot 6.
- **T-110, documentation** : le troisième critère (un intégrateur réussit un
  premier envoi en bac à sable sans poser de question) se vérifie avec une
  personne qui ne connaît pas le projet.
- **T-97, connecteurs** : la recopie automatique vers Jasmin de l'hôte, de
  l'identifiant et du secret saisis au back-office (lot 10 ou mise en
  production).
- **Lot 1, journal d'audit** (relevé par l'audit d'avant-lancement du
  8 novembre 2026) : `core.Audit` porte deux `ForeignKey`, `auteur` vers
  `comptes.Utilisateur` et `client` vers `comptes.Client`, depuis le commit
  b4175d9 qui a déplacé l'audit dans `core`. Elles remontent d'une couche,
  `core` étant sous `comptes`. Correction possible, celle que la charte prévoit :
  deux UUID simples et indexés, la cohérence tenue par les services. Elle
  demande une migration de `core` sur une table que toutes les autres
  alimentent ; non corrigée, décidé par la session responsable en l'absence de
  l'utilisateur, à confirmer.
- **Tables sans cloisonnement par ligne** (même audit) : `comptes_code_secours`
  (codes de secours du second facteur, lot 3) et `core_evenement_traite`
  (traces d'idempotence des consommateurs de file) n'ont ni `client_id` ni
  politique. La première se lit par utilisateur, comme `comptes_utilisateur`
  que la charte laisse hors cloisonnement ; la seconde n'est écrite et lue que
  par les workers. À confirmer : les laisser ainsi, ou les réserver aux rôles
  qui s'en servent.

## Relecture de sécurité de l'API publique, 8 novembre 2026

Trois défauts certains y ont été relevés et se corrigent chacun par son commit
et son test : le rebond DNS à la livraison d'un webhook, la collision des clés
d'idempotence entre bac à sable et production, la réactivation d'un point au
delà du maximum. Le délai par opération réseau des webhooks devient une
échéance totale. Deux points restent ouverts, décidés par la session
responsable en l'absence de l'utilisateur, à confirmer :

- **Port libre sur l'adresse d'un webhook, risque résiduel.** Un point de
  réception peut viser n'importe quel port d'un hôte public. Depuis que la
  connexion part vers l'adresse vérifiée, le sondage ne peut plus viser que des
  hôtes publics ; restreindre les ports pourrait casser des intégrateurs.
  Option possible : 443 et 80 seulement en production.
- **Temps de réponse d'une clé au préfixe inconnu, accepté.** Une clé dont le
  préfixe n'existe pas est refusée plus vite qu'une clé au secret faux, la
  vérification Argon2 n'ayant pas lieu : l'écart révèle si un préfixe existe.
  Accepté sans changement, le préfixe n'étant pas secret (il s'affiche dans
  l'espace du compte).
- **Résolution DNS hors de l'échéance d'une livraison, risque résiduel.**
  L'échéance de dix secondes couvre la connexion et l'échange, pas la
  résolution du nom : `getaddrinfo` n'a pas de délai en Python, un résolveur
  lent retient encore le worker. Deux options : régler le résolveur du
  conteneur (`timeout` et `attempts` dans `resolv.conf`), ce qui revient à
  l'exploitation ; ou un fil d'exécution à délai autour de la résolution, qui
  laisserait des fils orphelins dans le worker. Pas de correctif, décidé par
  la session responsable en l'absence de l'utilisateur, à confirmer.

## Relecture des rôles du back-office, 8 novembre 2026

Le mode de règlement d'un client se modifiait par la fiche standard du client,
au support compris, sans motif ni trace : il y passe en lecture seule et ne
change plus que par `comptes.services.changer_mode_reglement`. Le palier et
les deux plafonds du client passent en lecture seule pour tous sauf le
super-administrateur, dont chaque modification va au journal d'audit avec
l'ancienne et la nouvelle valeur. Décidé par la session responsable en
l'absence de l'utilisateur, à confirmer, et reste à trancher :

- **Qui règle le palier et les plafonds.** La finance (palier) et
  l'exploitation (plafonds) n'ont aujourd'hui que la lecture des clients ;
  leur confier ces champs demande des permissions dédiées.
- **Motif obligatoire et service dédié** pour ces changements, comme pour le
  mode de règlement.

## Relecture de sécurité des rappels de paiement, 8 novembre 2026

Aucun défaut certain : seule la référence est prise dans le rappel, le statut
et le montant sont relus auprès du fournisseur, le crédit se fait sous le
verrou de la transaction, une fois. Deux points pour l'utilisateur, décidés
par la session responsable en l'absence de l'utilisateur, à confirmer :

- **Unicité de l'écriture ACHAT d'un paiement, défense en profondeur
  recommandée.** L'écriture `ACHAT` de référence `paiement:<id>` n'a pas de
  contrainte d'unicité en base, alors que `CONSOMMATION` en a une
  (`registre_consommation_unique_par_message`). Le crédit unique tient
  aujourd'hui par le verrou de la ligne `paiements_transaction` et son état.
  Une contrainte partielle sur le modèle de celle de `CONSOMMATION` fermerait
  la porte à un second chemin qui oublierait le verrou. Elle touche le
  registre de crédits : non faite.
- **Devise du statut, condition du branchement d'un vrai fournisseur.** Le
  statut rendu par l'API d'un fournisseur (`fournisseurs.Statut`) porte un
  montant mais pas de devise : tout est en FCFA avec le fournisseur simulé.
  Un vrai fournisseur devra rendre sa devise, et un statut dans une autre
  devise ne devra rien créditer.

## Relecture des exports et des imports, 8 novembre 2026

Deux correctifs, chacun avec un test vu échouer sur l'ancien code :

- **Cellules exécutables dans les exports CSV.** Un texte venu d'un tiers (SMS
  entrant, contact importé, raison sociale) qui commençait par `=`, `+`, `-`,
  `@`, une tabulation ou un retour chariot sortait tel quel, et devenait une
  formule chez qui ouvrait l'export dans un tableur. Une telle cellule sort
  désormais précédée d'une apostrophe (`apps/core/tableur.py`), sauf un nombre
  signé : les numéros normalisés (+22670123456) et les montants restent tels
  quels.
- **Archive bureautique sans borne une fois décompressée.** Un `.xlsx` ou un
  `.docx` de quelques kilo-octets pouvait se décompresser en plusieurs
  gigaoctets à la lecture. La somme des tailles déclarées des membres est
  bornée à 1 Go au dépôt ; la bibliothèque standard ne décompresse jamais un
  membre au-delà de sa taille déclarée.

Décidé par la session responsable en l'absence de l'utilisateur, à
confirmer :

- **Borne en nombre de lignes d'un import, et valeur de la borne de 1 Go.**
  Aucun import n'est borné en lignes : seuls les 200 Mo du fichier déposé et,
  désormais, le 1 Go décompressé le limitent. Les deux réglages sont à fixer
  ensemble par l'utilisateur, selon la taille de carnet qu'il veut accepter
  d'un client.
- **Apostrophe visible sur un numéro saisi comme texte.** Un numéro écrit avec
  des espaces dans un champ libre d'un contact (« +226 70 00 00 00 ») n'est pas
  un nombre signé : il sort précédé d'une apostrophe. Gêne de lecture
  acceptée. Les numéros normalisés, sans espace, ne sont pas touchés.

## Relecture de l'authentification, 8 novembre 2026

Deux correctifs, chacun avec un test vu échouer sur l'ancien code :

- **Contournement complet du second facteur.** La page d'activation est
  ouverte avant la présentation du code, et rien ne refusait un compte dont le
  second facteur était déjà actif. Avec le seul mot de passe, un intrus
  réglait son propre secret à la place de celui de la victime et recevait une
  session vérifiée. La page renvoie désormais vers la vérification, et le
  service refuse sous verrou.
- **Essais de code sans limite.** Le compteur des cinq codes refusés vivait
  dans la session : une reconnexion avec le bon mot de passe rendait cinq
  essais de plus. Il porte désormais sur l'utilisateur, dans Redis, sur le
  modèle du verrouillage de connexion.

Sain : jetons d'invitation et de vérification signés, à usage unique et à
durée bornée ; verrouillage par courriel sans révéler si un compte existe ;
rotation de la clé de session à la connexion et à la vérification ; code TOTP
comparé en temps constant, rejeu refusé ; codes de secours hachés et
consommés sous verrou.

Deux absences, à trancher par l'utilisateur :

- **Pas de parcours de mot de passe oublié, ni de changement de mot de
  passe.** Un utilisateur qui oublie le sien ne peut que demander une
  nouvelle invitation ; un mot de passe compromis ne se change pas.
- **Pas de freinage par adresse IP à la connexion.** Le verrouillage porte sur
  le courriel. Une même adresse peut essayer un mot de passe courant sur
  beaucoup de courriels différents (pulvérisation) sans être freinée.

## Relecture de la configuration de production, 8 novembre 2026

`manage.py check --deploy` lancé sur `prod.py` avec des variables factices,
aucun vrai secret. Un correctif, avec son test vu échouer sur l'ancien code :
sans `SMS_MOTEUR_COURRIEL`, les courriels partaient vers la console, donc au
journal avec les liens d'invitation et leurs jetons, et `PORTAIL_URL`
retombait sur le poste de développement. `prod.py` exige désormais un vrai
moteur d'envoi et une adresse en https, sinon le service refuse de démarrer.

Sain : `DEBUG` est faux dans `base.py` et `prod.py` ne le change pas ;
`ALLOWED_HOSTS` vient de l'environnement, sans valeur par défaut ; HSTS d'un
an avec les sous-domaines ; redirection vers https ; cookies de session et
CSRF en `Secure`, session en `HttpOnly` et `SameSite=Lax` ; `X-Frame-Options`
à `DENY`, `nosniff`, `Referrer-Policy` et `Cross-Origin-Opener-Policy` aux
valeurs par défaut de Django ; administration absente de l'espace des
clients ; point des métriques fermé sans jeton, jeton comparé en temps
constant ; clé de chiffrement absente : refus, jamais de secret en clair.

Points qui dépendent du déploiement réel, à trancher par l'utilisateur :

- **Préchargement HSTS** (seul avertissement restant de `check --deploy`).
  Inscrire le domaine dans la liste des navigateurs engage tous ses
  sous-domaines en https pour longtemps, et se défait mal.
- **Aucune politique de sécurité du contenu (CSP).** Django 6 la propose
  (`SECURE_CSP`). Il faut d'abord recenser les scripts en ligne des gabarits ;
  la version standard d'Alpine évalue ses expressions et demande soit
  `unsafe-eval`, soit sa version compatible CSP.
- **Redirection vers https et appels internes.** `SECURE_SSL_REDIRECT`
  renvoie en 301 toute requête en http : le relevé des métriques et le relais
  des accusés par Jasmin (`JASMIN_URL_RAPPEL`), s'ils passent en http sur le
  réseau interne, seraient redirigés, et un accusé non suivi serait perdu.
  Selon le mandataire inverse : appels internes en https, ou chemins exemptés
  (`SECURE_REDIRECT_EXEMPT`).
- **Mandataire inverse.** `SECURE_PROXY_SSL_HEADER` croit l'en-tête
  `X-Forwarded-Proto` : le mandataire doit l'écraser, jamais le laisser venir
  du client. S'il réécrit l'en-tête `Host`, il faudra `CSRF_TRUSTED_ORIGINS`
  avec le nom de domaine réel.

## Relecture du support, 8 novembre 2026

Un correctif, avec son test vu échouer sur l'ancien code : une adresse
d'ouverture de ticket au paramètre `campagne` ou `message` mal formé levait
une erreur 500 ; elle rend désormais une page introuvable.

Sain : pièces jointes reconnues au contenu (PDF, JPEG, PNG seulement, donc ni
SVG ni HTML), 5 Mo au plus, chiffrées au repos, rangées sous un chemin tiré au
hasard, le nom d'origine n'étant jamais un chemin ; aucun lien de
téléchargement côté client, lien signé de quinze minutes pour les seuls agents
qui traitent les tickets ; aucun contenu client rendu sans échappement, au
portail comme au back-office ; courriels de notification en texte seul ;
ticket, réponse et fragment HTMX lus sur le compte, cloisonnement en plus ;
notes internes absentes du fil du client et des courriels ; verrou du ticket
limité à sa ligne (correctif de T-102).

Décidé par la session responsable en l'absence de l'utilisateur, à
confirmer :

- **Pièces jointes servies en ligne aux agents, option recommandée.** Le lien
  signé de `fichiers.lien` ouvre le fichier dans le navigateur. Le risque est
  faible : type contrôlé au contenu, origine du stockage distincte de celle du
  back-office. Recommandé : demander au stockage un téléchargement
  (`response-content-disposition=attachment`) ; non fait aujourd'hui, la
  fonction servant aussi à d'autres écrans dont les appelants restent à
  relire. Vérifier en préproduction que le stockage répond avec
  `X-Content-Type-Options: nosniff`.
- **Taille des envois contrôlée après réception.** La borne de 5 Mo est
  vérifiée une fois tout le fichier reçu par Django. Le mandataire inverse
  doit borner la taille des requêtes.

## Relecture des notifications, 8 novembre 2026

Un correctif, décidé en cours de relecture. Le module
`notifications` prévu (file `courriels.sortants`, prestataire, modèles
versionnés, journal des envois) n'existe pas encore : les courriels partent
en direct par `apps.core.courriels.envoyer`, en texte seul, après la
validation de la transaction. Aucune notification ne part par SMS. Six
appelants relus : invitation et vérification d'adresse, réinitialisation du
second facteur, support, confirmation de paiement, export de contacts,
désactivation d'un point de réception de webhook.

Sain : chaque courriel part vers un membre du compte concerné, jamais vers
une adresse saisie ; aucun ne porte de donnée d'un autre compte. Les workers
passent par le rôle `worker`, qui ignore le cloisonnement par construction ;
chaque requête relue y filtre explicitement par client (contacts exportés,
points de réception, membres prévenus).

**Défaut certain, corrigé (07d787f), décidé par la session responsable en
l'absence de l'utilisateur, à confirmer.** Un membre retiré du compte
(`retirer_membre`, ou clôture du compte) continuait de recevoir des
courriels, faute de relire `is_active` au moment de l'envoi :

- **Support** : le demandeur retiré recevait chaque réponse d'un agent avec
  tout le fil public du ticket, les rappels et la clôture.
- **Export de contacts** : retiré entre sa demande et la production par le
  worker, il recevait quand même le lien vers tout le répertoire exporté.
- **Paiement** : la confirmation partait à l'initiateur retiré.

Le destinataire est désormais relu au moment de l'envoi
(`apps/comptes/selectors.py`). Support et paiement : au membre s'il est
encore actif dans ce compte, sinon aux administrateurs actifs, sur le modèle
de la désactivation d'un point de réception. Export : au seul demandeur
encore actif, sinon à personne ; aucun lien vers le répertoire ne part à qui
ne l'a pas demandé. Un test par appelant, vu échouer sur l'ancien code.

À trancher par l'utilisateur :

- **Lien d'export valable sept jours, envoyé par courriel.** C'est un lien
  signé vers le stockage, sans connexion : quiconque obtient le courriel
  télécharge tout le répertoire de contacts du compte. Deux options, qui
  touchent à l'usage : un lien vers le portail, qui exige la connexion et
  sert le fichier après contrôle des droits, ou une durée courte (24 heures
  par exemple).

## Lot 7, crédits, tarifs, paiements, facturation

**Ouverture du lot**

- Le taux de TVA se lit en base, pas dans le code. Justification non écrite.
- Un envoi hors campagne prend le palier atteint à sa soumission.
  Justification non écrite.
- Sens des types d'écriture : ACHAT est un crédit payé et seulement cela, y
  compris un paiement manuel ; RECREDIT est un crédit offert ; AJUSTEMENT
  corrige une erreur et n'offre jamais rien. Justification : la règle de
  remboursement à la clôture exige de tenir ces sommes distinctes.

**T-78, réservation et règlement**

- Le règlement se fait par balayage de l'ordonnanceur, pas à l'accusé.
  Justification : `journal` et `credits` sont de la même couche et ne
  s'importent pas, et un verrou du compte à chaque accusé ralentirait leur
  traitement ; le montant est déjà réservé, le client n'y perd rien.
- La consommation d'une campagne s'écrit en une fois, à sa conclusion. Le suivi
  en direct annonce le coût émis, pas le coût facturé. Justification non
  écrite au-delà de ce constat.
- Aucun écran ne change le mode de règlement d'un client ; le service existe.
  Justification : l'écran viendra avec la refonte de l'interface.

**T-79, concurrence sur le solde**

- Une réservation attend le verrou du solde dix secondes au plus.
  Justification : assez long pour une confirmation voisine qui insère des
  milliers de destinataires.

**T-80, rapprochement quotidien**

- Le rapprochement tourne toutes les vingt-quatre heures depuis le démarrage de
  l'ordonnanceur, pas à heure fixe ; un redémarrage le relance et peut répéter
  une alerte. Justification non écrite.
- L'alerte est une ligne d'erreur et un événement ; son destinataire relève du
  module des notifications. Justification : ce module n'est pas encore écrit.

**T-81, grilles datées**

- Un brouillon peut recouvrir une version publiée. Justification : sinon aucune
  version future ne se prépare tant que la version en vigueur n'a pas de fin.
- Le palier atteint s'applique à tout le segment, pas par tranches.
  Justification non écrite.
- La diffusion lit encore la grille message par message ; T-82 fige le prix à
  la confirmation. Justification : décision de T-82.

**T-82, calcul du coût**

- Le volume du mois compte les SMS soumis, un message long pour un, sans les
  envois hors campagne. Justification : `journal_compteur` ne les portait pas.
- Le mois civil suit l'heure de Ouagadougou. Justification non écrite.
- Une campagne confirmée avant le changement, sans tarif figé, prend le prix du
  moment à la diffusion. Justification non écrite.

**T-83, grilles négociées et promotions**

- La grille publique n'entre pas dans la comparaison : un prix négocié plus
  haut que le public reste le prix du client. Justification non écrite.
- À prix égal, la négociée l'emporte sur la promotion. Justification non
  écrite.
- Une grille négociée ou une promotion peut ne tarifer que certains réseaux ;
  les autres suivent la publique. Justification non écrite.
- Le palier se lit dans chaque grille avec le même volume du mois.
  Justification non écrite.

**T-84, rappels de paiement**

- L'adresse source est celle de la connexion ; `X-Forwarded-For` n'est cru que
  venant d'un frontal déclaré dans `PROXIES_DE_CONFIANCE`, et seule sa dernière
  adresse compte. Justification non écrite.
- Soixante rappels par minute et par adresse source. Justification non écrite.
- Un fournisseur inconnu reçoit le même 403 que les autres refus, pas un 404.
  Justification non écrite.

**T-85, vérification et crédit**

- Un paiement ECHOUE ne se reprend pas, même confirmé ensuite par le
  fournisseur : il alerte, pour instruction par un agent. Justification non
  écrite.
- Le fournisseur simulé lit son statut dans Redis. Justification : il imite un
  système extérieur.

**T-86, rechargement**

- Recharger est réservé à l'administrateur du compte ; suivre un paiement est
  ouvert à tout membre. Justification non écrite.
- Quinze minutes avant de déclarer un paiement sans confirmation ; relance de
  chaque paiement en attente toutes les minutes, deux cents au plus par tour ;
  reprise des paiements expirés pendant un jour, tous les quarts d'heure.
  Justification non écrite.
- Aucun minimum ni maximum, seul le pas de 50 FCFA HT. Justification :
  l'utilisateur n'a pas fixé de bornes.

**T-87, paiements manuels**

- L'agent saisit les crédits HT, le TTC s'en déduit ; un virement qui ne tombe
  pas sur le pas se saisit au multiple inférieur. Justification non écrite.
- Les bornes du rechargement en ligne ne s'appliquent pas, le pas d'achat si.
  Justification non écrite.
- La référence est obligatoire. Justification : elle empêche de saisir deux
  fois le même paiement.

**T-88, facturation**

- La facture tient lieu de reçu, émise acquittée avec la date, le moyen et la
  référence du paiement. Justification non écrite.
- Deux séries, F et AV, sur six chiffres, remises à un chaque année.
  Justification non écrite ; voir les points ouverts.
- La date de facture est celle de l'émission, la date du paiement à part.
  Justification non écrite.
- La TVA d'un avoir partiel est arrondie au franc inférieur, le dernier avoir
  prend le reste exact. Justification : un remboursement au franc près reste
  possible.

**T-89, export et clôture**

- Le remboursement exige que les factures des achats soient émises.
  Justification non écrite ; le remboursement s'émet en avoirs sur ces
  factures.
- La liste noire du compte est gardée après l'effacement. Justification : elle
  prouve qu'un abonné s'est opposé aux envois.
- Les messages du journal ne sont pas effacés à la clôture. Justification : ils
  suivent la rétention du journal, encore à trancher.
- Le remboursement attend que les réservations des campagnes annulées soient
  libérées. Justification non écrite.

## Lot 8, tableaux de bord, back-office, anti-fraude, support

**Rôles d'agent**

- La suspension revient au support ; la finance et l'exploitation voient les
  fiches clients en lecture ; la clôture revient au support, le remboursement à
  la finance ; le super-administrateur ne se donne pas par l'écran des rôles.
  Justification non écrite.

**T-91, compteurs pré-agrégés**

- Un écart se signale mais ne se corrige que sur demande (`--corriger`).
  Justification : une anomalie ne disparaît pas sans avoir été vue.

**T-92, tableau de bord client**

- Taux de livraison sur les messages dont le sort est connu. Justification :
  une campagne en cours ne doit pas paraître mauvaise.
- Latence de la soumission à l'accusé de livraison, sur les livrés.
  Justification non écrite.
- Désinscriptions : les STOP reçus par SMS seulement. Justification non
  écrite.
- Les envois hors campagne entrent au tableau de bord mais pas au palier des
  grilles. Justification : les y compter changerait le prix.
- Rafraîchissement toutes les dix secondes. Justification : ce que prévoit
  l'architecture pour un tableau de bord ordinaire.

**T-93, recherche dans le journal**

- Bascule en export au-delà de 31 jours de plage. Justification : compter les
  résultats demanderait de lire le journal.
- Navigation vers le passé, avec retour au début, sans page précédente.
  Justification non écrite.
- Les tests de plan désactivent le balayage séquentiel. Justification : voir le
  plan d'une table de production.

**T-94, fiche message**

- L'adresse de la fiche porte la date de réception. Justification non écrite.
- Le diagnostic ne dit rien de la facturation d'un non livré. Justification :
  elle dépend du mode de règlement du compte.

**T-95, gestion des clients**

- Les membres d'un compte suspendu peuvent encore se connecter, consulter et
  exporter ; seuls les envois s'arrêtent. Justification non écrite.
- Une campagne programmée dont l'heure passe pendant la suspension part à la
  réactivation, en retard. Justification non écrite.
- Les messages déjà soumis finissent leur chemin et se facturent.
  Justification non écrite.

**T-96, supervision des liaisons**

- Supervision muette à une minute ; tendance des files sur cinq minutes.
  Justification non écrite.

**T-97, connecteurs**

- Les identifiants saisis sont rangés en base, Jasmin reste configuré par
  `scripts/configurer_jasmin.py`. Justification : la recopie est à décider
  (voir les points ouverts).
- `seuil_bascule` à 85 % par défaut. Justification : le même que le seuil de
  santé.
- Un opérateur qui a des routes n'utilise qu'elles, sans secours implicite.
  Justification non écrite.

**T-98, grilles au back-office**

- La commande `publier_grille` garde la publication sans date pour le premier
  chargement d'une grille ; l'écran exige toujours la date. Justification non
  écrite.

**T-99, dictionnaire anti-spam**

- Le dictionnaire revient à l'exploitation. Justification : avec
  l'anti-fraude.
- Une expression peut se trouver à cheval sur deux mots. Justification : prix
  du refus des contournements par espaces.
- Les envois unitaires et l'API ne passent pas au dictionnaire. Justification :
  il porte sur la composition des campagnes.

**T-100, trafic artificiel**

- Seuils initiaux : contigus 1 000 et 10 000 ; codes 20 et 100 sur 60 minutes ;
  livraison sous 50 % et sous 20 %, sur 60 minutes et 50 accusés au moins.
  Justification non écrite.
- Une plage est un bloc de dix mille numéros ; un code, quatre à huit chiffres
  d'affilée hors campagne. Justification non écrite.
- L'exploitation est prévenue par l'écran et l'événement, le courriel reste à
  brancher. Justification : module des notifications.
- Les messages en file vers une plage bloquée sont refusés sans frais.
  Justification non écrite.

**T-101, plafonnement et suspension automatique**

- Les campagnes ne comptent pas dans le plafond. Justification : confirmées
  chiffres lus et bornées par la réservation de leur coût.
- Les essais refusés comptent. Justification : un client qui insiste atteint
  le seuil critique.
- Alerte au premier refus, critique à 200 % du plafond, réglables.
  Justification non écrite.
- C'est le compte qui est suspendu, pas une clé. Justification : les clés
  sont arrivées au lot 9.
- La suspension attend le passage suivant de l'ordonnanceur, quinze secondes
  au plus ; les envois au-delà du plafond sont refusés entre-temps.
  Justification non écrite.

**T-102, billetterie**

- Samedi ouvré jusqu'à midi ; jours fériés de la table de la fenêtre légale.
- « Un jour » vaut une journée ouvrée de dix heures ; « immédiat » vaut quinze
  minutes.
- Le compteur mesure la première réponse et continue ensuite tant que le
  ticket est entre nos mains.
- Tout membre du compte voit et ouvre les tickets du compte.
- L'exploitation lit et traite (niveau 2), le support ressaisit aussi, la
  finance n'y a pas accès.
- Un agent ne clôt pas un ticket en attente d'information ; il peut clore un
  ticket résolu.
- Pièces jointes : PDF, JPEG, PNG, 5 Mo, chiffrées, comme les pièces du
  dossier.
- Réclamation d'un destinataire et demande du régulateur s'enregistrent comme
  des tickets ; leur circuit propre reste à écrire.

Justification non écrite pour ces points, sauf les pièces jointes (même
famille que les pièces du dossier).

**T-103, page d'état**

- Périmé au-delà de trois minutes sans publication ; un opérateur sans liaison
  active s'affiche dégradé ; l'application web et l'API ne passent pas en
  dégradé automatiquement. Justification : si la plateforme tombe, c'est l'âge
  de l'état qui le dit.
- Seule l'exploitation déclare et publie, le support lit. Justification : point
  ouvert du document de support.

**T-104, point de contrôle**

- Le temps de réponse se mesure sur la base d'essai de la CI, pas sur un
  volume de production. Justification non écrite.

## Lot 9, API publique

**T-105, politique de version**

- Douze mois entre l'annonce et le retrait ; relances à 90, 30 et 7 jours ; ni
  version datée ni en-tête de version en requête. Justification : le plan
  laisse la durée à trancher.

**T-106, clés**

- Argon2id à coût réduit (deux passes, 1 Mio). Justification : secret
  aléatoire de 256 bits.
- Dernière utilisation réécrite une fois par minute au plus ; seuls les
  administrateurs gèrent les clés ; une clé n'expire pas, elle se révoque.
  Justification non écrite.

**T-107, quotas** (le mode permissif est en tête)

- Limites communes à toutes les clés, sans réglage par clé ni par palier ;
  en-têtes nommés en clair plutôt que les champs du brouillon IETF
  `RateLimit`. Justification non écrite.

**T-108, route d'envoi**

- En-tête d'idempotence facultatif ; lot accepté en partie rendu en 202 ; dix
  segments au plus par message ; expéditeur par défaut le numéro court ;
  expéditeur non approuvé sur le réseau refusé sans repli. Justification non
  écrite.
- La liste noire n'est pas vérifiée à l'acceptation. Justification : le
  moteur la vérifie au départ, le message passe en rejet et son crédit est
  rendu.

**T-109, consultation**

- Le texte du message n'est pas rendu par l'API ; plage par défaut d'une
  semaine. Justification : la plage est reprise de l'écran.

**T-110, documentation**

- Pas d'interface interactive de type Swagger. Justification : page rendue au
  serveur, sans script tiers.
- Exemples du schéma sous l'extension `x-exemples`. Justification non écrite.

**T-111, webhooks**

- En-tête `Signature` sur le modèle courant ; dix secondes par tentative ; dix
  points actifs au plus par compte ; une livraison abandonnée ne se relance pas
  à la main ; journal des cent dernières livraisons sans pagination ;
  réarmement de l'alerte de solde à 120 % du seuil ; texte d'un SMS entrant
  transmis dans l'événement ; file `courriels.sortants` sans producteur.
  Justification non écrite, sauf les courriels (ils partent par
  `apps.core.courriels`).

**T-113, bac à sable**

- Solde et expéditeurs lus avec une clé d'essai : ceux du compte ; délais
  simulés d'une, quatre et vingt secondes ; motifs `UNDELIV` et `EXPIRED` ;
  trente jours de conservation. Justification non écrite.

**T-114, point de contrôle**

- La clé et le point de réception se préparent dans l'espace du compte, hors
  script. Justification : aucune route d'API ne crée de clé.
- Le script vise le bac à sable par défaut. Justification non écrite.
