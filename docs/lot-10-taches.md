# Lot 10, optimisation et extensions

Fiches F-37, F-38, F-39, F-40, F-41 et F-47, soit 53 à 85 jours-homme. Quatorze tâches, T-115 à T-128.

Découpage très provisoire. C'est le lot le plus éloigné, celui dont le contenu bougera le plus, et celui qui sort du périmètre de lancement en cas de retard. Chaque bloc ci-dessous est indépendant des autres et se prend ou se laisse séparément.

## Qualification des bases, F-37

### T-115 · Intégration d'un fournisseur d'interrogation réseau

**Produit** L'adaptateur vers le fournisseur retenu, le traitement par lots, et la conservation des résultats avec une durée de validité.

**Critères** Un lot de dix mille numéros part et revient sans dépasser le quota du fournisseur. Un résultat plus vieux que la durée retenue est considéré comme périmé et non comme vrai. Le coût par interrogation est compté et imputé.

### T-116 · Marquage et usage des numéros inactifs

**Produit** Le marquage des numéros inactifs, leur exclusion optionnelle des campagnes, et l'écran qui montre au client la qualité de sa base.

**Critères** Un client voit combien de numéros de sa base sont inactifs et ce que cela lui coûte. L'exclusion est un choix, jamais automatique, parce qu'une qualification peut se tromper.

## Routage dynamique, F-38

### T-117 · Surveillance glissante par route

**Produit** Le calcul du taux de livraison par route sur fenêtre glissante, et son exposition à la supervision.

**Critères** Le taux reflète la réalité avec moins de cinq minutes de retard. Une route sans trafic récent n'affiche pas un taux trompeur mais un état indéterminé.

### T-118 · Bascule automatique

**Produit** Le passage à la route secondaire sous le seuil, le retour à la route principale après rétablissement, et l'hystérésis qui évite le va-et-vient.

**Critères** Une route dégradée déclenche la bascule en moins de deux minutes. Une route qui oscille autour du seuil ne provoque pas de bascules répétées. Chaque bascule est journalisée et alertée.

### T-119 · Respect du débit par connecteur

**Produit** La limitation par connecteur, indépendante des files, pour qu'une bascule ne fasse pas dépasser le débit accordé sur la route de secours.

**Critères** Basculer tout le trafic d'un opérateur vers une route de secours ne dépasse pas le débit de cette route, les messages excédentaires attendent.

## Liens et mesure, F-39

### T-120 · Raccourcisseur d'URL

**Produit** Le domaine court dédié, la génération d'un lien unique par destinataire, et la redirection journalisée.

**Critères** Un lien fait moins de vingt-cinq caractères, sans quoi il mange un segment. La redirection répond en moins de cent millisecondes. Un lien expiré redirige vers une page explicative et non vers une erreur.

### T-121 · Mesure des clics

**Produit** L'attribution du clic au destinataire, le taux de clic par campagne, et la remontée au tableau de bord.

**Critères** Le taux de clic est calculé sur les messages livrés et non sur les messages envoyés. Les robots de prévisualisation des messageries ne sont pas comptés comme des clics humains, ce qui demande un filtrage explicite.

## Automatisations, F-40

### T-122 · Déclencheurs sur date

**Produit** Le déclenchement sur date anniversaire, échéance ou rendez-vous, à partir d'un champ de contact, avec décalage réglable.

**Critères** Un anniversaire le 29 février se déclenche les années non bissextiles selon une règle écrite. L'ordonnanceur ne déclenche qu'une fois par occurrence, même après redémarrage.

### T-123 · Déclencheurs sur événement API

**Produit** La réception d'un événement client par l'API, sa correspondance avec un scénario, et le déclenchement de l'envoi.

**Critères** Un événement rejoué ne déclenche pas deux envois. Un scénario désactivé ignore les événements sans les perdre. Le délai entre événement reçu et message soumis reste sous cinq secondes.

### T-124 · Éditeur de scénarios

**Produit** L'écran de création d'un scénario, ses conditions, ses actions et sa mise en service.

**Critères** Un scénario en boucle, qui se redéclencherait lui-même, est détecté et refusé à l'enregistrement. Un scénario se teste à blanc avant mise en service.

## Omnicanal, F-41

### T-125 · Compte et modèles WhatsApp Business

**Produit** L'ouverture du compte, la soumission des modèles de message à l'approbation, et leur suivi.

**Critères** Aucune ligne de code de bascule n'est écrite avant qu'un modèle ne soit approuvé. Le suivi des états d'approbation est visible au back-office.

### T-126 · Adaptateur WhatsApp et bascule

**Produit** Un adaptateur au contrat de la couche d'abstraction, la règle de bascule vers le SMS, et la tarification à la conversation distincte de celle du SMS.

**Critères** Le coût d'une conversation n'est jamais calculé avec la grille SMS. Une bascule vers le SMS après échec WhatsApp ne facture pas les deux, ou les facture toutes les deux selon une règle écrite et testée.

## Second pays, F-47

### T-127 · Référentiels par pays

**Produit** La table des préfixes, les grilles, les plages horaires et les règles de consentement déclinées par pays, et la sélection du pays à l'import comme à l'envoi.

**Critères** Ajouter un pays se fait entièrement en base, sans migration ni redéploiement, ce qui valide la précaution prise en F-02. Un numéro d'un pays non servi est rejeté avec un message explicite.

### T-128 · Devise et facturation multi-pays

**Produit** La devise de facturation par client, la conversion si elle est retenue, et le traitement fiscal des ventes hors Burkina Faso.

**Critères** Une facture porte une seule devise. Le taux de conversion appliqué est celui du jour de la transaction et il est conservé sur la pièce. Si la position fiscale n'est pas tranchée, la tâche ne démarre pas.
