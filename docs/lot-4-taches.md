# Lot 4, contacts et qualité des données

Fiches F-12 à F-16, soit 27 à 42 jours-homme. Douze tâches, T-43 à T-54.

Découpage provisoire, à revoir en entrant dans le lot.

C'est le lot où le volume apparaît pour la première fois. Deux millions de lignes dans un fichier interdisent toutes les approches naïves, et la plupart des erreurs commises ici ne se voient qu'en production.

## T-43 · Modèle de contact et normalisation à l'écriture

**Produit** La table des contacts avec le numéro normalisé en colonne dédiée, l'opérateur présumé, les dix champs personnalisés en jsonb, et l'unicité garantie par contrainte de base.

**Critères** Deux insertions simultanées du même numéro pour le même client produisent une seule ligne, l'autre recevant une erreur de contrainte. Le test lance les deux dans deux transactions concurrentes. Une vérification applicative seule ne suffit pas et la tâche est ratée si elle s'y limite.

## T-44 · Classification par opérateur

**Produit** La table des préfixes chargée en base et modifiable sans redéploiement, la déduction de l'opérateur, et le marquage présumé tant qu'un accusé n'a pas confirmé.

**Critères** Ajouter une tranche de préfixes en base change le comportement sans redémarrage. Un numéro dont l'opérateur a été confirmé par un accusé n'est plus marqué présumé. Aucun préfixe n'est écrit dans le code.

## T-45 · Lecture en flux des fichiers d'import

**Produit** La lecture ligne à ligne du CSV, le mode lecture seule pour le tableur, l'extraction par motifs pour le traitement de texte, sans jamais charger le fichier en mémoire.

**Critères** Un fichier de deux millions de lignes est parcouru avec une consommation mémoire stable, mesurée et consignée. Le test échoue si la mémoire croît avec le nombre de lignes. Un fichier corrompu au milieu s'arrête proprement avec le numéro de ligne fautif.

## T-46 · Assistant de mappage des colonnes

**Produit** L'aperçu des vingt premières lignes, la proposition automatique de correspondance, la correction manuelle, et la mémorisation du mappage pour le prochain import du même format.

**Critères** Un fichier dont les entêtes sont absents se mappe quand même, par position. Un mappage enregistré est proposé par défaut au fichier suivant. Aucun import ne démarre sans mappage confirmé.

## T-47 · Traitement de l'import en worker

**Produit** L'écriture par paquets dans une table de transit, la normalisation, le dédoublonnage, la bascule en table définitive, avec suivi de progression écrit au fil de l'eau.

**Critères** La progression affichée correspond aux lignes réellement traitées, pas à une estimation. Un import interrompu reprend ou échoue proprement, sans laisser de lignes à moitié intégrées. Le compte final, acceptées plus rejetées plus doublons, égale le nombre de lignes du fichier.

## T-48 · Rapport de rejets

**Produit** La table des lignes rejetées avec numéro de ligne, contenu et motif, et l'écran de rapport téléchargeable.

**Critères** Chaque rejet porte un motif explicite et non un code. Le rapport se télécharge en CSV et se réimporte après correction. Un import entièrement rejeté n'affiche pas un écran vide mais explique la cause dominante.

## T-49 · Listes et appartenance

**Produit** La création de listes, l'ajout et le retrait manuels, l'ajout en masse depuis un import, le compteur dénormalisé tenu à jour.

**Critères** Le compteur affiché correspond au compte réel après une série d'ajouts et de retraits concurrents. Un contact retiré d'une liste reste dans le répertoire. Supprimer une liste ne supprime pas ses contacts, et la modale le dit.

## T-50 · Segments par critères

**Produit** Le constructeur de segments sur opérateur, ville, champ personnalisé et date d'entrée, avec estimation du nombre de contacts.

**Critères** L'estimation correspond au résultat réel de la diffusion, à la liste noire près. Un segment vide est signalé à la création et non au lancement de la campagne. Le calcul d'un segment sur cent mille contacts revient en moins de deux secondes.

## T-51 · Liste noire

**Produit** La table par client alimentée par ajout manuel, par import et plus tard par le STOP entrant, avec le double filtrage à la constitution de campagne et juste avant chaque soumission.

**Critères** Un numéro ajouté à la liste noire pendant qu'une campagne diffuse ne reçoit pas le message, ce que le test vérifie en insérant l'entrée en cours de diffusion. Le compteur de destinataires annoncé avant lancement tient compte de la liste noire.

## T-52 · Champs personnalisés

**Produit** La déclaration de jusqu'à dix champs par client, leur type, leur valeur de repli, et leur usage dans le mappage d'import et les segments.

**Critères** Un champ supprimé ne casse pas les segments qui l'utilisaient, ils sont désactivés avec un message. Une valeur de repli vide est refusée à la déclaration, parce que c'est elle qui évite un message adressé à personne.

## T-53 · Export et recherche de contacts

**Produit** La recherche par numéro, nom, liste, opérateur et date d'entrée, la pagination par curseur, et l'export au-delà du seuil d'affichage.

**Critères** La recherche par numéro passe par la colonne normalisée, ce qu'un plan d'exécution confirme. Aucune requête n'utilise de motif ouvert à gauche. Un export de cent mille contacts part en file et revient par lien signé.

## T-54 · Point de contrôle du lot

**Produit** Un import réel de deux cent mille lignes mélangeant les trois opérateurs, avec doublons, numéros invalides et champs personnalisés, suivi d'une segmentation et d'un export.

**Critères** L'import se termine sans dépassement mémoire, le rapport de rejets est exploitable, la répartition par opérateur correspond à la table des préfixes, et un segment construit sur ce jeu revient en moins de deux secondes.
