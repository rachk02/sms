# Lot 2, première liaison télécom vérifiable

Fiches F-05, F-06, F-07 et F-08, soit 23 à 36 jours-homme. Douze tâches, T-19 à T-30.

Ce lot produit la première chose qui prouve que le projet tient : un message qui part, qui revient accusé, et dont l'état change en base. Tout se fait contre un simulateur, aucune liaison réelle n'est nécessaire.

## Ce que le lot 1 a déjà posé

Plusieurs tâches de ce lot partent d'un existant : elles l'étendent, elles ne
le refont pas.

| Tâche | Déjà là | Reste à faire |
| --- | --- | --- |
| T-19 | `apps/telecom/passerelles.py` : `Passerelle` avec `soumettre`, `accuses_dus`, `traduire` | Interroger un statut, déclarer une indisponibilité, annoncer débit et limites ; test sans nom de fournisseur |
| T-20 | `journal.models.Etat` et ses dix états ; table de correspondance par adaptateur | Trace d'un libellé inconnu ; mécanisme de déclaration formalisé |
| T-21 | `Simulateur` : identifiant hexadécimal, délai et taux d'échec réglables, libellés DELIVRD et UNDELIV | Test à cinq pour cent sur mille messages ; écart soumission et accusé vérifié |
| T-22 | Images `jookies/jasmin:0.11.0` (novembre 2023) et `ukarim/smscsim` téléchargeables | Tout |
| T-23 | Rien | Tout |
| T-24 | `enregistrer_message` et `soumettre` ; encodage et décompte des segments | Découpage réel avec en-tête de concaténation, vérifié sur ce que reçoit le simulateur |
| T-25 | Colonne `soumission_debutee_le`, posée dans la transaction de soumission | Marqueur validé avant l'appel, reprise qui interroge la passerelle au lieu de resoumettre |
| T-26 | Fait en T-14 : base puis Redis, orphelins, rejeu sans seconde transition | Fait : durée de la validité du SMS (`VALIDITE_SMS_HEURES`) plus 24 heures ; validité non transmise à Jasmin, qui la convertit mal (T-30) |
| T-27 | `appliquer_accuse` refuse une seconde transition vers un état final | Table des transitions permises, worker accusés réel |
| T-28 à T-30 | Rien | Tout |

## T-19 · Interface interne de la couche d'abstraction

**Produit** Le contrat que tout adaptateur doit respecter : soumettre un message, interroger un statut, déclarer une indisponibilité, annoncer son débit et ses limites. Aucune implémentation, seulement la forme.

**Critères** Le contrat ne mentionne ni SMPP, ni HTTP, ni Jasmin. Un test vérifie qu'un adaptateur factice minimal le satisfait. Si un nom de fournisseur apparaît dans la signature, la tâche est ratée.

## T-20 · Jeu de statuts internes et table de correspondance

**Produit** Les dix états du cycle de vie en énumération, et le mécanisme par lequel chaque adaptateur déclare sa propre table de correspondance vers ces états.

**Critères** Deux adaptateurs déclarant des libellés différents produisent le même état interne pour la même situation. Un libellé inconnu d'un opérateur ne fait pas planter le traitement : il tombe dans un état par défaut et laisse une trace.

## T-21 · Adaptateur simulateur complet

**Produit** L'adaptateur de test du lot 1 porté au contrat de T-19, avec délai d'accusé réglable, taux d'échec paramétrable, et libellés de statut imitant un opérateur réel.

**Critères** Sur mille messages avec un taux d'échec réglé à cinq pour cent, les états finaux se répartissent conformément, à la marge statistique près. Le délai d'accusé configuré se retrouve dans l'écart entre date de soumission et date d'accusé.

## T-22 · Déploiement de Jasmin et premier connecteur

**Produit** Jasmin en conteneur, raccordé à RabbitMQ et Redis, avec un connecteur SMPP pointant vers un simulateur SMPP, piloté d'abord par sa console puis par script.

**Critères** Un message soumis par la console remonte un accusé visible dans les journaux de Jasmin. Le même résultat est obtenu par script, sans intervention manuelle. La configuration du connecteur est versionnée, pas saisie à la main.

## T-23 · Adaptateur Jasmin

**Produit** L'implémentation du contrat de T-19 au-dessus de Jasmin : soumission, lecture du retour, traduction des statuts, remontée des erreurs de liaison.

**Critères** Le même appel applicatif produit un envoi via le simulateur ou via Jasmin selon la configuration, sans qu'une ligne de code métier change. Bascule vérifiée par un test qui exécute le même scénario avec les deux adaptateurs.

## T-24 · Envoi unitaire de bout en bout

**Produit** Le service qui prend un numéro, un texte et un expéditeur, choisit l'encodage, découpe en segments avec en-tête de concaténation, horodate, écrit au journal et soumet.

**Critères** Un texte de 400 caractères GSM produit trois segments correctement enchaînés, vérifié sur ce que reçoit le simulateur. Un texte contenant un caractère hors table GSM bascule en UCS-2 et le nombre de segments suit. Le message existe au journal avant la soumission, jamais après.

## T-25 · Fenêtre de soumission et reprise

**Produit** Le marqueur écrit avant l'appel à la passerelle, et la procédure de reprise qui interroge la passerelle plutôt que de resoumettre.

**Critères** Un worker tué entre l'écriture du marqueur et la réponse de la passerelle ne produit pas de doublon au redémarrage. Le test simule la coupure et vérifie qu'un seul message atteint le simulateur.

## T-26 · Corrélation des accusés, mécanisme complet

**Produit** L'écriture de la correspondance en Redis à la soumission, avec la durée de validité alignée sur celle du SMS, la reprise en base, et la table des accusés orphelins.

**Critères** Trois cas passent : accusé normal, accusé arrivé après expiration de la clé Redis et retrouvé en base, accusé sans correspondance rangé en orphelin. Un accusé rejoué ne produit qu'une transition d'état.

## T-27 · Traitement des accusés et transitions

**Produit** Le worker accusés, la validation des transitions autorisées, et l'écriture d'un événement par changement d'état.

**Critères** Une transition interdite, par exemple de LIVRÉ vers EN FILE, est refusée et journalisée. Chaque message livré porte au moins trois événements datés, réception, soumission, accusé. L'historique d'un message se relit sans ambiguïté.

## T-28 · SMS entrants, chemin complet

**Produit** La réception des messages entrants sur le point d'entrée de rappel, le tri entre accusé et message reçu, la file des entrants, et leur écriture au journal.

**Critères** Le simulateur émet un message entrant qui atterrit en base avec son numéro d'origine, son texte et son horodatage. Un accusé et un message entrant arrivant par le même canal sont correctement séparés.

## T-29 · Santé des connecteurs

**Produit** La mesure des trois signaux : état de la session, taux d'accusés positifs sur fenêtre glissante de cinq minutes, délai moyen entre soumission et accusé. Alerte sur session tombée, alerte sur effondrement du taux.

**Critères** Couper le simulateur déclenche une alerte en moins d'une minute. Régler le taux d'échec à quatre-vingts pour cent déclenche l'alerte de dégradation alors que la session tient, ce qui est le cas le plus difficile à détecter.

## T-30 · Point de contrôle du lot

**Produit** Un scénario complet exécuté sans interface : cent messages soumis, leurs accusés remontés, leurs états finaux vérifiés, un message entrant traité, une coupure de connecteur simulée et rattrapée.

**Critères** Le scénario s'exécute en une commande et rend un rapport. Aucun message ne reste dans un état intermédiaire. Le compte des états finaux correspond au compte des messages soumis. C'est ce scénario qui sera rejoué contre une liaison réelle le jour venu.
