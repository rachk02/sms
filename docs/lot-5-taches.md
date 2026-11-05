# Lot 5, composition et diffusion des campagnes

Fiches F-17 à F-22, soit 33 à 53 jours-homme. Quatorze tâches, T-55 à T-68.

Découpage provisoire, à revoir en entrant dans le lot.

C'est le coeur du produit et le lot le plus long. C'est aussi celui où une erreur se facture à des milliers d'exemplaires.

## Décisions prises avant d'entrer dans le lot

Tranchées le 30 septembre 2026 par la direction du projet.

- **Envoi de test facturé (F-20).** Rien n'est gratuit pour l'instant : un
  envoi de test est débité du solde comme un envoi unitaire ordinaire, hors de
  la réservation de la campagne.
- **Récurrence sous plafond (F-22).** À chaque occurrence, le segment est
  recalculé et figé ; l'occurrence part seule si son coût reste sous un plafond
  confirmé à la création de la campagne, sinon elle attend une confirmation et
  le client est prévenu.
- **Règlement à la soumission jusqu'au lot 7.** Une campagne réserve son coût
  au cas le plus long à sa confirmation ; chaque message consomme son coût
  quand le réseau l'accepte, un message refusé avant le réseau ne coûte rien, et
  le reliquat est libéré en fin de campagne. Un envoi de test consomme de même,
  hors de la réservation de la campagne. Le lot 7 (T-78) ajoutera le règlement
  à la livraison.
- **Compositeur et constructeur de segments en HTMX et Alpine.** Même pile que
  le reste de l'interface ; le compteur de segments et l'analyse de l'encodage
  se calculent en Alpine. L'îlot React reste possible plus tard si l'état
  devient ingérable.

Déjà tranché par l'architecture : la politique d'expéditeur par défaut est la
bascule sur le numéro court, signalée dans le rapport de campagne ; le numéro
court est partagé entre tous les clients, factice jusqu'à son attribution.

## Choix faits en cours de lot

Choix d'exécution, sans décision de la direction à attendre ; à revoir si l'un
d'eux ne convient pas.

- **Prix provisoire (T-56).** Jusqu'aux grilles tarifaires datées (lot 7), le
  prix d'un segment est un entier des réglages, lu par `credits.tarifs`.
- **Identifiant d'expéditeur strict (T-58).** Lettres sans accent et chiffres,
  sans espace ni signe, au moins une lettre : ce que les trois réseaux affichent
  tous. La pièce de mandat est obligatoire.
- **Nom réservé par inclusion (T-58).** Un libellé qui contient un nom réservé
  part en revue manuelle ; la liste initiale (opérateurs, institutions, mots
  génériques trompeurs) est à compléter par le back-office.
- **Déclarer un identifiant est réservé à l'administrateur du compte** ; les
  deux rôles consultent les états par réseau.
- **Contrôle de l'expéditeur sur tout envoi (T-60).** Enregistré auprès des
  contrôles d'avant soumission : un message ne part que sous un identifiant du
  compte approuvé sur le réseau visé et non suspendu, ou sous le numéro court
  (réglage `SMS_NUMERO_COURT`, factice jusqu'à son attribution). La politique
  de campagne se décide par réseau (`expediteurs.controle.choisir`) ; le
  compte par expéditeur du rapport de campagne vient avec le suivi (T-67).
- **Publication par l'ordonnanceur (T-62).** L'ordonnanceur, seul actif, publie
  les campagnes par lots de 500, quelques lots par tour. Le texte est rendu à la
  publication avec les données du contact à cet instant et les valeurs de repli
  figées à la confirmation. Avant chaque lot, l'expéditeur est réévalué : un
  identifiant révoqué suit la politique de la campagne (bascule, ou attente
  pour « refuser » et « attendre »), une approbation libère les destinataires
  en attente. Un contact au réseau inconnu n'est ni figé ni facturé.
- **Débit par connecteur, partagé par tous les workers (T-63).** Créneaux
  espacés en Redis (horloge de Redis), retenus et attendus juste avant l'appel
  à la passerelle ; marge de sécurité de 3 % sous le débit accordé. Groupes de
  workers par variables d'environnement : `SMS_FILES_ENVOI` (files prioritaires
  d'un côté, files de masse d'un réseau de l'autre) et `SMS_PREFETCH_ENVOI`,
  aligné sur le débit de l'opérateur.
- **Le coût réel ne dépasse jamais le coût annoncé.** Un message plus long que
  prévu (contact modifié depuis la confirmation) consomme au plus ce qui reste
  de la réservation de sa campagne ; la plateforme absorbe l'écart.
- **Pause par retenue au worker (T-64).** Une campagne en pause ne publie plus,
  et ses messages déjà en file sont retenus par le worker avant tout créneau de
  débit : ils passent EN_ATTENTE au journal, sans soumission, et ne prennent la
  place d'aucun autre envoi. La retenue est enregistrée dans `core.controles`
  par le module des campagnes, comme les contrôles d'avant soumission, et
  revérifiée sous le verrou du message dans `journal.services.soumettre`. À la
  reprise, l'ordonnanceur remet les messages retenus en file, par lots, chaque
  remise sous sa propre clé dans la boîte d'envoi.
- **Annulation et crédits d'une campagne annulée (T-64).** Le plan laissait ce
  point à trancher avec le lot 7 ; jusque-là, la règle du règlement à la
  soumission suffit : les messages déjà soumis restent consommés, les autres
  sont annulés sans frais, puis le reliquat de la réservation est libéré. Un
  message dont la soumission a commencé n'est jamais annulé : il finit son
  chemin et se règle avant la libération. À revoir avec le règlement à la
  livraison (T-78).
- **Clôture de toute campagne (T-64).** Une campagne qui n'a plus rien à
  publier ni à soumettre se clôt à l'ordonnanceur : les messages soumis dont le
  worker n'aurait pas encore écrit la consommation sont réglés d'abord, puis le
  coût réel est inscrit et le reliquat libéré. Un message au sort de soumission
  inconnu retient la clôture jusqu'à sa vérification. Des destinataires en
  attente d'approbation retiennent aussi une campagne en cours ; le client peut
  l'annuler.
- **Bilan lu à l'affichage, en attendant les compteurs (T-64).** L'écran d'une
  campagne confirmée compte ses messages par état au journal, borné à ses
  partitions et porté par l'index (campagne_id, etat), sans rafraîchissement.
  Le suivi en direct (T-67) le remplacera par les compteurs pré-agrégés.

## T-55 · Modèles de message et variables

**Produit** L'éditeur de message avec balises de personnalisation issues des champs de contact, valeur de repli obligatoire, et substitution au moment de la soumission.

**Critères** Une balise sans valeur de repli est refusée à l'enregistrement. Un champ vide chez un contact produit la valeur de repli et jamais une chaîne vide au milieu d'une phrase. Un test vérifie qu'aucun message rendu ne contient de virgule orpheline.

## T-56 · Compteur de segments au cas le plus long

**Produit** Le calcul du nombre de segments après personnalisation, en retenant le destinataire qui produit le message le plus long, et non la moyenne.

**Critères** Sur une liste où un contact porte un nom de trente caractères et un autre de cinq, le compteur annonce le cas long. Le coût affiché avant lancement est supérieur ou égal au coût réel, jamais inférieur. Un test compare les deux sur mille destinataires.

## T-57 · Moteur d'optimisation de l'encodage

**Produit** L'analyse du texte pendant la saisie, la détection des caractères hors table GSM, leur surlignage, la conversion en un clic, et le compteur qui suit en direct.

**Critères** Taper un e accent circonflexe fait apparaître l'avertissement et le compteur tombe de 160 à 70 sans rechargement de page. La conversion proposée ne dénature pas le sens, elle remplace seulement les caractères coûteux par leur équivalent GSM. Les accents de la table GSM ne déclenchent aucun avertissement.

## T-58 · Déclaration d'un identifiant d'expéditeur

**Produit** Le formulaire de déclaration, le contrôle des onze caractères et de l'alphabet, la liste des noms réservés, le téléversement de la pièce de mandat.

**Critères** Un nom figurant sur la liste réservée déclenche une revue manuelle et n'est jamais activé automatiquement. Un nom de douze caractères est refusé à la saisie. Un accent dans le libellé est refusé.

## T-59 · État par réseau des expéditeurs

**Produit** La table des états par opérateur, l'écran qui les affiche séparément, et la saisie des décisions par un agent avec date, référence de dossier et motif.

**Critères** Un même identifiant affiche trois états distincts sur un écran. Une décision saisie laisse une écriture au journal d'audit. Aucune route ne permet de modifier un état réseau depuis l'espace client.

## T-60 · Contrôle de l'expéditeur à la soumission

**Produit** La vérification que l'identifiant appartient au compte émetteur et qu'il est approuvé sur le réseau qui porte le numéro visé, avec les trois politiques de campagne.

**Critères** Un lot mélangé avec un expéditeur approuvé chez un seul opérateur produit le comportement choisi : refus, bascule sur le numéro court, ou mise en attente. Le rapport de campagne indique combien de messages sont partis sous quel expéditeur.

## T-61 · Aperçu, test et confirmation

**Produit** Le récapitulatif avant diffusion : destinataires après filtrage, expéditeur retenu, segments, coût total, envoi de test vers un numéro, puis confirmation explicite.

**Critères** La modale de confirmation porte le nombre de destinataires et le coût en toutes lettres dans son texte, pas seulement dans l'écran derrière. Le bouton nomme l'action. Un envoi de test ne décompte pas de crédit de campagne.

## T-62 · Découpage et publication en file

**Produit** Le découpage de la campagne en lots, la publication vers la file du bon réseau, et la clé d'idempotence par couple campagne et destinataire.

**Critères** Une campagne rejouée entièrement ne produit aucun doublon, la contrainte d'unicité la bloque. Un lot mélangeant trois opérateurs se répartit dans les trois files d'envoi correspondantes, vérifié en comptant les messages par file.

## T-63 · Workers de diffusion par réseau

**Produit** Les workers de masse, un jeu par opérateur, avec un prefetch aligné sur le débit accordé par cet opérateur, et les workers prioritaires distincts.

**Critères** Un envoi transactionnel passe devant une campagne de quarante mille messages en cours, ce que le test mesure en comparant les délais. Régler le débit d'un opérateur à dix messages par seconde produit effectivement dix messages par seconde et non davantage.

## T-64 · Pause, reprise et annulation

**Produit** La mise en pause d'une campagne en cours, sa reprise, son annulation, et la libération des crédits réservés non consommés.

**Critères** Une pause arrête la soumission en quelques secondes et le compteur de messages non soumis est exact. Une annulation libère exactement les crédits des messages non partis. Ce qui est déjà soumis au réseau n'est pas rattrapable et l'écran le dit clairement.

## T-65 · Planification et récurrence

**Produit** L'envoi immédiat, programmé, et récurrent sur fréquence fixe, avec l'ordonnanceur en instance unique protégée par verrou.

**Critères** Deux instances de l'ordonnanceur lancées en même temps ne déclenchent qu'un seul envoi, le verrou consultatif PostgreSQL le garantit et le test le vérifie. Une campagne programmée dans le passé est refusée à l'enregistrement.

## T-66 · Lissage par paliers

**Produit** L'étalement d'une campagne sur une durée choisie, par paliers réguliers, pour éviter de saturer le standard du client.

**Critères** Une campagne de trois mille messages lissée sur trois heures produit environ mille messages par heure, mesuré sur les horodatages de soumission. Le lissage se combine correctement avec le débit maximal de l'opérateur, le plus contraignant des deux s'applique.

## T-67 · Suivi de campagne en direct

**Produit** L'écran de suivi avec ses compteurs pré-agrégés, le fragment rafraîchi toutes les deux secondes pendant la diffusion, et l'échange hors bande qui met à jour plusieurs zones en une réponse.

**Critères** Le rafraîchissement ne déclenche aucun comptage sur le journal des messages, vérifié par le plan d'exécution. Une campagne de quarante mille messages affiche sa progression sans que le temps de réponse de la page augmente. La position de défilement ne bouge pas au rafraîchissement.

## T-68 · Point de contrôle du lot

**Produit** Une campagne complète de quarante mille destinataires contre le simulateur : composition avec personnalisation, aperçu, test, lancement, suivi en direct, pause, reprise, rapport final.

**Critères** Le coût réel ne dépasse pas le coût annoncé. Aucun doublon. Le rapport final réconcilie envoyés, livrés, échecs et coût. La pause et la reprise n'ont produit ni perte ni doublon.
