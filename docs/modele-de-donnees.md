# Modèle de données

Version du 29 septembre 2026. Ce document fait foi sur les noms de tables, de
champs et de contraintes. Un champ qui n'y figure pas ne s'invente pas : il se
demande.

Conventions valables partout. La clé primaire est un UUID nommé `id`, sauf sur
les tables partitionnées, où elle est composée de `id` et de la clé de
partitionnement, ce que PostgreSQL impose. Toute table métier porte `client_id`
et les colonnes `cree_le` et `modifie_le`, y compris les tables filles, pour que
la politique de cloisonnement s'applique sans jointure. Les montants et les prix
sont en entiers de FCFA, jamais en flottants, et aucun prix ne porte de
virgule. Les dates sont stockées en UTC et affichées en heure de Ouagadougou.
Les états sont des chaînes courtes en majuscules, contraintes par une
énumération applicative et non par un type PostgreSQL, pour qu'un ajout d'état
ne demande pas de migration de type.

Une référence vers une table d'un module de couche supérieure est un UUID
simple, indexé, sans clé étrangère : c'est noté « UUID simple » dans les tables
ci-dessous. Une référence vers une table partitionnée l'est aussi, et porte en
plus la date qui permet de retrouver la partition.

## comptes

**`comptes_client`** porte l'identité juridique de l'organisation cliente.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `raison_sociale` | varchar(200) | non nul |
| `forme` | varchar(30) | SOCIETE, ASSOCIATION, PUBLIC, DIPLOMATIQUE, LIBERALE |
| `rccm` | varchar(50) | unique si non nul |
| `ifu` | varchar(30) | unique si non nul |
| `pays` | char(2) | non nul, défaut BF |
| `palier` | varchar(20) | DECOUVERTE, VERIFIE, CONTRACTUEL |
| `etat` | varchar(20) | ACTIF, SUSPENDU, CLOTURE |
| `contact_facturation` | varchar(254) | adresse de courriel |
| `plafond_horaire` | integer | non nul, défaut selon le palier |
| `plafond_journalier` | integer | non nul |
| `cloture_le` | timestamptz | nul tant que le compte vit |

**`comptes_utilisateur`** étend le modèle utilisateur de Django. Le courriel est
l'identifiant de connexion, il n'y a pas de nom d'utilisateur séparé.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `client_id` | uuid | référence `comptes_client` |
| `courriel` | varchar(254) | unique, non nul |
| `mot_de_passe` | varchar(255) | condensat Argon2id |
| `nom` | varchar(150) | non nul |
| `telephone` | varchar(20) | forme normalisée |
| `role` | varchar(30) | PROPRIETAIRE, ADMIN, OPERATEUR, COMPTABLE, LECTEUR |
| `courriel_verifie_le` | timestamptz | nul tant que non vérifié |
| `second_facteur_actif` | boolean | défaut faux |
| `second_facteur_secret` | varchar(255) | chiffré au repos |
| `derniere_connexion_le` | timestamptz | |
| `echecs_consecutifs` | smallint | défaut zéro |
| `verrouille_jusqu_a` | timestamptz | nul hors verrouillage |
| `langue` | char(2) | défaut fr |

Un index unique sur `courriel`. Un index sur `(client_id, role)`.

Cette table est la seule table portant `client_id` qui n'est pas soumise au
cloisonnement par ligne : la connexion la lit par courriel avant que le client
soit connu. Ses lectures passent par `selectors.py` et filtrent toujours sur le
client courant.

**`comptes_cle_api`**

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `client_id` | uuid | référence `comptes_client` |
| `nom` | varchar(100) | non nul |
| `prefixe` | varchar(12) | unique, stocké en clair, sert à retrouver la clé |
| `condensat` | varchar(255) | Argon2id du secret |
| `etat` | varchar(20) | ACTIVE, REVOQUEE |
| `cree_par_id` | uuid | référence `comptes_utilisateur` |
| `derniere_utilisation_le` | timestamptz | |
| `revoquee_le` | timestamptz | |

Le secret complet a la forme `sk_live_<prefixe>_<secret>` et n'est affiché
qu'une fois, à la création.

**`comptes_dossier`** et **`comptes_piece`** portent l'onboarding vérifié.

`comptes_dossier` : `id`, `client_id`, `etat` parmi BROUILLON, DEPOSE,
EN_REVUE, APPROUVE, REJETE, `depose_le`, `decide_le`, `decide_par_id`,
`motif_rejet` parmi une liste fermée, `motif_detail` en texte libre facultatif,
`palier_accorde`.

La liste fermée des motifs de rejet : PIECE_ILLISIBLE, PIECE_EXPIREE,
NOM_NON_CONCORDANT, NUMERO_INTROUVABLE, REPRESENTANT_NON_JUSTIFIE,
ACTIVITE_INCOMPATIBLE, DOUBLON, SOUPCON_FAUX. Sans liste fermée, aucun refus
n'est mesurable ni justifiable. Le texte libre complète, il ne remplace pas.

`comptes_piece` : `id`, `client_id`, `dossier_id`, `type` parmi RCCM, IFU, PIECE_GERANT,
MANDAT, RECEPISSE, ACTE_CREATION, ACCORD_SIEGE, ATTESTATION_ORDRE,
`chemin_stockage`, `nom_origine`, `type_mime`, `taille_octets`, `expire_le`,
`etat` parmi DEPOSEE, ACCEPTEE, REFUSEE, `motif_refus`.

## expediteurs

**`expediteurs_expediteur`** : `id`, `client_id`, `libelle` de onze caractères
au maximum contraint à l'alphabet GSM, `etat` parmi BROUILLON, SOUMIS, ACTIF,
SUSPENDU, `justificatif_id` référence une pièce, `par_defaut` booléen.

Contrainte d'unicité sur `(client_id, libelle)`.

**`expediteurs_reseau`** porte une ligne par réseau, ce qui est le point
structurant : un identifiant peut être approuvé chez un opérateur et refusé
chez un autre.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `client_id` | uuid | non nul |
| `expediteur_id` | uuid | référence `expediteurs_expediteur` |
| `operateur` | varchar(20) | ORANGE, MOOV, TELECEL |
| `etat` | varchar(20) | NON_SOUMIS, EN_INSTRUCTION, APPROUVE, REFUSE, REVOQUE |
| `reference_dossier` | varchar(100) | référence chez l'opérateur |
| `depose_le` | timestamptz | |
| `decide_le` | timestamptz | |
| `motif_refus` | text | |
| `expire_le` | timestamptz | surveillé par l'ordonnanceur |
| `refus_consecutifs` | integer | défaut zéro, déclenche la suspension |

Unicité sur `(expediteur_id, operateur)`.

**`expediteurs_reserve`** : liste des noms interdits ou soumis à revue manuelle.
`id`, `libelle`, `motif`, `actif`.

## contacts

**`contacts_contact`** : `id`, `client_id`, `numero_normalise` au format
international, `numero_origine` tel que saisi, `operateur_presume`,
`operateur_confirme`, `nom`, `prenom`, `ville`, `champs` en jsonb pour les dix
champs personnalisés, `origine` parmi IMPORT, API, SAISIE, `consentement_le`,
`consentement_source`, `actif`.

Unicité sur `(client_id, numero_normalise)`, garantie par la base et non par
l'application : sur deux imports simultanés, la contrainte est le seul garde-fou
fiable. Index sur `(client_id, operateur_presume)`.

**`contacts_liste`** : `id`, `client_id`, `nom`, `description`,
`nombre_contacts` en compteur dénormalisé.

**`contacts_liste_contact`** : table de liaison, `client_id`, `liste_id`,
`contact_id`, `ajoute_le`. Unicité sur le couple.

**`contacts_segment`** : `id`, `client_id`, `nom`, `criteres` en jsonb,
`nombre_estime`, `calcule_le`.

**`contacts_liste_noire`** : `id`, `client_id`, `numero_normalise`, `origine`
parmi STOP_SMS, MANUEL, IMPORT, API, `message_origine` conservé tel quel,
`ajoute_le`. Unicité sur `(client_id, numero_normalise)`.

**`contacts_import`** : `id`, `client_id`, `fichier_chemin`, `nom_origine`,
`etat` parmi DEPOSE, EN_COURS, TERMINE, ECHOUE, `lignes_total`,
`lignes_acceptees`, `lignes_rejetees`, `doublons`, `mappage` en jsonb,
`demarre_le`, `termine_le`. Un fichier d'import pèse au plus 200 Mo, limite
appliquée par le serveur.

**`contacts_import_rejet`** : `id`, `client_id`, `import_id`, `numero_ligne`,
`contenu_ligne`, `motif`.

## campagnes

**`campagnes_campagne`** : `id`, `client_id`, `nom`, `type` parmi MARKETING,
TRANSACTIONNEL, OTP, `expediteur_id`, `modele_texte`, `encodage_prevu`,
`segments_prevus`, `etat` parmi BROUILLON, PLANIFIEE, EN_ATTENTE, EN_COURS,
EN_PAUSE, TERMINEE, ANNULEE, `politique_expediteur` parmi REFUSER, REPLI,
ATTENDRE, `planifiee_pour`, `lissage_par_minute`, `destinataires_prevus`,
`cout_estime`, `cout_reel`, `demarree_le`, `terminee_le`, `creee_par_id`.

**`campagnes_modele`** : `id`, `client_id`, `nom`, `texte`, `variables` en jsonb.

## journal

**`journal_message`** est la table de volume, partitionnée par mois sur
`date_reception`. La partition se fait sur la réception et non sur la
soumission, parce qu'un message reçu, en attente ou rejeté n'a pas encore de
date de soumission, et qu'une clé de partitionnement ne peut pas être nulle.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire composée avec `date_reception` |
| `client_id` | uuid | non nul |
| `campagne_id` | uuid | UUID simple, nul pour un envoi unitaire |
| `contact_id` | uuid | UUID simple, nul si envoi à un numéro libre |
| `numero_normalise` | varchar(20) | non nul |
| `operateur` | varchar(20) | non nul |
| `expediteur_libelle` | varchar(11) | figé à la soumission |
| `texte` | text | après personnalisation |
| `encodage` | varchar(10) | GSM7 ou UCS2 |
| `segments` | smallint | non nul |
| `cout_unitaire` | integer | figé à la soumission, en FCFA |
| `cout_total` | integer | segments multipliés par le coût unitaire |
| `etat` | varchar(20) | les dix états du cycle de vie |
| `motif_echec` | varchar(100) | |
| `connecteur_id` | uuid | référence `telecom_connecteur` |
| `reference_smsc` | varchar(100) | identifiant rendu par l'opérateur |
| `cle_idempotence` | varchar(100) | copie informative, l'unicité vit ailleurs |
| `date_reception` | timestamptz | non nul, clé de partitionnement |
| `soumission_debutee_le` | timestamptz | marqueur posé juste avant l'appel à la passerelle |
| `date_soumission` | timestamptz | nul tant que non soumis |
| `date_accuse` | timestamptz | |
| `modifie_le` | timestamptz | non nul |

`date_reception` tient lieu de `cree_le`. `soumission_debutee_le` rempli et
`date_soumission` vide signalent une soumission interrompue : au rejeu, le
worker interroge la passerelle au lieu de resoumettre.

Index par partition : `(client_id, date_reception desc)`,
`(client_id, numero_normalise, date_reception desc)`, `(campagne_id, etat)`,
et `(numero_normalise, date_reception desc)`. Ce dernier sert au rapprochement
d'un STOP reçu sur le numéro court partagé, qui cherche tous les clients ayant
écrit à ce numéro dans les soixante-douze dernières heures. Il est posé dès la
création de la table, parce qu'ajouter un index sur toutes les partitions d'une
table déjà volumineuse coûte cher.

Aucune contrainte d'unicité métier ne peut vivre sur cette table : PostgreSQL
exige qu'elle inclue la clé de partitionnement, et un doublon tombé dans un
autre mois passerait alors inaperçu. Les deux garanties vivent dans des tables
non partitionnées, `journal_destinataire` ci-dessous et `api_idempotence` dans
le module `api`.

**`journal_destinataire`** garantit qu'une campagne n'envoie jamais deux fois
au même contact, même après une reprise sur incident : `campagne_id` UUID
simple, `contact_id` UUID simple, `client_id`, `message_id`,
`message_date_reception`, `cree_le`. Clé primaire composée
`(campagne_id, contact_id)`. L'insertion se fait dans la même transaction que
celle du message ; un conflit signifie que le destinataire est déjà servi.

**`journal_evenement`** : `id`, `client_id`, `message_id` UUID simple, `etat`,
`horodatage`, `source` parmi API, WORKER, ACCUSE, ADMIN, `details` en jsonb.
Partitionnée par mois sur `horodatage`, clé primaire composée
`(id, horodatage)`.

**`journal_compteur`** porte les agrégats que lisent les tableaux de bord :
`client_id`, `campagne_id` UUID simple, `operateur`, `heure`, `emis`, `livres`,
`echecs`, `sans_accuse`, `cout`. Clé primaire composée. C'est cette table que
les rafraîchissements interrogent, jamais `journal_message`.

## credits

**`credits_registre`** est en écriture seule. Aucune ligne n'est modifiée ni
supprimée, jamais.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `client_id` | uuid | non nul |
| `type` | varchar(20) | ACHAT, CONSOMMATION, RECREDIT, AJUSTEMENT |
| `montant` | integer | signé, en FCFA |
| `solde_apres` | integer | pour contrôle, jamais pour lecture |
| `reference` | varchar(100) | transaction, message ou campagne |
| `motif` | text | obligatoire pour AJUSTEMENT |
| `auteur_id` | uuid | nul si écriture automatique |
| `cree_le` | timestamptz | non nul |

**`credits_compte`** est le cache de solde, verrouillé pendant une réservation :
`client_id` en clé primaire, `solde_acquis`, `montant_reserve`, `mis_a_jour_le`.

**`credits_reservation`** porte soit une campagne entière, soit un envoi
unitaire. Une campagne réserve en une seule fois, à son lancement, son coût
estimé au cas le plus long : c'est ce qui permet à deux campagnes lancées en
même temps de ne pas se partager un solde qui n'existe qu'une fois. Chaque
message réglé écrit ensuite sa CONSOMMATION au registre et diminue
`montant_restant`. Le reliquat est libéré quand tous les messages de la
campagne ont atteint un état final.

`id`, `client_id`, `campagne_id` UUID simple, `message_id` UUID simple,
`message_date_reception`, `montant_initial`, `montant_restant`, `etat` parmi
ACTIVE, REGLEE, LIBEREE, `cree_le`, `resolue_le`. Une contrainte impose qu'un
seul des deux, `campagne_id` ou `message_id`, soit renseigné. Unicité sur
`campagne_id` et sur `message_id` quand ils sont renseignés. Index sur
`(client_id, etat)`.

**`credits_grille`** : `id`, `nom`, `client_id` nul pour la grille publique,
`devise` défaut XOF.

**`credits_grille_version`** : `id`, `grille_id`, `debut`, `fin`, `publiee`.
Une contrainte d'exclusion interdit deux versions qui se chevauchent pour une
même grille.

**`credits_grille_palier`** : `id`, `version_id`, `pays`, `operateur`,
`volume_min`, `prix_unitaire` en FCFA entiers.

## telecom

**`telecom_connecteur`** : `id`, `nom`, `operateur`, `type` parmi SMPP, HTTP,
`hote`, `port`, `identifiant`, `secret_chiffre`, `debit_max`, `sessions_max`,
`fenetre`, `actif`, `etat_liaison` parmi CONNECTE, DECONNECTE, DEGRADE,
`derniere_connexion_le`.

**`telecom_route`** : `id`, `operateur`, `connecteur_id`, `priorite`,
`seuil_bascule`, `actif`.

**`telecom_prefixe`** : `id`, `pays`, `prefixe`, `operateur`, `actif`. Chargée
en base et modifiable sans redéploiement : le plan de numérotation évolue.

**`telecom_correlation`** : `id`, `reference_smsc`, `message_id` UUID simple,
`message_date_reception`, `connecteur_id`, `cree_le`, `expire_le`. Reprise en
base de la correspondance tenue en Redis. Unicité sur
`(connecteur_id, reference_smsc)` : deux opérateurs peuvent rendre le même
identifiant sans se mélanger. `message_date_reception` permet au worker
accusés de mettre à jour le message dans sa seule partition.

**`telecom_accuse_orphelin`** : `id`, `reference_smsc`, `connecteur_id`,
`charge_utile` en jsonb, `recu_le`. Un accusé sans correspondance n'est jamais
jeté.

**`telecom_numero_court`** : `id`, `numero`, `pays`, `operateurs` en tableau,
`attribue_le`, `expire_le`.

**`telecom_message_entrant`** : `id`, `client_id` nul tant que non rapproché,
`numero_expediteur`, `numero_destination`, `texte`, `mot_cle_reconnu`,
`traite_le`, `recu_le`.

**`telecom_mot_cle`** : `id`, `client_id` nul pour les mots-clés globaux,
`mot_cle`, `action` parmi DESINSCRIPTION, AIDE, CONTACT, `reponse_automatique`.

## paiements

**`paiements_transaction`** : `id`, `client_id`, `fournisseur` parmi
ORANGE_MONEY, MOOV_MONEY, CARTE, VIREMENT, ESPECES, `reference_transaction`,
`montant`, `credits`, `etat` parmi INITIE, EN_ATTENTE, CONFIRME, ECHOUE,
EXPIRE, REMBOURSE, `initie_le`, `confirme_le`, `expire_le`, `justificatif_id`,
`saisi_par_id` pour les paiements manuels.

Unicité sur `(fournisseur, reference_transaction)`. C'est cette contrainte qui
empêche le double crédit quand un opérateur réémet son rappel.

**`paiements_facture`** : `id`, `client_id`, `numero` unique et séquentiel par
année, `periode_debut`, `periode_fin`, `montant_ht`, `tva`, `montant_ttc`,
`emise_le`, `chemin_pdf`.

## conformite

**`conformite_jour_ferie`** : `id`, `pays`, `date`, `libelle`.

**`conformite_consentement`** : `id`, `client_id`, `contact_id` UUID simple,
`source`, `preuve` en jsonb, `obtenu_le`.

**`conformite_audit`** : `id`, `auteur_id`, `action`, `objet_type`, `objet_id`,
`valeur_avant` en jsonb, `valeur_apres` en jsonb, `motif`, `adresse_ip`,
`horodatage`. En écriture seule.

## notifications

**`notifications_notification`** : `id`, `utilisateur_id`, `evenement`,
`titre`, `corps`, `lien`, `lue_le`, `cree_le`.

**`notifications_preference`** : `id`, `utilisateur_id`, `evenement`, `canal`
parmi APPLICATION, COURRIEL, `actif`. Unicité sur le triplet.

**`notifications_courriel`** : `id`, `type`, `destinataire`, `sujet`,
`reference_prestataire`, `etat` parmi FILE, ENVOYE, REMIS, REJETE, PLAINTE,
`envoye_le`, `statut_le`. C'est la table que consulte le support quand un
client dit ne pas avoir reçu sa facture.

**`notifications_suppression`** : `id`, `adresse`, `motif` parmi REJET_DUR,
PLAINTE, `ajoute_le`.

**`notifications_modele_courriel`** : `id`, `type`, `version`, `langue`,
`sujet`, `corps_html`, `corps_texte`, `cree_le`. Unicité sur
`(type, version, langue)`. Les modèles sont versés avec le code et conservés
par version, pour savoir ce qu'a reçu un client à une date donnée.

## support

**`support_ticket`** : `id`, `client_id`, `demandeur_id`, `categorie`,
`priorite` parmi P1, P2, P3, P4, `etat` parmi NOUVEAU, PRIS_EN_CHARGE,
ATTENTE_CLIENT, ESCALADE_N2, ESCALADE_RESEAU, RESOLU, CLOS, `objet`,
`contexte` en jsonb pour l'objet technique rattaché, `agent_id`,
`premiere_reponse_le`, `resolu_le`, `clos_le`.

**`support_message`** : `id`, `client_id`, `ticket_id`, `auteur_id`, `interne`
booléen, `corps`, `cree_le`.

**`support_piece`** : `id`, `client_id`, `message_id`, `fichier_id` référence
`fichiers_fichier`, `cree_le`.

**`support_incident`** : `id`, `titre`, `composants` en tableau parmi
APPLICATION, API, ORANGE, MOOV, TELECEL, ACCUSES, RECHARGEMENT, `etat` parmi
DEGRADE, PANNE, MAINTENANCE, RETABLI, `debut_le`, `fin_le`. Table de
plateforme, sans `client_id`.

**`support_publication_incident`** : `id`, `incident_id`, `texte`, `auteur_id`,
`publiee_le`.

## api

**`api_idempotence`** conserve vingt-quatre heures la réponse produite pour un
en-tête `Idempotency-Key` : `id`, `client_id`, `cle`, `empreinte` condensat du
corps de la requête, `statut_http`, `reponse` en jsonb, `cree_le`, `expire_le`.
Unicité sur `(client_id, cle)`. Un rejeu avec la même clé et la même empreinte
renvoie la réponse stockée ; la même clé avec une autre empreinte est refusée.

## fichiers

**`fichiers_fichier`** : `id`, `client_id`, `famille` parmi PIECE, IMPORT,
EXPORT, FACTURE, ARCHIVE, `chemin`, `nom_origine`, `type_mime`,
`taille_octets`, `chiffre` booléen, `expire_le`, `supprime_le`.

Le chemin suit la convention `clients/<client_id>/<famille>/<uuid>.<ext>`, avec
un identifiant aléatoire et jamais le nom d'origine.

## Ce qui reste à trancher

La durée de rétention en ligne des partitions du journal, qui décide du moment
où elles sont détachées. La date de validité éventuelle des crédits, qui ajoute
une colonne `expire_le` sur `credits_registre` si elle est retenue. Et le
traitement de la TVA, exigible à l'achat du crédit ou à sa consommation, qui
change le calcul porté par `paiements_facture`.
