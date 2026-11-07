# Lot 8, pilotage, supervision et back-office

Fiches F-30, F-31, F-32, F-33, F-43 et F-44, soit 44 à 66 jours-homme. Quatorze tâches, T-91 à T-104.

Découpage provisoire, à revoir en entrant dans le lot.

C'est le poste que l'on sous-estime toujours parce qu'il ne se vend pas. Vingt-six écrans d'administration, dont la moitié peut tenir en version minimale au départ, à condition de décider lesquels explicitement.

## Décisions prises avant d'entrer dans le lot

Tranchées avec l'utilisateur le 7 novembre 2026.

**Billetterie intégrée.** Le support se développe dans la plateforme, et non
dans un outil du marché : un ticket ouvert depuis un message ou une campagne
porte tout son contexte technique sans saisie.

**Page d'état, hébergeur choisi plus tard.** La page, le fichier d'état poussé
chaque minute et la publication d'incident se construisent derrière une
abstraction de dépôt, éprouvée sur un dossier local. Le véritable hébergement
statique, séparé de la plateforme, et son compte se branchent avec l'accord de
l'utilisateur.

**Quatre rôles d'agent.** Support (clients, tickets, messages), exploitation
(liaisons, connecteurs, page d'état, anti-fraude), finance (crédits, paiements,
factures, grilles) et super-administrateur, qui a tout. Un agent peut cumuler
plusieurs rôles.

**Plafonds.** Le plafond par compte, la suspension automatique et l'alerte à
l'exploitation se font au lot 8 ; le plafond par clé se branche sur le même
mécanisme au lot 9, avec les clés de l'API.

**Écrans.** Comme aux lots précédents, les écrans restent minimaux en attendant
la refonte de l'interface : le back-office s'appuie sur l'administration
existante, le portail sur des gabarits simples.

## Rôles d'agent, mise en place

Les trois rôles sont des groupes Django, réécrits après chaque migration depuis la table de `apps/backoffice/roles.py`, qui seule fait foi ; le super-administrateur est le drapeau `is_superuser`. Les actions sensibles qui ne sont pas l'écriture d'une table ont leurs droits sur un modèle sans table (`backoffice.Droit`). Chaque vue réécrite du back-office vérifie elle-même le droit exigé : l'enveloppe de l'administration ne vérifie que le statut d'agent. Les rôles s'attribuent depuis la fiche d'un agent (`/admin/comptes/utilisateur/<id>/roles/`), par un super-administrateur seulement, jamais sur lui-même, avec un motif, et la trace porte les rôles avant et après. Un test parcourt chaque liste et chaque vue réécrite avec un agent de chaque rôle, aucun n'étant super-administrateur, et échoue si une vue réécrite n'y est pas déclarée.

| Rôle | Droits |
| --- | --- |
| Support | clients (voir, enregistrer, modifier), dossiers d'ouverture (revue), utilisateurs (voir), expéditeurs (revue), noms réservés, dossier de preuve, journal d'audit (voir) ; suspendre et réactiver, clore un compte, réinitialiser un second facteur |
| Exploitation | clients et journal d'audit (voir), fenêtres légales et jours fériés |
| Finance | clients et journal d'audit (voir), paiements (voir, saisir un paiement manuel), factures (voir), réglages des paiements ; attribuer des crédits, rembourser, émettre un avoir |
| Super-administrateur | tout, dont les grilles tarifaires et l'attribution des rôles |

Choix faits, à confirmer :

- la suspension d'un compte revient au support ; l'exploitation y viendra par la suspension automatique (T-101) ;
- la finance et l'exploitation voient les fiches clients, en lecture ;
- la clôture d'un compte revient au support, le remboursement à la finance ;
- le rôle de super-administrateur ne se donne pas par l'écran des rôles.

## T-91 · Compteurs pré-agrégés

**Produit** La table des compteurs par client, campagne, opérateur et heure, alimentée par les workers au fil de l'eau, et le traitement de rattrapage en cas de trou.

**Critères** Le compteur d'une campagne terminée égale le compte réel dans le journal, vérifié par une requête de contrôle. Une panne de worker pendant dix minutes se rattrape sans double comptage. Aucun tableau de bord ne lit le journal des messages.

**Fait.** Les compteurs `journal_compteur` (client, campagne, réseau, expéditeur, heure) existaient depuis T-67 : ils s'écrivent dans la transaction même de chaque changement d'état d'un message. Un worker qui tombe annule donc ensemble l'état et le compteur, et la reprise les réécrit ensemble, une seule fois ; il n'y a pas de trou à combler. Ajouts : la requête de contrôle (`journal.selectors.compte_journal`), qui recompte une campagne dans le journal, bornée sur la date de réception, à partir de l'état final et de la date de soumission ; le recalcul (`journal.services.recompter`), qui reconstruit les compteurs d'une campagne heure par heure depuis les événements, sous verrou de ses messages, et remplace au lieu d'ajouter ; `campagnes.controle`, qui compare les deux pour les campagnes terminées ; la commande `controler_compteurs` (`--jours`, `--campagne`, `--corriger`) ; un contrôle quotidien de l'ordonnanceur sur les campagnes terminées la veille. Les tests couvrent une panne au milieu d'un accusé suivie du rejeu de la file et des accusés, un écart signalé puis recompté à l'identique, et l'absence de toute lecture de `journal_message` par le suivi et le volume mensuel.

Choix faits, à confirmer :

- un écart se signale (erreur au journal applicatif, commande en échec) mais ne se corrige que sur demande, avec `--corriger` : une anomalie ne disparaît pas sans avoir été vue ;
- les envois hors campagne (envois unitaires, API au lot 9) n'avaient pas de compteurs ; T-92 les ajoute.

## T-92 · Tableau de bord client

**Produit** Les cinq indicateurs retenus, volume émis, taux de livraison effectif, latence moyenne, répartition par opérateur, désinscriptions, avec sélecteur de période et rafraîchissement par fragment.

**Critères** Le tableau se charge en moins d'une seconde sur un compte ayant émis un million de messages. Le rafraîchissement met à jour toutes les zones en une requête. Une période sans données affiche un état vide explicite et non des zéros.

**Fait.** Écran minimal `/tableau-de-bord` (permission `tableau.consulter`, administrateur et utilisateur), lié depuis l'accueil, avec son fragment `/tableau-de-bord/fragments/indicateurs` : une sonde l'interroge toutes les dix secondes tant que l'onglet est visible, et la réponse remplace toutes les zones d'un coup, par échange hors bande. Périodes : 24 heures, 7 jours (par défaut), 30 et 90 jours, en heures entières. `campagnes.selectors.tableau_de_bord` fait deux requêtes bornées : une sur `journal_compteur`, groupée par réseau, par l'index `(client_id, heure)` ajouté ; une sur la liste noire du compte (origine STOP_SMS) pour les désinscriptions. Les compteurs portent désormais la latence (cumul en millisecondes et nombre de livraisons, écrits avec le passage à LIVRE, repris par le recalcul et par la requête de contrôle de T-91), et comptent les envois hors campagne sous une campagne nulle. Une période sans envoi ni désinscription affiche un état vide explicite ; une zone sans accusé ou sans livraison le dit aussi, au lieu d'un zéro. Les tests vérifient les cinq indicateurs, l'état vide, le fragment unique, l'envoi hors campagne, et la lecture en moins d'une seconde d'un an d'envois sur trois réseaux (26 280 lignes, 1 051 200 messages) sans aucune requête sur `journal_message`.

Choix faits, à confirmer :

- taux de livraison effectif : livrés sur les messages dont le sort est connu (livrés, non livrés, sans accusé), pour qu'une campagne en cours de diffusion ne paraisse pas mauvaise ; les compteurs étant rangés à l'heure de la transition, le taux porte sur les accusés reçus dans la période, quelle que soit l'heure d'envoi ;
- latence moyenne : de la soumission au réseau à l'accusé de livraison, sur les messages livrés ; les livraisons antérieures à T-92 n'ont pas de latence tant qu'elles ne sont pas recomptées ;
- désinscriptions : les STOP reçus par SMS, sans les ajouts manuels, les imports ni les réclamations ;
- les envois hors campagne entrent au tableau de bord mais pas encore au palier de volume des grilles (T-82) : les y compter changerait le prix ;
- rafraîchissement toutes les dix secondes, comme le prévoit l'architecture pour un tableau de bord ordinaire.

Au déploiement de T-92, les compteurs des campagnes terminées juste avant n'ont pas de latence alors que la requête de contrôle la recompte : lancer une fois `manage.py controler_compteurs --jours 2 --corriger`, sans quoi le contrôle quotidien signalera ces campagnes.

## T-93 · Journal des messages, recherche

**Produit** L'écran de recherche avec plage de dates obligatoire, filtres par statut, campagne et numéro, pagination par curseur, et bascule en export au-delà du seuil.

**Critères** Aucune recherche ne s'exécute sans borne de dates. La pagination reste constante en temps de réponse à la page cent comme à la page une. Une recherche par numéro utilise l'index sur la colonne normalisée, confirmé par le plan d'exécution.

**Fait.** Écran minimal `/journal` (permission `compte.consulter`), lié depuis l'accueil : dates de début et de fin, état, identifiant de campagne, numéro. `journal.selectors.lire_filtres` valide la saisie : une seule date, ou une plage à l'envers, est refusée avant toute lecture ; sans aucune date, la recherche porte sur les sept derniers jours. Le numéro est normalisé puis cherché par égalité sur `numero_normalise`. `rechercher` rend cinquante messages, du plus récent au plus ancien, avec un curseur sur (date de réception, id) ; la borne simple sur la date, redondante avec la condition du curseur, permet à l'index de démarrer au curseur. Au-delà de 31 jours, l'écran ne cherche pas et propose l'export, qui accepte désormais les mêmes filtres et va jusqu'à un an. Les tests vérifient le refus sans borne, sans aucune lecture de `journal_message` ; les filtres et le cloisonnement ; le parcours complet de trois pages sans OFFSET ; par le plan exécuté, que la page cent ne lit pas plus de lignes que la page une ; et que la recherche par numéro passe par l'index de la colonne normalisée.

Choix faits, à confirmer :

- le seuil de bascule en export est une durée, 31 jours, et non un nombre de résultats : compter les résultats demanderait de lire le journal ;
- la navigation va de page en page vers le passé, avec un retour au début, sans page précédente ;
- les tests de plan désactivent le balayage séquentiel, pour voir le plan d'une table de production, où les messages se comptent en millions.

## T-94 · Fiche message

**Produit** Le détail d'un message avec son historique d'états daté, l'opérateur atteint, le connecteur emprunté, l'expéditeur utilisé, le coût et le motif d'échec.

**Critères** La fiche répond à elle seule à la question la plus fréquente du support, pourquoi ce message n'est pas arrivé. Le bouton d'ouverture de ticket depuis cette fiche transporte tout ce contexte.

**Fait.** Écran minimal `/journal/messages/<id>?recu=<date de réception>` (permission `compte.consulter`), ouvert depuis chaque ligne de la recherche. La date de réception, clé de partition, vient avec l'adresse : le message se lit par sa clé exacte, sur une seule partition, et sans elle la page répond 404. `journal.selectors.fiche` rend le message, son historique d'états daté avec la source et les détails de chaque événement (réponse de l'opérateur, référence, motif), le réseau, la liaison empruntée (son nom vient de l'état des liaisons publié par l'ordonnanceur, le rôle web ne lisant pas la table des connecteurs), l'expéditeur, le coût, le motif d'échec, le délai de livraison, et un diagnostic en clair, état par état : en file, retenu, en attente d'accusé, livré, non livré avec la réponse de l'opérateur et les causes fréquentes, expiré, sans accusé, refusé avant le réseau avec sa cause (liste noire, expéditeur non approuvé, bac à sable), refusé après acceptation, annulé. `Fiche.contexte_ticket()` rassemble tout ce contexte, historique compris, en un dictionnaire transportable : le bouton d'ouverture de ticket s'y branchera avec la billetterie (T-102).

Choix faits, à confirmer :

- l'adresse de la fiche porte la date de réception plutôt que de la déduire de l'identifiant ;
- le diagnostic ne dit rien de la facturation d'un message non livré : elle dépend du mode de règlement du compte.

## T-95 · Back-office, gestion des clients

**Produit** La liste des comptes avec recherche et filtres, la fiche client complète, la suspension et la réactivation, l'attribution manuelle de crédits.

**Critères** Toute action sensible laisse une écriture au journal d'audit avec motif saisi. La suspension arrête les envois en cours et le dit à l'agent avant de confirmer. Un agent sans le rôle requis ne voit pas ces écrans et la route refuse.

**Fait.** Sur l'administration existante, écrans minimaux. Les rôles sont décrits plus haut (« Rôles d'agent, mise en place »). La liste des comptes garde sa recherche et ses filtres ; la fiche client affiche en plus, en lecture, le solde de crédits, l'activité des trente derniers jours (lue sur les compteurs, par le sélecteur du tableau de bord), les campagnes actives et les membres, et l'état n'y est plus modifiable : il ne change que par la suspension, la réactivation ou la clôture. Les actions de la fiche n'apparaissent qu'à l'agent qui en a le droit, et leurs routes refusent les autres.

Suspension (`backoffice.services.suspendre_compte`, droit `suspendre_client`) : la page dit d'abord ce qui sera arrêté, chaque campagne en attente ou en cours et ses messages pas encore partis, sans rien changer ; l'envoi du formulaire, motif exigé, met ces campagnes en pause puis suspend le compte (`comptes.services.suspendre`), et la trace nomme les campagnes mises en pause. Ensuite : les messages en file de ces campagnes sont retenus ; tout autre message du compte est refusé avant le réseau, sans frais, par un contrôle de soumission (`COMPTE_NON_ACTIF`, expliqué sur la fiche message), sauf les messages système dus à l'abonné ; rien ne se confirme ni ne reprend ; les campagnes programmées et les occurrences récurrentes attendent. La réactivation (`reactiver_compte`) reprend les seules campagnes que la suspension avait mises en pause, jamais celles que le client avait lui-même arrêtées. `suspendre_compte` accepte un auteur nul, pour la suspension automatique de T-101. Crédits offerts (`attribuer_credits`, droit `crediter_client`) : un recrédit au registre avec motif et auteur, et sa trace au journal d'audit. Les tests suivent la suspension et la réactivation de bout en bout, une campagne programmée pendant la suspension, les crédits offerts, et l'écran : refus au rôle finance, page d'aperçu sans effet, motif exigé, puis suspension.

Choix faits, à confirmer :

- les membres d'un compte suspendu peuvent encore se connecter, consulter et exporter ; seuls les envois s'arrêtent ;
- une campagne programmée dont l'heure passe pendant la suspension part à la réactivation, en retard, et non à sa prochaine heure ;
- les messages déjà soumis au réseau au moment de la suspension finissent leur chemin et se facturent normalement.

## T-96 · Back-office, supervision des liaisons

**Produit** L'écran d'état des connecteurs, la profondeur des files, l'état des workers, et l'historique des incidents de liaison.

**Critères** Une session tombée apparaît en moins d'une minute avec son heure de chute. La profondeur des files est réelle et non estimée. L'écran distingue une file qui monte d'une file qui stagne, ce qui n'est pas le même incident.

**Fait.** Écran « Supervision des liaisons » du back-office (rôle exploitation, droit de consulter les incidents de liaison), rafraîchi toutes les dix secondes par un fragment. Connecteurs : lus en base par le rôle du back-office, sans leurs secrets, avec leur état, l'heure de leur chute ou de leur dernière connexion, le motif et les messages soumis. La supervision (`telecom.sante.surveiller`, toutes les quinze secondes) tient la nouvelle table `telecom_incident_liaison` : chaque changement d'état clôt l'incident ouvert et en ouvre un si la liaison n'est pas saine, dans la même transaction que l'état et l'alerte ; un seul incident ouvert par connecteur. La table est réservée aux workers et au back-office (sécurité par ligne forcée sans politique, `core` 0033). Si l'ordonnanceur ne publie plus depuis plus d'une minute, l'écran annonce une supervision muette. Files : profondeur, consommateurs et débits lus à l'API de gestion de RabbitMQ, avec ses moyennes de cinq minutes ; une file qui stagne (rien ne sort, consommateur absent ou bloqué) se distingue d'une file qui monte (les consommateurs travaillent, moins vite que les arrivées) ; les paliers de réessai et le rebut ont leur propre lecture. Workers : chaque exemplaire écrit un battement dans Redis toutes les quinze secondes, qui expire en une minute (`core.battements`) ; l'écran nomme les workers attendus dont aucun exemplaire ne bat, et l'ordonnanceur en veille se distingue de l'actif. L'historique complet des incidents est la liste standard, filtrable par connecteur et par état.

Choix faits, à confirmer : seuil de supervision muette à une minute ; fenêtre de cinq minutes pour la tendance des files.

## T-97 · Back-office, configuration des connecteurs

**Produit** La création et la modification d'un connecteur, ses identifiants chiffrés, son débit, ses sessions, ses routes et leurs priorités.

**Critères** Un secret saisi n'est jamais réaffiché en clair. Une modification de débit prend effet sans redémarrage des workers. Toute modification laisse une écriture au journal d'audit avec l'ancienne et la nouvelle valeur.

**Fait.** Écrans « Connecteurs » et « Routes » du back-office (rôle exploitation : consulter, créer et modifier les connecteurs ; tout sur les routes). Toute écriture passe par `telecom.services` (`enregistrer_connecteur`, `enregistrer_route`, `supprimer_route`) et laisse au journal d'audit l'ancienne et la nouvelle valeur. Le secret de la liaison se saisit dans un champ de mot de passe jamais prérempli, est chiffré en base (`core.chiffrement`) et n'apparaît ni sur l'écran ni dans l'audit, qui dit seulement « présent », « absent » ou « remplacé » ; laissé vide, il reste en place. Un réglage d'adaptateur dont la clé ressemble à un secret est refusé. L'état de la liaison reste à la supervision. Un connecteur ne se supprime pas, il se désactive. Nouvelle table `telecom_route` (opérateur destinataire, connecteur, priorité, seuil de bascule, actif ; une priorité et un connecteur au plus une fois par opérateur), réservée aux workers et au back-office (`core` 0034). Le choix du connecteur (`connecteur_pour`) suit les routes quand l'opérateur en a : la première route active, par priorité, dont le connecteur est actif et la liaison pas tombée ; sans route, la règle d'avant. Il est relu à chaque message, et le worker d'envoi refait sa passerelle quand le connecteur a changé : débit, réglages ou identifiants valent sans redémarrage.

Choix faits, à confirmer :

- l'hôte, l'identifiant et le secret saisis ici sont rangés en base, mais la configuration des sessions SMPP dans Jasmin reste celle du script `scripts/configurer_jasmin.py` : la recopie automatique vers Jasmin est à décider (lot 10 ou mise en production) ;
- `seuil_bascule` est en pour cent d'accusés positifs, 85 par défaut comme le seuil de santé ; il ne sert qu'avec la bascule de T-118 ;
- un opérateur qui a des routes n'utilise plus que celles-ci : un connecteur sans route n'est jamais pris en secours implicite.

## T-98 · Back-office, grilles tarifaires

**Produit** L'écran de gestion des grilles, la création d'une version datée, la publication, et la double validation si elle est retenue.

**Critères** Une version publiée n'est plus modifiable, seule une nouvelle version la remplace. La publication laisse une écriture au journal d'audit. Un changement de prix sans date de prise d'effet est refusé.

**Fait.** Écran « Grilles » du back-office, au super-administrateur seul, sans double validation (décision du 6 novembre 2026). Une grille se crée vide (publique, négociée pour un client, promotion) ; sa fiche liste ses versions, brouillons et publiées, avec leur prise d'effet, leur fin et l'auteur de la publication. Une nouvelle version se prépare en brouillon avec sa date de prise d'effet, obligatoire et jamais passée (`backoffice.services.preparer_version`), en reprenant au besoin les prix de la version en vigueur pour ne saisir que ce qui change ; ses prix se posent ou se retirent tant qu'elle n'est pas publiée (`credits.services.fixer_palier`, `retirer_palier`). La publication passe par une page qui dit la date et les prix avant de confirmer, puis par la règle existante (`credits.services.publier_version` : version complète, jamais dans le passé, la précédente reçoit sa fin, trace avec les anciens et les nouveaux prix). Une version publiée refuse toute modification, à l'écran comme au service. Une version sans date est refusée à la publication par l'écran (`publier_grille_version`).

Choix fait, à confirmer : la commande `publier_grille` garde la publication sans date, qui prend effet tout de suite, pour le premier chargement d'une grille ; l'écran, lui, exige toujours la date.

## T-99 · Dictionnaire anti-spam

**Produit** La table des mots-clés proscrits, modifiable en base, et le filtrage à la composition avec avertissement puis blocage selon la gravité.

**Critères** Un mot de la liste bloque la campagne et nomme le mot en cause. La liste se modifie sans redéploiement. Le filtrage ne se contourne pas par des espaces ou des substitutions de caractères évidentes.

**Fait.** Nouvelle table `conformite_mot_proscrit` (expression, forme normalisée unique, gravité avertissement ou blocage, motif montré au client, actif), lue par tous et écrite par le back-office (`core` 0035). Aucune expression n'est semée : la liste se remplit à l'écran « Mots proscrits » du back-office (rôle exploitation), chaque changement tracé au journal d'audit, et vaut dès l'examen suivant. L'examen (`conformite.antispam`) normalise le texte et chaque expression de la même façon : casse et accents, substitutions évidentes (0 pour o ; 1, l, ! et | pour i ; 3 et € pour e ; 4 et @ pour a ; 5 et $ pour s ; 7 pour t ; 8 pour b), séparateurs et chiffres restants retirés, lettres répétées réduites ; une expression doit compter au moins trois lettres normalisées. Une expression bloquante refuse la campagne à sa création, à son aperçu, à l'envoi de test, à la confirmation et à chaque occurrence d'une récurrence, et le refus nomme l'expression et son motif. Un avertissement s'affiche dans l'aperçu de la campagne sans bloquer.

Choix faits, à confirmer :

- le dictionnaire revient à l'exploitation, avec l'anti-fraude ;
- les séparateurs étant retirés, une expression peut se trouver à cheval sur deux mots ; c'est le prix du refus des contournements par espaces ;
- les envois unitaires et ceux de l'API ne passent pas au dictionnaire, qui porte sur la composition des campagnes.

## T-100 · Détection de trafic artificiel

**Produit** Les trois règles de détection : séries de numéros contigus, pics de codes à usage unique vers des plages inhabituelles, taux de livraison anormalement bas sur une plage.

**Critères** Un envoi vers mille numéros consécutifs déclenche l'alerte. Chaque règle a un seuil configurable en base. Une alerte ne bloque pas automatiquement, elle prévient un humain, sauf dépassement du seuil critique.

## T-101 · Plafonnement et suspension automatique

**Produit** Les plafonds par clé et par compte en application effective, la suspension automatique d'une clé au-delà du seuil critique, et l'alerte à l'exploitation.

**Critères** Un compte qui dépasse son plafond horaire reçoit un refus et une alerte part à l'exploitation. Créer dix clés ne multiplie pas la limite, le plafond compte est le garde-fou et le test le vérifie explicitement.

## T-102 · Billetterie de support

**Produit** Le formulaire rattaché à une campagne ou à un message, le fil de ticket, les catégories et priorités, le cycle de vie complet des sept états, et les écrans côté agent.

**Critères** Un ticket ouvert depuis une fiche message porte tout son contexte technique sans saisie. Une escalade réseau suspend le compteur d'engagement. Un ticket en attente d'information se clôt automatiquement après sept jours avec préavis au cinquième.

## T-103 · Page d'état du service

**Produit** Les sept composants, leurs quatre états, le fichier poussé toutes les minutes vers un hébergement statique séparé, et la publication d'incident.

**Critères** La page continue d'afficher le dernier état connu quand la plateforme ne répond plus, ce que le test vérifie en coupant l'application. Le passage en dégradé est automatique, le passage en panne demande une action humaine.

## T-104 · Point de contrôle du lot

**Produit** Une journée d'exploitation simulée : campagnes en cours, une liaison qui tombe, une alerte anti-fraude, un ticket ouvert depuis un message, un incident publié, un rapprochement quotidien.

**Critères** Chaque événement est visible depuis le back-office sans requête en base. Le journal d'audit contient toutes les actions d'administration de la journée. Aucun écran ne met plus de deux secondes à répondre.
