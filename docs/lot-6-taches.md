# Lot 6, conformité et retours réseau

Fiches F-23, F-24 et F-25, soit 13 à 20 jours-homme. Huit tâches, T-69 à T-76.

Découpage provisoire, à revoir en entrant dans le lot.

Ce lot est court mais il est celui qui protège l'entreprise. Une plateforme d'envoi de masse sans désinscription fiable et sans preuve de consentement est un risque juridique permanent.

## Décisions prises avant d'entrer dans le lot

Tranchées le 5 novembre 2026 par l'utilisateur.

- **Fenêtre légale initiale (T-72).** Du lundi au samedi, de 8 h à 20 h, heure
  de Ouagadougou (sans heure d'été). La fenêtre vit en base et se modifie sans
  redéploiement.
- **Campagne en cours à la fermeture de la fenêtre (T-74).** La règle légale
  l'emporte : les messages marketing pas encore soumis sont retenus comme pour
  une pause et repartent seuls à l'ouverture suivante ; l'écran annonce l'heure
  de reprise. Les messages transactionnels et les codes à usage unique ne sont
  jamais retenus.
- **SMS de confirmation du STOP gratuit, hors registre (T-71).** Message
  système envoyé sous le numéro court, inscrit au journal des messages avec un
  coût nul ; aucune réservation, aucune écriture au registre de crédits, aucun
  verrou sur le compte du client. Il part donc même si le client n'a plus de
  crédit.
- **Nombre de messages reçus, lu au dossier de preuve (T-75).** L'écran du
  back-office lit les messages du numéro sur la durée de rétention, par
  l'index numéro et date, et les compte en les affichant. Exception à
  l'interdit des compteurs calculés à la volée, limitée à cet écran d'agent,
  comme le bilan de T-64.

Tranchées le même jour, en entrant dans T-71 :

- **Une confirmation par client.** Un STOP qui vaut pour plusieurs clients
  reçoit une confirmation de chacun, qui nomme l'expéditeur du dernier message
  que ce client a soumis au numéro.
- **Texte de la confirmation** : « Vous ne recevrez plus de SMS de
  {expediteur}. Votre désinscription est enregistrée. », tenu en base sur le
  mot-clé (`reponse_automatique`), un seul segment GSM.
- **Pas de réponse à un STOP sans émetteur** : rien n'a été inscrit, rien
  n'est confirmé ; l'exploitation traite le cas.

Tranchées le 6 novembre 2026, en entrant dans T-75 :

- **Douze mois au dossier de preuve.** La durée de rétention du journal n'est
  pas encore tranchée ; le dossier compte et liste les messages des douze
  derniers mois, la borne des recherches du journal, et l'écran le dit.
- **Écran seul pour le consentement.** Le dossier montre le consentement quand
  il est déclaré, « non déclaré » sinon. Aucun chemin ne le déclare encore : il
  viendra avec l'API (lot 9) et une colonne d'import ; la table
  `conformite_consentement` attend jusque-là.

Déjà tranché par l'architecture : un STOP touché par plusieurs clients dans
les soixante-douze heures s'applique à tous, une ligne par client, la
désinscription l'emportant sur le chiffre d'affaires. Le webhook du client
(T-71) se limite pour l'instant à la publication d'un événement : les webhooks
sortants arrivent au lot 9 (T-111).

## Choix faits en cours de lot

- **Reconnaissance stricte du mot-clé (T-69).** Le texte reçu est ramené en
  majuscules, sans accent, chaque signe remplacé par une espace. Il est reconnu
  s'il n'est que le mot-clé, au plus entouré de formules de politesse (svp,
  stp, merci, s'il vous plaît, s'il te plaît). Un mot-clé épelé est reconnu
  aussi, ses lettres isolées recollées (« S T O P », « S.T.O.P merci ! »,
  forme trouvée par le point de contrôle T-76). Un mot-clé pris dans une
  phrase n'est pas reconnu. Les mots-clés vivent dans `telecom_mot_cle`, relue à
  chaque message entrant ; la plateforme y sème STOP, ARRET et ARRETER. Les
  formules de politesse sont une donnée de la langue et restent dans le code.
- **Reconnaissance à l'enregistrement (T-69).** Le mot-clé reconnu s'écrit
  avec le message entrant, dans la même instruction ; le texte reçu est gardé
  mot pour mot. Un rappel rejoué ne refait rien.

- **Rapprochement par le bus (T-70).** `telecom` annonce le STOP
  (`telecom.desinscription.recue`) dans la transaction qui écrit le message
  entrant ; le journal cherche les clients qui ont écrit au numéro et publie une
  désinscription par client (`journal.desinscription.rapprochee`) ; `contacts`
  l'inscrit sur la liste noire du client avec le message reçu mot pour mot.
  Aucun import ne remonte : c'est le chemin de `contacts.operateurs`. Chaque
  étape passe par `traiter_une_fois`.
- **Soixante-douze heures comptées sur la soumission (T-70).** Un client a
  touché le numéro s'il y a soumis un message dans les soixante-douze heures
  qui précèdent la réception du STOP, et non l'heure de son traitement, qui
  peut être retardé par les paliers de réessai. Un message jamais soumis
  (refusé avant le réseau, annulé, en attente) ne compte pas. La date de
  soumission sert parce qu'une campagne reprise après une longue pause envoie
  des messages reçus bien avant ; une borne basse de trente jours sur la date
  de réception écarte les partitions anciennes.
- **Message entrant rapproché (T-70).** Il reçoit son client s'il n'y en a
  qu'un ; pour plusieurs clients, il reste sans client, rapproché quand même
  (`traite_le` posé), et chaque client le retrouve dans sa liste noire. Un
  numéro déjà sur la liste d'un client y garde sa première preuve ; le STOP
  reçu demeure dans les messages entrants.
- **STOP sans émetteur signalé à l'exploitation (T-70).** Gardé en base, sans
  client, `traite_le` posé ; un avertissement est journalisé et la métrique
  `sms_desinscriptions_sans_emetteur_total` augmente. Un expéditeur qui ne se
  normalise pas (numéro court, pays non servi) prend le même chemin, sans
  bloquer la file.

- **Message système (T-71).** `journal_message` porte une nature, CLIENT ou
  SYSTEME. La confirmation est un message SYSTEME du client concerné, sous le
  numéro court, à coût nul, déposé en file prioritaire après l'inscription en
  liste noire. Le worker d'envoi ne la règle pas (ni réservation, ni registre,
  ni verrou du compte) et le contrôle de liste noire la laisse passer : c'est
  sa seule exemption, tous les autres contrôles s'appliquent. Un message
  système n'explique pas un STOP au rapprochement.
- **Webhook du client (T-71).** L'inscription publie
  `contacts.desinscription.inscrite` (numéro, message d'origine, date
  d'inscription) ; le journal y répond par la confirmation, et les webhooks du
  lot 9 s'y abonneront.
- **Texte semé avec les mots-clés (T-71).** `telecom_mot_cle` est cloisonnée
  avec `FORCE ROW LEVEL SECURITY` : après sa migration de cloisonnement, même
  le propriétaire ne peut plus écrire une ligne de la plateforme. Le texte de
  confirmation est donc semé par la migration qui crée la table, avant le
  cloisonnement ; il se modifie ensuite par le rôle des workers.

- **Fenêtre et jours fériés en base (T-72, T-73).** `conformite_fenetre`
  porte une plage par jour de la semaine et par pays, en heure locale ; un jour
  sans ligne est fermé, et un pays sans aucune fenêtre n'est jamais ouvert :
  l'écran annonce alors qu'aucune ouverture n'est prévue plutôt que d'inventer
  une heure. L'ouverture est incluse, la fermeture exclue. Les deux tables sont
  relues à chaque question, sans cache : un jour férié saisi prend effet
  aussitôt. Aucun jour férié n'est semé, faute de source vérifiée pour les
  dates ; la table reste vide jusqu'à la saisie d'un agent.
- **Écran d'agent (T-73).** La fenêtre et les jours fériés se tiennent dans
  l'administration du back-office ; chaque ajout, correction ou suppression
  passe par `conformite.services` et s'inscrit au journal d'audit.

- **Deux points de contrôle de la fenêtre (T-72).** L'ordonnanceur ne publie
  aucun lot d'une campagne marketing hors de la fenêtre, avant même de la
  démarrer ou de remettre en file ses messages retenus ; le worker d'envoi
  retient, comme pour une pause, tout message marketing tiré de la file hors de
  la fenêtre. À l'ouverture suivante, l'ordonnanceur remet ces messages en file
  par le chemin de la reprise (T-64). Une campagne interrompue reste EN_COURS
  avec sa vraie date de départ ; elle ne se clôt pas la nuit, ses messages
  retenus comptant comme non soumis, et sa réservation reste active. Les
  campagnes transactionnelles, les envois unitaires (codes compris), les envois
  de test et les confirmations de désinscription ne sont jamais retenus.
- **Libération annoncée (T-74).** Le suivi d'une campagne marketing qui attend
  l'ouverture annonce le jour et l'heure de la reprise, calculés sur la fenêtre
  et les jours fériés en base ; une campagne programmée hors de la fenêtre
  l'annonce dès sa programmation. Sans ouverture prévue, l'écran le dit. Une
  campagne en attente s'annule comme avant (T-64) : le reliquat de sa
  réservation revient au solde.
- **Tests indépendants de l'heure.** Une fixture de `conftest.py` tient la
  fenêtre ouverte pour toute la suite, faute de quoi une campagne testée la
  nuit ou le dimanche attendrait ; les tests marqués `fenetre_reelle` lisent la
  vraie fenêtre avec une horloge de test.

- **Dossier de preuve au back-office (T-75).** Une entrée « Dossiers de
  preuve » de l'administration (premier clic) ; la recherche d'un numéro, sous
  n'importe quelle forme (deuxième), montre chaque client qui le connaît ou lui
  a écrit : origine et date d'entrée du contact, consentement, nombre de
  messages sur douze mois, inscription en liste noire, et les mots-clés reçus
  de ce numéro ; le détail d'un client (troisième) liste ses messages et le
  message de désinscription d'origine. Lu par le rôle du back-office, qui voit
  tous les clients. Le compte exclut les confirmations de désinscription, que
  la liste montre à part.

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

## Bilan du lot, 6 novembre 2026

Les huit tâches sont faites. Le point de contrôle (T-76) est un test
automatique, `tests/test_point_de_controle_lot6.py` : une campagne atteint un
numéro ; une seconde campagne est confirmée, ce numéro compris ; le numéro
répond « S.T.O.P merci ! » par le point d'entrée des rappels de la passerelle ;
la désinscription est rapprochée, inscrite avec le message d'origine et
confirmée gratuitement ; la seconde campagne part, et le message vers ce numéro
est refusé au filtrage avant soumission (LISTE_NOIRE), non à la constitution ;
le dossier de preuve réunit l'origine du contact, sa date d'entrée, les
messages reçus et le message de désinscription d'origine. Chaque événement y
est livré deux fois, pour le rejeu. Le point de contrôle a fait trouver un
trou de T-69 : un mot-clé épelé suivi d'une politesse n'était pas reconnu.

Ce qui reste ouvert :

- La durée de rétention du journal reste à trancher ; le dossier de preuve lit
  douze mois en attendant.
- Aucun chemin ne déclare encore le consentement d'un contact : il viendra
  avec l'API (lot 9) et une colonne d'import ; `conformite_consentement`
  n'est pas créée.
- Les jours fériés du Burkina Faso sont à saisir par un agent, année par
  année : aucune date n'a été semée faute de source vérifiée.
- L'annonce `contacts.desinscription.inscrite` attend les webhooks du lot 9
  (T-111) ; un STOP sans émetteur ne reçoit pas de réponse et se traite à la
  main, signalé par la métrique `sms_desinscriptions_sans_emetteur_total`.
- Le numéro court reste factice tant que l'ARCEP ne l'a pas attribué : sans
  lui, aucun STOP n'arrive par SMS en production.
