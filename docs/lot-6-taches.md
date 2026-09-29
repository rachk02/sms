# Lot 6, conformité et retours réseau

Fiches F-23, F-24 et F-25, soit 13 à 20 jours-homme. Huit tâches, T-69 à T-76.

Découpage provisoire, à revoir en entrant dans le lot.

Ce lot est court mais il est celui qui protège l'entreprise. Une plateforme d'envoi de masse sans désinscription fiable et sans preuve de consentement est un risque juridique permanent.

## T-69 · Détection du STOP entrant

**Produit** La normalisation du message reçu, sans accents ni casse, et sa comparaison à la table des mots-clés : STOP, ARRET, ARRETER, et les variantes avec espaces ou ponctuation.

**Critères** Les formes stop, STOP, Stop., arrêt, ARRET et stop svp sont toutes reconnues. Un message contenant le mot stop au milieu d'une phrase n'est pas traité comme une désinscription, ce qui est le cas piégeux. La table des mots-clés se modifie sans redéploiement.

## T-70 · Rapprochement du STOP avec son émetteur

**Produit** La recherche dans l'historique d'envoi des soixante-douze dernières heures vers ce numéro, l'application au client identifié, et l'application à tous les clients concernés en cas d'ambiguïté.

**Critères** Un numéro touché par un seul client voit son STOP appliqué à ce client. Un numéro touché par trois clients dans la fenêtre voit trois entrées créées. Un STOP sans historique correspondant est conservé et signalé à l'exploitation plutôt que jeté.

## T-71 · Inscription en liste noire et accusé de désinscription

**Produit** L'écriture immédiate en liste noire avec le message d'origine conservé et l'horodatage, puis l'envoi du SMS de confirmation de désinscription.

**Critères** Le message d'origine est stocké mot pour mot, il constitue la preuve. Le SMS de confirmation part même si le compte du client émetteur est à court de crédits, parce qu'il s'agit d'une obligation et non d'un service facturé. La notification part vers le webhook du client s'il en a déclaré un.

## T-72 · Plages horaires légales

**Produit** La fenêtre autorisée du lundi au samedi, l'interdiction le dimanche et les jours fériés, et l'exclusion des messages transactionnels et des codes à usage unique.

**Critères** Une campagne marketing soumise un dimanche est mise en attente et non rejetée. Un OTP soumis le même dimanche part immédiatement. La fenêtre est configurable en base, sans redéploiement.

## T-73 · Table des jours fériés

**Produit** La table des jours fériés par pays, sa saisie depuis le back-office, et son chargement par l'ordonnanceur.

**Critères** Ajouter un jour férié en base bloque les campagnes marketing de ce jour sans redémarrage. Les jours fériés mobiles se saisissent année par année, l'écran ne prétend pas les calculer.

## T-74 · Mise en attente et libération

**Produit** L'état EN ATTENTE pour les campagnes hors fenêtre, et la libération automatique à l'ouverture suivante par l'ordonnanceur.

**Critères** Une campagne mise en attente le samedi soir part le lundi à l'heure d'ouverture, sans intervention. L'écran annonce l'heure de libération prévue, calculée et non approximative. Le client peut annuler une campagne en attente et récupérer ses crédits réservés.

## T-75 · Traçabilité du consentement

**Produit** La conservation de l'origine de chaque contact, import, API ou saisie, de sa date d'entrée, et du consentement lorsqu'il est déclaré, avec l'écran qui permet de répondre à une réclamation.

**Critères** Pour un numéro donné, un agent retrouve en trois clics quel client l'a importé, quand, par quel moyen, et combien de messages il a reçus. C'est exactement ce que demandera l'ARCEP ou un abonné mécontent, et le délai de réponse est le critère.

## T-76 · Point de contrôle du lot

**Produit** Un scénario complet : envoi vers un numéro, réception d'un STOP sous une forme inhabituelle, désinscription effective, tentative de renvoi bloquée, et production du dossier de preuve pour ce numéro.

**Critères** Le second envoi vers ce numéro est bloqué au filtrage avant soumission, pas seulement à la constitution de la campagne. Le dossier de preuve contient l'origine du contact, la date d'entrée, la liste des messages reçus et le message de désinscription d'origine.
