# Lot 3, comptes, sécurité et onboarding

Fiches F-09, F-10 et F-11, soit 19 à 33 jours-homme. Douze tâches, T-31 à T-42.

Découpage provisoire, à revoir en entrant dans le lot : ce que le lot 2 aura appris sur le modèle de données peut déplacer une ou deux tâches.

Ce lot ouvre le premier écran. Jusqu'ici tout se faisait en ligne de commande.

## Décisions prises avant d'entrer dans le lot

Tranchées le 29 septembre 2026 par la direction du projet.

- **Pas d'inscription libre au lancement (F-11).** Les premiers clients viennent
  de la vente directe : l'équipe EL-IMMEKA enregistre le client et son dossier
  depuis le back-office, après signature. Le parcours de téléversement en
  quatre étapes de T-37 est remplacé par ce formulaire d'agent ; les pièces
  attendues selon la forme juridique restent exigées.
- **Deux rôles au lancement (F-10)** : administrateur du compte et utilisateur.
  Les cinq rôles du CDC V3.0 viendront ensuite ; T-35 et T-36 se lisent avec
  ces deux rôles, « propriétaire » s'entendant « dernier administrateur ».
- **Pas d'expiration forcée des mots de passe (F-09).** Changement exigé sur
  soupçon de compromission ; second facteur obligatoire pour l'administration.

## Ce que les lots précédents ont déjà posé

| Tâche | Déjà là |
| --- | --- |
| T-31 | Modèle `Client` (forme juridique, RCCM et IFU uniques en base), service `creer_client` |
| T-33 | Modèle `Utilisateur` : courriel comme identifiant, Argon2id, compteur d'échecs et date de verrouillage |
| T-38 | MinIO dans le docker-compose ; convention de chemin `clients/<client>/<famille>/<uuid>.<ext>` au modèle |
| T-41 | Rien : le journal d'audit est à écrire |

## T-31 · Inscription et identité juridique

**Produit** Le parcours de création de compte : raison sociale, forme juridique, RCCM, IFU, contact, avec les validations propres à chaque forme de structure.

**Critères** Une société commerciale sans RCCM est refusée, une association sans récépissé aussi, et le message nomme la pièce manquante. Deux inscriptions avec le même RCCM sont refusées par la contrainte de base, pas seulement par le formulaire.

## T-32 · Vérification de l'adresse de courriel

**Produit** L'envoi du lien signé valable vingt-quatre heures, la page de confirmation, et le renvoi du lien.

**Critères** Un lien expiré affiche un message clair et propose un renvoi. Un lien déjà consommé ne fonctionne plus. Le compte reste inutilisable tant que l'adresse n'est pas vérifiée.

## T-33 · Connexion, verrouillage et sessions

**Produit** La connexion par courriel et mot de passe, le verrouillage temporaire après cinq échecs, l'expiration de session sur inactivité, et le journal des connexions.

**Critères** Cinq échecs verrouillent le compte pour une durée annoncée à l'utilisateur. Le verrouillage ne révèle pas si l'adresse existe. Une session inactive expire et la page de reconnexion préserve le travail en cours, comme prévu au catalogue des retours.

## T-34 · Second facteur

**Produit** L'activation par application d'authentification, les codes de secours, l'obligation pour les rôles d'administration, et la procédure de réinitialisation.

**Critères** Un compte à rôle administrateur ne peut pas se connecter sans second facteur une fois le délai de grâce écoulé. Les codes de secours ne sont affichés qu'une fois et l'utilisateur doit confirmer les avoir notés. Un code de secours consommé ne fonctionne plus.

## T-35 · Rôles et permissions

**Produit** Les cinq rôles, avec des permissions exprimées par ressource et par action, vérifiées côté serveur à chaque appel, page comme fragment.

**Critères** Un test parcourt toutes les routes avec chacun des cinq rôles et vérifie le code de réponse attendu. Masquer un bouton dans l'interface ne suffit pas : la route correspondante doit refuser. Un fragment HTMX refuse exactement comme sa page.

## T-36 · Gestion des membres

**Produit** L'invitation par courriel avec lien valable sept jours, l'acceptation, le changement de rôle, le retrait d'un membre.

**Critères** Le dernier propriétaire d'un compte ne peut pas être retiré ni rétrogradé. Une invitation vers une adresse déjà membre est refusée avec un message explicite. Chaque changement de rôle laisse une écriture au journal d'audit.

## T-37 · Dépôt du dossier d'onboarding

**Produit** Le parcours en quatre étapes : identité juridique, téléversement des pièces selon la forme de structure, déclaration de l'identifiant d'expéditeur, récapitulatif et soumission.

**Critères** Les pièces attendues changent selon la forme juridique choisie, conformément au tableau de la fiche F-11. Un fichier au type non conforme est refusé sur son contenu et non sur son extension. Le parcours se reprend là où il a été laissé.

## T-38 · Stockage et chiffrement des pièces

**Produit** Le téléversement vers le stockage objet, le chemin porteur du cloisonnement, le chiffrement au repos des pièces d'identité, les liens signés de quinze minutes.

**Critères** Aucun fichier n'est servi par l'application elle-même. Un lien signé expiré ne donne plus accès. Le nom d'origine du fichier n'apparaît jamais dans le chemin de stockage.

## T-39 · Revue des dossiers par un agent

**Produit** L'écran de revue côté administration : la file des dossiers, la fiche de revue avec les pièces, les cinq contrôles, l'approbation, le rejet motivé.

**Critères** Un rejet sans motif saisi est refusé. Le motif est celui qui part au client, mot pour mot. Chaque décision laisse une écriture au journal d'audit avec l'auteur et l'horodatage. La file est triée par ancienneté, pas par date de dépôt décroissante.

## T-40 · Bac à sable et paliers de compte

**Produit** Les trois paliers, la limitation du bac à sable à dix numéros de test vérifiés, le bandeau permanent, et le passage automatique au palier supérieur à l'approbation.

**Critères** Un compte en bac à sable ne peut envoyer qu'aux numéros enregistrés et vérifiés, la tentative vers un autre numéro est refusée côté serveur. Le bandeau indique l'état réel du dossier et non un texte figé. Le passage de palier relève les plafonds sans intervention.

## T-41 · Journal d'audit

**Produit** La table en écriture seule et le mécanisme qui enregistre toute action d'administration touchant à l'argent, aux accès ou à la conformité, avec valeur avant et après.

**Critères** Aucune méthode ne permet de modifier ni de supprimer une écriture, ce qu'un test tente explicitement. Une approbation de dossier, un changement de rôle et une révocation de clé produisent chacun une écriture complète. Le journal se consulte et se filtre.

## T-42 · Point de contrôle du lot

**Produit** Un parcours complet joué de bout en bout : inscription, vérification, dépôt du dossier, revue par un agent, approbation, premier envoi réel sous palier vérifié.

**Critères** Le parcours s'effectue entièrement par l'interface, sans commande. Un second parcours avec rejet du dossier aboutit à un compte resté en bac à sable et à un client informé du motif. Les deux parcours laissent une trace complète au journal d'audit.

## Bilan du lot, 30 septembre 2026

Les douze tâches sont faites, avec les décisions de lancement ci-dessus. Le
point de contrôle (T-42) est un test automatique qui joue les deux parcours par
les écrans, chacun dans l'espace et sous le rôle PostgreSQL qui le servent :
`tests/test_point_de_controle_lot3.py`.

Écarts par rapport au découpage d'origine :

- T-31 et T-37 : enregistrement par un agent au back-office au lieu d'une
  inscription libre et d'un parcours en quatre étapes (décision F-11).
- T-32 et T-36 : l'invitation vérifie l'adresse en même temps qu'elle crée
  l'accès ; la vérification seule (vingt-quatre heures) sert au renvoi et
  servira au changement d'adresse.
- T-42 : le premier envoi passe par le service d'envoi, l'écran d'envoi
  arrivant au lot 5.
- Courriels : envoyés par le moteur de courriel de Django après validation de
  la transaction ; la file `courriels.sortants`, le prestataire et le journal
  des envois viennent avec le module notifications.
- Le journal d'audit est passé de `conformite` à `core`.
