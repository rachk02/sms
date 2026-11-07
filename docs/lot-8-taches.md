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

## T-93 · Journal des messages, recherche

**Produit** L'écran de recherche avec plage de dates obligatoire, filtres par statut, campagne et numéro, pagination par curseur, et bascule en export au-delà du seuil.

**Critères** Aucune recherche ne s'exécute sans borne de dates. La pagination reste constante en temps de réponse à la page cent comme à la page une. Une recherche par numéro utilise l'index sur la colonne normalisée, confirmé par le plan d'exécution.

## T-94 · Fiche message

**Produit** Le détail d'un message avec son historique d'états daté, l'opérateur atteint, le connecteur emprunté, l'expéditeur utilisé, le coût et le motif d'échec.

**Critères** La fiche répond à elle seule à la question la plus fréquente du support, pourquoi ce message n'est pas arrivé. Le bouton d'ouverture de ticket depuis cette fiche transporte tout ce contexte.

## T-95 · Back-office, gestion des clients

**Produit** La liste des comptes avec recherche et filtres, la fiche client complète, la suspension et la réactivation, l'attribution manuelle de crédits.

**Critères** Toute action sensible laisse une écriture au journal d'audit avec motif saisi. La suspension arrête les envois en cours et le dit à l'agent avant de confirmer. Un agent sans le rôle requis ne voit pas ces écrans et la route refuse.

## T-96 · Back-office, supervision des liaisons

**Produit** L'écran d'état des connecteurs, la profondeur des files, l'état des workers, et l'historique des incidents de liaison.

**Critères** Une session tombée apparaît en moins d'une minute avec son heure de chute. La profondeur des files est réelle et non estimée. L'écran distingue une file qui monte d'une file qui stagne, ce qui n'est pas le même incident.

## T-97 · Back-office, configuration des connecteurs

**Produit** La création et la modification d'un connecteur, ses identifiants chiffrés, son débit, ses sessions, ses routes et leurs priorités.

**Critères** Un secret saisi n'est jamais réaffiché en clair. Une modification de débit prend effet sans redémarrage des workers. Toute modification laisse une écriture au journal d'audit avec l'ancienne et la nouvelle valeur.

## T-98 · Back-office, grilles tarifaires

**Produit** L'écran de gestion des grilles, la création d'une version datée, la publication, et la double validation si elle est retenue.

**Critères** Une version publiée n'est plus modifiable, seule une nouvelle version la remplace. La publication laisse une écriture au journal d'audit. Un changement de prix sans date de prise d'effet est refusé.

## T-99 · Dictionnaire anti-spam

**Produit** La table des mots-clés proscrits, modifiable en base, et le filtrage à la composition avec avertissement puis blocage selon la gravité.

**Critères** Un mot de la liste bloque la campagne et nomme le mot en cause. La liste se modifie sans redéploiement. Le filtrage ne se contourne pas par des espaces ou des substitutions de caractères évidentes.

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
