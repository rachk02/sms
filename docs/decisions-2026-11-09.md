# Décisions du 9 novembre 2026

Le 9 novembre 2026 à 08 h 28, l'utilisateur a confié pour la journée à la
session « Niveau d'avancement du projet » la conduite de la session de
développement et les décisions du registre `docs/a-confirmer.md` qui lui
revenaient. Chaque décision ci-dessous est **décidée par la session
responsable, par délégation de l'utilisateur du 9 novembre 2026**, et renvoie
au point du registre qu'elle tranche.

Restent réservés à l'utilisateur, même par délégation : la production, le
déploiement, les secrets, l'argent, les comptes externes (hébergeur de la page
d'état, prestataire de courriel), le contenu légal des factures (mentions, DGI,
numérotation), le passage à 40 000 destinataires et le test humain de T-110.
En conséquence :

- ce qui touche à l'argent (prix facturés, registre de crédits) et les
  correctifs dans les zones délicates (registre de crédits, cloisonnement par
  ligne, corrélation des accusés) sont préparés, testés et commités en local,
  puis poussés seulement avec l'accord de l'utilisateur ;
- la décision C2, qui change ce que paient les clients, est consignée
  seulement ;
- la purge du journal (B5) est construite mais inactive tant que
  l'utilisateur n'a pas réglé sa durée.

La session de développement a signalé un changement de compte : depuis le
9 novembre, elle est rattachée à un autre compte que la veille. L'utilisateur
dira si la délégation vient bien de lui ; d'ici là, seul le travail réversible
avance.

À 08 h 38, dans le terminal de la session responsable, l'utilisateur a
confirmé le changement de compte et la délégation, et coché son accord pour
B1, C2 et la poussée des correctifs des zones délicates après relecture par la
session responsable. Cet accord est parvenu à la session de développement par
relais : pour ce qui touche à l'argent et aux zones délicates, elle attend que
l'utilisateur le lui confirme dans son propre terminal, et garde ces commits
en local d'ici là. C2 entre dans l'ordre de travail, juste après C1.

## Suivi

Les deux listes se remplissent au fil de la journée, un commit par ligne.

### Appliqué

- **A2**, mot de passe oublié et changement de mot de passe : commit
  « Comptes : mot de passe oublié et changement de mot de passe ».
- **A3**, freinage par adresse IP à la connexion : commit « Comptes :
  freinage par adresse IP à la connexion ». L'adresse source est désormais lue
  par `apps.core.reseau.adresse_source`, partagée avec les rappels de paiement.
- **A1**, panne de Redis : commit « Quotas : repli en mémoire quand Redis
  tombe ». Seaux par clé et plafond du compte comptés dans la mémoire du
  processus (`apps.core.repli`), aux mêmes limites, en-têtes compris ; après
  un échec, Redis est laissé de côté cinq secondes. Le client Redis reçoit un
  délai de deux secondes (`REDIS_DELAI_S`), sans quoi un Redis muet
  bloquerait chaque requête. Le « laisser passer » de la soumission par l'API
  (campagnes) disparaît. Connexion et second facteur restent fermés.

### Préparé, en attente de l'accord de l'utilisateur

Chaque ligne nomme le commit local, non poussé. L'accord de l'utilisateur
suffit pour le pousser tel quel.

Aucun commit en attente à l'ouverture de la journée.

### Consigné seulement

- **C2**, prix public plafond : rien n'est écrit dans le code tant que
  l'utilisateur ne l'a pas confirmé.

## A. Sécurité

- **A1. Panne de Redis.** La connexion et le second facteur restent fermés.
  Pour les quotas de l'API et le plafond du compte, au lieu de tout laisser
  passer, repli sur des compteurs en mémoire par processus, aux mêmes limites :
  approximatifs, jamais illimités. L'événement `api.quota.permissif` devient
  `api.quota.repli`. Registre : en tête, T-107 et les deux comportements côte
  à côte.
- **A2. Mot de passe oublié et changement de mot de passe**, à construire.
  Oubli : lien signé à usage unique valable une heure, réponse identique que
  le courriel soit connu ou non, second facteur toujours exigé ensuite.
  Changement : avec l'ancien mot de passe ; les autres sessions sont
  invalidées, la session courante garde son empreinte ; courriel
  d'avertissement. Registre : relecture de l'authentification.
- **A3. Freinage par adresse IP à la connexion.** Vingt échecs en quinze
  minutes depuis une adresse la bloquent quinze minutes. L'adresse source se
  lit comme pour T-84 (`PROXIES_DE_CONFIANCE`). Registre : relecture de
  l'authentification.
- **A4. Lien d'export des contacts.** Le courriel porte un lien du portail qui
  exige la connexion et contrôle les droits, puis renvoie vers un lien signé de
  cinq minutes. Le lien de sept jours disparaît. Registre : relecture des
  notifications.
- **A5. Webhooks en production** : https obligatoire, ports 443 et 8443
  seulement. Rien ne change en développement ni en test. Registre : relecture
  de l'API publique, port libre.
- **A6. Pièces jointes servies aux agents en téléchargement**
  (`response-content-disposition=attachment`), après relecture de tous les
  appelants de `fichiers.lien`. Registre : relecture du support.
- **A7. Appels internes en http** : `SECURE_REDIRECT_EXEMPT` pour le seul
  chemin du rappel de Jasmin et celui des métriques, à condition que ces deux
  routes soient authentifiées (jeton, liste d'adresses). Si le rappel de
  Jasmin ne l'est pas, rien n'est exempté avant d'en reparler. Registre :
  relecture de la configuration de production.
- **A8. Politique de sécurité du contenu** en mode `Report-Only` en
  production dès maintenant ; elle devient bloquante à la refonte du
  frontend. Registre : relecture de la configuration de production.
- **A9. Pas de préchargement HSTS au lancement.** Registre : relecture de la
  configuration de production.
- **A10. Résolveur DNS des workers** : `timeout:2` et `attempts:2` dans
  Docker Compose. Registre : relecture de l'API publique, résolution DNS.
- **A11. Acceptés** : le temps de réponse d'une clé au préfixe inconnu ;
  l'apostrophe sur un numéro saisi comme texte. Le port libre est remplacé par
  A5. Registre : relectures de l'API publique et des exports.

## B. Données et registre

- **B1. Unicité de l'écriture ACHAT d'un paiement** : contrainte partielle
  sur la référence du paiement, sur le modèle de `CONSOMMATION`, après
  vérification des formes de référence existantes (paiement en ligne et
  paiement manuel), avec un test de concurrence. Zone du registre de crédits :
  préparé en local, poussé avec l'accord de l'utilisateur. Registre :
  relecture des rappels de paiement.
- **B2. Devise du statut** : seul le XOF crédite ; toute autre devise lève
  une alerte sans rien créditer. Registre : relecture des rappels de paiement.
- **B3. `core.Audit`** : les deux `ForeignKey` deviennent des UUID simples,
  indexés, sous le même nom de colonne. La migration ne touche aucune
  politique de cloisonnement ; si elle le devait, arrêt et retour à la session
  responsable. Registre : points ouverts, lot 1.
- **B4. Tables sans cloisonnement** : `comptes_code_secours` reste en l'état.
  Le rôle applicatif perd ses droits sur `core_evenement_traite` si, et
  seulement si, le service web ne s'en sert pas ; dans une migration dédiée de
  `core`. Registre : points ouverts.
- **B5. Rétention du journal** : douze mois consultables, puis purge par
  partition mensuelle des partitions de plus de douze mois pleins. La purge est
  construite mais **inactive tant que `JOURNAL_RETENTION_MOIS` n'est pas réglé
  dans l'environnement**, sans valeur par défaut : rien n'est effacé avant que
  l'utilisateur l'ait réglé, après validation juridique. Le dossier de preuve
  couvre déjà douze mois. Registre : points ouverts, T-89.
- **B6. Imports** : 500 000 lignes au plus ; la borne de 1 Go décompressé est
  gardée. Registre : relecture des exports et des imports.

## C. Métier

- **C1. Palier et plafonds d'un client** : le palier est réglé par la
  finance, les plafonds par l'exploitation, et les deux par le
  super-administrateur, avec des permissions dédiées, un motif obligatoire et
  un service audité, sur le modèle de `changer_mode_reglement`. Registre :
  relecture des rôles du back-office.
- **C2. T-83, prix public plafond** : un client ne paie jamais plus que la
  grille publique. **Consigné seulement** : la décision change ce que paient
  les clients, elle attend la confirmation de l'utilisateur. Registre : T-83.
- **C3. T-80, rapprochement à heure fixe** : une fois par jour à 02 h 00,
  heure de Ouagadougou, sans répéter l'alerte après un redémarrage.
  Registre : T-80.
- **C4. Alertes vers les agents** : l'alerte critique de fraude et l'écart du
  rapprochement partent par courriel aux agents d'exploitation actifs, par un
  consommateur d'événements placé dans la couche que la charte permet.
  Registre : T-80, T-101.
- **C5. Autres choix des lots 7 à 9** : confirmés tels quels. Deux
  exceptions : la numérotation F et AV reste liée aux factures (réservée à
  l'utilisateur) ; T-97, la recopie vers Jasmin, reste manuelle par script au
  lancement. Registre : sections des lots 7, 8 et 9.

## D. Qualité

- **D1. `tests/test_envoi_debit.py`** : les deux mesures au temps réel
  deviennent robustes (débit mesuré sans plafond de durée totale, attente de
  démarrage de soixante secondes). Tests seulement. Registre : bilan du
  8 novembre.
- **D2. Vitesse des tests, première piste**, après les tâches ci-dessus : les
  fichiers à une seule connexion passent en transaction simple, un fichier à
  la fois, mesuré avant et après. Registre : bilan du 8 novembre.

## E. Relectures de sécurité restantes

En lecture d'abord, défaut certain corrigé avec son test : composition des
campagnes, segments et programmation ; passerelle et corrélation des accusés ;
registre de crédits ; écrans du portail hors authentification. Un correctif
dans une zone délicate porte ses tests de concurrence et de rejeu, et reste en
local jusqu'à l'accord de l'utilisateur.

## Ordre de travail

Documentation des décisions, puis A2, A3, A1, A4, B1 (en local), B3, A7, A5,
A6, B2, C1, C2, C3, A8, A10, B4, B6, D1, C4, B5 (inactive), puis E, puis D2.
