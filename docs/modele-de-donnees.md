# Modèle de données

Version du 29 septembre 2026. Ce document fait foi sur les noms de tables, de
champs et de contraintes. Un champ qui n'y figure pas ne s'invente pas : il se
demande.

Conventions valables partout. La clé primaire est un UUID nommé `id`. Toute
table métier porte `client_id` et les colonnes `cree_le` et `modifie_le`. Les
montants sont en entiers de FCFA, jamais en flottants. Les dates sont stockées
en UTC et affichées en heure de Ouagadougou. Les états sont des chaînes courtes
en majuscules, contraintes par une énumération applicative et non par un type
PostgreSQL, pour qu'un ajout d'état ne demande pas de migration de type.

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
`motif_rejet` en texte libre, `palier_accorde`.

`comptes_piece` : `id`, `dossier_id`, `type` parmi RCCM, IFU, PIECE_GERANT,
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

**`contacts_liste_contact`** : table de liaison, `liste_id`, `contact_id`,
`ajoute_le`. Unicité sur le couple.

**`contacts_segment`** : `id`, `client_id`, `nom`, `criteres` en jsonb,
`nombre_estime`, `calcule_le`.

**`contacts_liste_noire`** : `id`, `client_id`, `numero_normalise`, `origine`
parmi STOP_SMS, MANUEL, IMPORT, API, `message_origine` conservé tel quel,
`ajoute_le`. Unicité sur `(client_id, numero_normalise)`.

**`contacts_import`** : `id`, `client_id`, `fichier_chemin`, `nom_origine`,
`etat` parmi DEPOSE, EN_COURS, TERMINE, ECHOUE, `lignes_total`,
`lignes_acceptees`, `lignes_rejetees`, `doublons`, `mappage` en jsonb,
`demarre_le`, `termine_le`.

**`contacts_import_rejet`** : `id`, `import_id`, `numero_ligne`,
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
`date_soumission`.

| Champ | Type | Contraintes |
| --- | --- | --- |
| `id` | uuid | clé primaire |
| `client_id` | uuid | non nul |
| `campagne_id` | uuid | nul pour un envoi unitaire |
| `contact_id` | uuid | nul si envoi à un numéro libre |
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
| `cle_idempotence` | varchar(100) | |
| `date_reception` | timestamptz | non nul |
| `date_soumission` | timestamptz | clé de partitionnement |
| `date_accuse` | timestamptz | |

Index par partition : `(client_id, date_soumission desc)`,
`(client_id, numero_normalise, date_soumission desc)`, `(campagne_id, etat)`.
Unicité sur `(campagne_id, contact_id)` quand les deux sont renseignés, et sur
`(client_id, cle_idempotence)` quand elle est fournie.

**`journal_evenement`** : `id`, `message_id`, `etat`, `horodatage`, `source`
parmi API, WORKER, ACCUSE, ADMIN, `details` en jsonb. Partitionnée par mois.

**`journal_compteur`** porte les agrégats que lisent les tableaux de bord :
`client_id`, `campagne_id`, `operateur`, `heure`, `emis`, `livres`,
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

**`credits_reservation`** : `id`, `client_id`, `campagne_id`, `montant`, `etat`
parmi ACTIVE, REGLEE, LIBEREE, `cree_le`, `resolue_le`.

**`credits_grille`** : `id`, `nom`, `client_id` nul pour la grille publique,
`devise` défaut XOF.

**`credits_grille_version`** : `id`, `grille_id`, `debut`, `fin`, `publiee`.
Une contrainte d'exclusion interdit deux versions qui se chevauchent pour une
même grille.

**`credits_grille_palier`** : `id`, `version_id`, `pays`, `operateur`,
`volume_min`, `prix_unitaire`.

## telecom

**`telecom_connecteur`** : `id`, `nom`, `operateur`, `type` parmi SMPP, HTTP,
`hote`, `port`, `identifiant`, `secret_chiffre`, `debit_max`, `sessions_max`,
`fenetre`, `actif`, `etat_liaison` parmi CONNECTE, DECONNECTE, DEGRADE,
`derniere_connexion_le`.

**`telecom_route`** : `id`, `operateur`, `connecteur_id`, `priorite`,
`seuil_bascule`, `actif`.

**`telecom_prefixe`** : `id`, `pays`, `prefixe`, `operateur`, `actif`. Chargée
en base et modifiable sans redéploiement : le plan de numérotation évolue.

**`telecom_correlation`** : `id`, `reference_smsc`, `message_id`,
`connecteur_id`, `cree_le`, `expire_le`. Reprise en base de la correspondance
tenue en Redis.

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

**`conformite_consentement`** : `id`, `client_id`, `contact_id`, `source`,
`preuve` en jsonb, `obtenu_le`.

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

## support

**`support_ticket`** : `id`, `client_id`, `demandeur_id`, `categorie`,
`priorite` parmi P1, P2, P3, P4, `etat` parmi NOUVEAU, PRIS_EN_CHARGE,
ATTENTE_CLIENT, ESCALADE_N2, ESCALADE_RESEAU, RESOLU, CLOS, `objet`,
`contexte` en jsonb pour l'objet technique rattaché, `agent_id`,
`premiere_reponse_le`, `resolu_le`, `clos_le`.

**`support_message`** : `id`, `ticket_id`, `auteur_id`, `interne` booléen,
`corps`, `cree_le`.

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
