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

## core

**`core_evenement_sortant`** est la boîte d'envoi du bus d'événements : `id`,
`nom` selon la convention domaine, objet, action, `client_id` UUID simple nul
pour un événement de plateforme, `objet_id`, `auteur_id`, `donnees` en jsonb,
`cle_idempotence` unique, `echange` de destination (`evenements` pour le bus,
`sms` pour déposer dans une file de trafic, le nom servant alors de clé de
routage), `cree_le`, `publie_le` nul tant que le relais ne l'a pas publiée. `publier` y écrit dans la transaction de l'écriture métier. Ce
n'est pas une table métier : le rôle applicatif peut y écrire sans pouvoir y
lire, le relais passe par le rôle worker.

**`core_evenement_traite`** : `consommateur`, `cle_idempotence`, `traite_le`.
Clé primaire composée sur les deux premiers. Écrite dans la même transaction
que le traitement : un événement relivré après un traitement validé est
reconnu et ignoré.

**`core_audit`** : `id`, `auteur_id`, `client_id` du compte concerné (nul
pour une action de plateforme), `action`, `objet_type`, `objet_id`,
`valeur_avant` en jsonb, `valeur_apres` en jsonb, `motif`, `adresse_ip`,
`horodatage`. En écriture seule pour tous les rôles, par le modèle et par un
déclencheur. Le rôle applicatif y écrit sans pouvoir y lire ; le back-office la
consulte. Dans `core` pour que tout module
audite dans la transaction même de son action (décision du 30 septembre 2026).

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
| `role` | varchar(30) | ADMIN, UTILISATEUR au lancement ; PROPRIETAIRE, OPERATEUR, COMPTABLE, LECTEUR déclarés pour la suite |
| `courriel_verifie_le` | timestamptz | nul tant que non vérifié |
| `second_facteur_actif` | boolean | défaut faux |
| `second_facteur_secret` | varchar(255) | chiffré par la clé applicative `SMS_CLE_CHIFFREMENT` |
| `second_facteur_dernier_pas` | bigint | dernier pas TOTP accepté, contre le rejeu d'un code |
| `derniere_connexion_le` | timestamptz | |
| `echecs_consecutifs` | smallint | défaut zéro |
| `verrouille_jusqu_a` | timestamptz | nul hors verrouillage |
| `langue` | char(2) | défaut fr |
| `actif` | boolean | défaut vrai, exigé par l'authentification de Django |
| `agent_plateforme` | boolean | défaut faux, vrai pour un agent EL-IMMEKA, ouvre l'administration |
| `super_administrateur` | boolean | défaut faux |

Un index unique sur `courriel`, enregistré en minuscules. Un index sur
`(client_id, role)`. `client_id` est nul pour les agents EL-IMMEKA, qui
n'appartiennent à aucun compte client. Les tables de liaison aux groupes et
aux permissions de Django accompagnent ce modèle, pour l'administration.

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

`comptes_piece` : `id`, `client_id`, `dossier_id`, `type` parmi RCCM, IFU,
PIECE_GERANT, DEMANDE_ECRITE, MANDAT, RECEPISSE, ACTE_CREATION, ACCORD_SIEGE,
ATTESTATION_ORDRE, `fichier_id` UUID simple vers `fichiers_fichier` (qui porte
chemin, nom d'origine, type et taille), `expire_le`, `etat` parmi DEPOSEE,
ACCEPTEE, REFUSEE, `motif_refus`. Une pièce par type et par dossier. Pièces
attendues selon la forme : IFU, pièce du premier responsable et demande écrite
pour toutes, plus RCCM (société), récépissé (association), acte de création
(structure publique), accord de siège (représentation diplomatique) ou
attestation d'inscription à l'ordre (profession libérale).

**`comptes_code_secours`** : `id`, `utilisateur_id`, `condensat` (jamais le code), `utilise_le`,
`cree_le`. Dix codes à l'activation du second facteur, montrés une seule fois,
chacun utilisable une fois.

**`comptes_connexion`** : journal des tentatives de connexion. `id`, `utilisateur_id` nul
pour une adresse inconnue, `courriel_saisi`, `reussie`, `motif` (INCONNU, MOT_DE_PASSE,
VERROUILLE, COURRIEL_NON_VERIFIE), `adresse_ip`, `agent_utilisateur`, `horodatage`. Le
rôle applicatif y écrit sans pouvoir y lire ; le back-office la consulte.

**`comptes_numero_test`** : les numéros du bac à sable. `id`, `client_id`,
`numero_normalise`, `code_condensat` (condensat du code à six chiffres, jamais
le code), `code_expire_le` (dix minutes), `verifie_le`, `cree_le`,
`modifie_le`. Dix au plus par client, unicité sur `(client_id,
numero_normalise)`. Un compte au palier Découverte n'envoie qu'à ses numéros
vérifiés ; le moteur d'envoi le contrôle avant la passerelle et rejette tout
autre envoi (motif BAC_A_SABLE_NUMERO_NON_VERIFIE).

## expediteurs

**`expediteurs_expediteur`** : `id`, `client_id`, `libelle` de onze caractères
au maximum, lettres sans accent et chiffres avec au moins une lettre (contrainte
de la base), `etat` parmi BROUILLON, SOUMIS, ACTIF, SUSPENDU, `justificatif_id`
référence la pièce de mandat (famille PIECE, chiffrée), `par_defaut` booléen,
`revue_manuelle` et `motif_revue` quand le libellé contient un nom réservé.

Contrainte d'unicité sur `(client_id, upper(libelle))`. Un identifiant devient
ACTIF dès qu'un réseau l'a approuvé. Un nom réservé n'est jamais déposé chez un
opérateur avant qu'un agent ait levé la revue, motif au journal d'audit.

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

Unicité sur `(expediteur_id, operateur)`. Le client lit ses lignes et les crée
à la déclaration (NON_SOUMIS), sans aucune politique de modification ni de
suppression : un état réseau ne change que par le back-office, qui saisit
dépôt et décision (date, référence du dossier, motif) au journal d'audit.

**`expediteurs_reserve`** : liste des noms interdits ou soumis à revue manuelle.
`id`, `libelle` en majuscules sans signe, `motif`, `actif`. Commune à tous, lue
par chacun, écrite par le back-office seul ; un libellé qui contient un nom
réservé (« ORANGEPROMO » contient « ORANGE ») part en revue manuelle. Liste
initiale chargée par `manage.py charger_reserves`.

## contacts

**`contacts_contact`** : `id`, `client_id`, `numero_normalise` au format
international, `numero_origine` tel que saisi, `operateur_presume`,
`operateur_confirme`, `nom`, `prenom`, `ville`, `champs` en jsonb pour les dix
champs personnalisés, `origine` parmi IMPORT, API, SAISIE, `consentement_le`,
`consentement_source`, `actif`.

Unicité sur `(client_id, numero_normalise)`, garantie par la base et non par
l'application : sur deux imports simultanés, la contrainte est le seul garde-fou
fiable. Index sur `(client_id, operateur_presume)`, `(client_id, ville)`,
`(client_id, cree_le)` (pagination par curseur sur `(cree_le, id)`), et, pour la
recherche par début de numéro et de nom, `(client_id, numero_normalise)` et
`(client_id, upper(nom))` en classes d'opérateurs de motif : la collation de la
base n'est pas « C », et aucune recherche n'emploie de motif ouvert à gauche.

**`contacts_liste`** : `id`, `client_id`, `nom`, `description`,
`nombre_contacts` en compteur dénormalisé.

**`contacts_liste_contact`** : table de liaison, `client_id`, `liste_id`,
`contact_id`, `ajoute_le`. Unicité sur le couple.

**`contacts_champ`** : `id`, `client_id`, `nom` technique, `libelle`, `type`
parmi TEXTE, NOMBRE, DATE, `valeur_repli` non vide (contrainte de la base).
Dix champs au plus par client ; un champ ne prend pas le nom d'une donnée fixe
du contact ou d'un critère de segment. Le nom se calcule du libellé comme d'un
entête de colonne, si bien que l'entête « Référence client » d'un fichier
retrouve le champ du même libellé. Une colonne d'import ne nourrit qu'un champ
déclaré. Supprimer un champ laisse les valeurs sur les contacts et désactive,
avec un message, les segments qui s'en servaient.

**`contacts_segment`** : `id`, `client_id`, `nom`, `criteres` en jsonb (liste
de `{champ, operation, valeur}` : opérateur, ville, date d'entrée, liste,
champ personnalisé), `nombre_estime`, `calcule_le`, `actif`,
`motif_desactivation`. L'estimation compte les contacts actifs hors liste
noire, ce que la diffusion enverra ; un segment vide est refusé à sa création.
Les contacts d'un segment sont figés à la confirmation de la campagne. Le
filtre par client y est explicite, la confirmation passant par un rôle qui
ignore le cloisonnement. Index GIN (`jsonb_path_ops`) sur `champs`.

**`contacts_liste_noire`** : `id`, `client_id` (nul pour la liste noire de la
plateforme, qui vaut pour tous les expéditeurs), `numero_normalise`, `origine`
parmi STOP_SMS, MANUEL, IMPORT, API, RECLAMATION, `message_origine` conservé
tel quel, `motif`, `ajoute_le`. Unicité sur `(client_id, numero_normalise)`,
les valeurs nulles comptant comme égales. Un client lit ses entrées et celles
de la plateforme, n'écrit que les siennes ; la liste de la plateforme est tenue
par le back-office, chaque ajout audité. Le contrôle avant soumission est
enregistré auprès de `apps.core.controles`.

**`contacts_import`** : `id`, `client_id`, `fichier_id` UUID simple vers
`fichiers_fichier`, `nom_origine`, `etat` parmi DEPOSE (mappage à confirmer),
EN_FILE, EN_COURS, TERMINE, ECHOUE, `mappage` en jsonb (entêtes, largeur,
colonnes des champs fixes, champs personnalisés), `lignes_total`,
`lignes_acceptees`, `lignes_rejetees`, `doublons`, `erreur`, `demarre_le`,
`termine_le`. Un fichier d'import pèse au plus 200 Mo, limite appliquée par le
serveur ; son type (CSV, tableur, traitement de texte) est reconnu au contenu.
Le compte final tient toujours : acceptées plus rejetées plus doublons égale
le nombre de lignes non vides du fichier.

**`contacts_export`** : `id`, `client_id`, `courriel` du demandeur, `filtres`
en jsonb (ceux de la recherche), `etat` parmi EN_FILE, EN_COURS, TERMINE,
ECHOUE, `lignes`, `fichier_id` UUID simple vers `fichiers_fichier` (famille
EXPORT), `erreur`, `termine_le`. Produit par le worker d'import, sur la file
`import.contacts` ; le lien signé, valable sept jours, part par courriel. La
demande est tracée au journal d'audit.

**`contacts_import_rejet`** : `id`, `client_id`, `import_id`, `numero_ligne`,
`contenu_ligne`, `motif` en clair (« numéro trop court », « colonne du numéro
vide »...).

**`contacts_import_transit`** : table de transit, `import_id`, `client_id`,
`numero_ligne` et les champs du contact. Les lignes valides y sont écrites par
paquets de mille, puis versées d'un seul coup dans `contacts_contact` (un
contact par numéro, sans écraser l'existant), et la table est vidée : un import
interrompu n'a rien intégré.

## campagnes

**`campagnes_campagne`** : `id`, `client_id`, `nom`, `type` parmi MARKETING,
TRANSACTIONNEL, OTP, `expediteur_id`, `modele_texte`, `encodage_prevu`,
`segments_prevus`, `etat` parmi BROUILLON, PLANIFIEE, EN_ATTENTE, EN_COURS,
EN_PAUSE, TERMINEE, ANNULEE, `politique_expediteur` parmi REFUSER, REPLI,
ATTENDRE, `planifiee_pour`, `lissage_par_minute`, `destinataires_prevus`,
`cout_estime`, `cout_reel`, `demarree_le`, `terminee_le`, `creee_par_id`, et
`cible` en jsonb (`listes` et `segment_id`), `confirmee_le`, `replis` en jsonb (valeurs de repli des champs personnalisés figées à la confirmation). `expediteur_id`
nul : la campagne part sous le numéro court. `politique_expediteur` vaut REPLI
par défaut.

L'aperçu recalcule tout à chaque affichage. La confirmation refait le calcul et
refuse si les chiffres diffèrent de ceux que le client a lus ; sinon elle fige
les destinataires, réserve le coût annoncé en une fois et passe EN_ATTENTE.

L'ordonnanceur la passe EN_COURS au premier lot publié. EN_PAUSE arrête la
publication et la soumission : les messages déjà en file passent EN_ATTENTE au
journal, sans être soumis, et sont remis en file à la reprise. ANNULEE annule
les messages non soumis (état ANNULE) ; un message dont la soumission a
commencé n'est jamais annulé. Une campagne se clôt quand plus rien n'est à
publier ni à soumettre : `cout_reel` reçoit ce qui a été consommé sur sa
réservation, le reliquat est libéré, `terminee_le` est posé, et une campagne
EN_COURS passe TERMINEE (une campagne annulée reste ANNULEE).

**`campagnes_destinataire`** : les destinataires figés à la confirmation. Clé
primaire composée `(campagne_id, contact_id)`, `client_id`, `numero_normalise`,
`operateur` (confirmé, sinon présumé), `expediteur` retenu pour son réseau,
`action` parmi ENVOYER, REPLI (numéro court), ATTENDRE (approbation en cours).
Les contacts refusés par la politique n'y figurent pas. `contact_id` est un
UUID simple : supprimer un contact n'empêche pas une campagne de se terminer.

**`campagnes_modele`** : `id`, `client_id`, `nom` unique par client, `texte`,
`variables` en jsonb (les noms des balises du texte, dans l'ordre). Une balise
s'écrit `{prenom|cher client}` : le nom, le prénom et la ville exigent leur
valeur de repli dans la balise, un champ personnalisé a celle de sa
déclaration. Un texte n'est enregistré qu'après analyse : une balise inconnue,
sans repli ou mal fermée est refusée à l'enregistrement, pas à l'envoi.

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
| `solde_apres` | bigint | pour contrôle, jamais pour lecture |
| `reference` | varchar(100) | transaction, message ou campagne |
| `motif` | text | obligatoire pour AJUSTEMENT |
| `auteur_id` | uuid | nul si écriture automatique |
| `cree_le` | timestamptz | non nul |

**`credits_compte`** est le cache de solde, verrouillé pendant une réservation :
`client_id` en clé primaire, `solde_acquis` et `montant_reserve` en bigint,
`mis_a_jour_le`. Les soldes cumulés sont en bigint : une écriture tient en
integer, pas forcément la somme de toutes.

Le registre refuse toute modification et toute suppression à deux niveaux : le
modèle Django, et un déclencheur PostgreSQL qui vaut pour tous les rôles.

Une consommation par message au plus : les écritures CONSOMMATION de référence
message:<id>` sont uniques (index partiel). Jusqu'au lot 7, le worker d'envoi
solde chaque message après sa soumission (`credits.services.solder_message`) :
consommation sur la réservation de la campagne ou sur celle du message, rien
pour un refus avant le réseau ; une relivraison ne consomme pas deux fois.

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

**`telecom_connecteur`** : `id`, `nom` unique, `operateur`, `type` parmi SMPP,
HTTP, SIMULE, `hote`, `port`, `identifiant`, `secret_chiffre`, `debit_max`,
`sessions_max`, `fenetre`, `parametres` en jsonb pour les réglages propres à
l'adaptateur (jamais un secret), `actif`, `etat_liaison` parmi CONNECTE,
DECONNECTE, DEGRADE, `derniere_connexion_le`, `cree_le`, `modifie_le`.

Cette table porte les identifiants des liaisons SMPP. Le rôle applicatif n'en
lit ni n'en écrit aucune ligne : sécurité par ligne forcée, sans politique.
Seuls les workers la voient.

**`telecom_route`** : `id`, `operateur`, `connecteur_id`, `priorite`,
`seuil_bascule`, `actif`.

**`telecom_prefixe`** : `id`, `pays`, `prefixe`, `operateur`, `actif`. Chargée
en base et modifiable sans redéploiement : le plan de numérotation évolue.
Unicité sur `(pays, prefixe)` ; le plus long préfixe qui correspond au numéro
national l'emporte. Le rôle applicatif la lit sans pouvoir y écrire ; elle se
charge d'un fichier CSV tenu auprès de l'ARCEP (`manage.py charger_prefixes`,
par le rôle du back-office). Aucun préfixe n'est écrit dans le code.

**`telecom_correlation`** : `id`, `reference_smsc`, `message_id` UUID simple,
`message_date_reception`, `connecteur_id`, `cree_le`, `expire_le`. Reprise en
base de la correspondance tenue en Redis. Unicité sur
`(connecteur_id, reference_smsc)` : deux opérateurs peuvent rendre le même
identifiant sans se mélanger. `message_date_reception` permet au worker
accusés de mettre à jour le message dans sa seule partition.

**`telecom_accuse_orphelin`** : `id`, `reference_smsc`, `connecteur_id`,
`charge_utile` en jsonb, `recu_le`. Un accusé sans correspondance n'est jamais
jeté.

Ces deux tables, sans `client_id`, sont réservées aux workers : sécurité par
ligne forcée sans politique, comme `telecom_connecteur`. La correspondance est
écrite en base dans la transaction de la soumission, puis dans Redis une fois
cette transaction validée.

**`telecom_numero_court`** : `id`, `numero`, `pays`, `operateurs` en tableau,
`attribue_le`, `expire_le`.

**`telecom_message_entrant`** : `id`, `client_id` nul tant que non rapproché,
`connecteur_id`, `reference` rendue par la passerelle, `numero_expediteur`,
`numero_destination`, `texte`, `mot_cle_reconnu`, `traite_le`, `recu_le`.
Unicité sur `(connecteur_id, reference)` : un rappel rejoué ne crée pas de
doublon. Cloisonnée par `client_id` : un message non rapproché n'est visible
d'aucun client.

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

Le journal d'audit est passé dans `core` (voir `core_audit`).

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

Le type est reconnu au contenu (signature des premiers octets), la taille
bornée par le serveur. `chiffre` vaut vrai pour les pièces d'identité, déposées
avec le chiffrement SSE-S3 de MinIO et sa clé propre (`MINIO_KMS_SECRET_KEY`) :
le stockage les chiffre au repos, et les liens signés de quinze minutes restent
possibles, si bien qu'aucun fichier n'est servi par l'application.

## Ce qui reste à trancher

La durée de rétention en ligne des partitions du journal, qui décide du moment
où elles sont détachées. La date de validité éventuelle des crédits, qui ajoute
une colonne `expire_le` sur `credits_registre` si elle est retenue. Et le
traitement de la TVA, exigible à l'achat du crédit ou à sa consommation, qui
change le calcul porté par `paiements_facture`.
