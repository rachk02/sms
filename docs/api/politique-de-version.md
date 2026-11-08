# API publique, politique de version

Écrite le 8 novembre 2026 pour T-105, avant la première route publique. Elle fait
foi pour toute route sous `/v1/` ; une route qui la contredirait se corrige, pas
la politique. Source : la fiche F-46 du plan d'implémentation et la section « API
publique et webhooks » de l'architecture backend.

Les points marqués « à confirmer » sont des choix faits pour avancer, à valider
par l'utilisateur avant l'ouverture de l'API à un premier intégrateur.

## La version est dans le chemin

Toute route publique commence par sa version majeure : `/v1/messages`,
`/v1/solde`. Il n'existe pas d'en-tête de version en requête, pas de version
par compte, pas de version datée : l'intégrateur lit la version dans l'adresse
qu'il appelle, et rien d'autre ne la change.

**Une seule version majeure est en service à la fois**, hors période de
transition. Pendant une transition, l'ancienne et la nouvelle répondent ensemble
jusqu'à la date de retrait de l'ancienne.

Chaque réponse porte l'en-tête `Api-Version: 1`, pour le diagnostic : un ticket
de support qui joint une réponse dit d'emblée quelle version a répondu.

## Ce qui est une rupture

Une rupture est un changement qui peut faire échouer une intégration écrite
correctement contre la documentation de la version. Elle ne se publie jamais
dans une version en service : elle attend la version majeure suivante.

| Changement | Classe |
| --- | --- |
| Retirer un champ de réponse, une route, un paramètre, un événement de webhook | Rupture |
| Renommer un champ, changer son type, son unité ou son format (un montant entier qui deviendrait décimal, une date qui changerait de fuseau) | Rupture |
| Rendre obligatoire un paramètre facultatif, resserrer une validation qui acceptait jusque-là | Rupture |
| Changer la signification d'un état existant (`LIVRE`, `NON_LIVRE`, `EXPIRE`...) ou d'un code d'erreur | Rupture |
| Changer le code HTTP d'un cas existant (un 202 qui deviendrait 200, un 422 qui deviendrait 400) | Rupture |
| Changer le calcul d'un coût ou d'un solde exposé | Rupture, annoncée comme décision d'argent |
| Ajouter une route, un champ de réponse facultatif, un paramètre facultatif, un en-tête de réponse | Non cassant, publié sans préavis |
| Ajouter un événement de webhook (un point de réception ne reçoit que ceux auxquels il s'abonne) | Non cassant |
| Ajouter une valeur à une liste fermée (un état de message, un code d'erreur) | Non cassant, annoncé à l'avance, à condition que l'intégrateur traite les valeurs inconnues (voir plus bas) |
| Assouplir une validation, allonger une limite de longueur | Non cassant |
| Corriger un comportement qui contredisait la documentation | Non cassant, annoncé au journal des modifications |

Un champ facultatif qui apparaît dans une réponse n'est pas une rupture **à la
condition** que les intégrateurs ignorent les champs qu'ils ne connaissent pas.
Cette condition est écrite dans la documentation, en tête du guide
d'intégration, avec la même force qu'une règle d'authentification.

## Ce que la documentation demande aux intégrateurs

Le texte suivant figure tel quel en tête de la documentation de l'API (T-110) :

> Ignorez les champs que vous ne connaissez pas. Une réponse peut gagner un
> champ à tout moment, sans changement de version : ne validez pas les réponses
> contre un schéma strict qui refuserait un champ supplémentaire, et ne
> reconstruisez pas un objet en exigeant qu'il n'ait que les champs documentés.
>
> Prévoyez une branche par défaut pour toute liste fermée. Un état de message
> ou un code d'erreur nouveau peut apparaître ; il est annoncé à l'avance au
> journal des modifications, mais votre code ne doit pas tomber s'il le reçoit
> avant d'avoir été mis à jour. Traitez un état inconnu comme un état non final,
> et un code d'erreur inconnu selon son code HTTP.
>
> Ne dépendez pas de l'ordre des champs dans une réponse, ni de l'ordre des
> en-têtes.

Côté plateforme, la règle est symétrique : un champ inconnu dans le corps d'une
requête est ignoré, il ne fait pas échouer l'appel. C'est ce qui permet à un
intégrateur d'envoyer un champ que la version suivante comprendra.

## Le cycle de vie d'une version

1. **En service.** La version reçoit les ajouts non cassants.
2. **Dépréciée.** La version suivante est publiée ; l'ancienne continue de
   répondre à l'identique. La dépréciation s'annonce de trois façons à la fois :
   - le journal des modifications publié, daté ;
   - un courriel aux administrateurs de chaque compte qui a appelé la version
     dans les quatre-vingt-dix derniers jours ;
   - des en-têtes sur **chaque** réponse de la version dépréciée :
     - `Deprecation: @<instant>` (RFC 9745), l'instant de la dépréciation en
       secondes depuis l'époque Unix ;
     - `Sunset: <date HTTP>` (RFC 8594), la date de retrait, dès qu'elle est
       fixée ;
     - `Link: <adresse du guide de migration>; rel="deprecation"`.
3. **Retirée.** À la date de retrait, la version répond `410 Gone` avec le code
   d'erreur `VERSION_RETIREE` et le lien du guide de migration, sur toutes ses
   routes. Le retrait se décide sur la liste des comptes qui l'appellent encore,
   jamais à l'aveugle.

Une route seule peut être dépréciée dans une version en service, quand elle est
remplacée par une autre dans la même version : mêmes en-têtes, même calendrier.

**Durée de maintien : douze mois** entre l'annonce de la dépréciation et le
retrait. C'est l'usage sur ce type de service ; moins expose aux départs des
clients intégrés, davantage oblige à maintenir deux implémentations. À
confirmer, c'est le point que le plan laisse à trancher.

**Relances** par courriel à quatre-vingt-dix, trente et sept jours du retrait,
aux seuls comptes qui appellent encore la version. À confirmer.

**Exception de sécurité.** Une route qui expose une faille peut être coupée sans
préavis. Les comptes concernés sont prévenus directement, et la coupure est
écrite au journal des modifications avec sa raison. L'exception ne sert jamais
une refonte.

## Savoir qui casserait avant de publier

Chaque appel authentifié note, sur la clé qui l'a fait, la version appelée et
l'instant (`comptes_cle_api.derniere_version`, à côté de
`derniere_utilisation_le`, ajoutés par T-106). Avant toute dépréciation, la
liste des comptes à prévenir se tire de ces deux colonnes : une clé qui n'a pas
appelé la version depuis quatre-vingt-dix jours n'est pas comptée comme
utilisatrice. Rien du contenu des requêtes n'est conservé pour cela.

## Le journal des modifications

`docs/api/journal-des-modifications.md`, publié avec la documentation. Une ligne
par changement visible d'un intégrateur : date, version, classe (non cassant,
annoncé, rupture), description, lien vers la section concernée de la
documentation. Il s'écrit dans le même commit que le changement qu'il décrit.

## Ce qui n'est pas versionné ici

- Le format des webhooks suit la version de l'API qui les a déclarés : un point
  de réception déclaré en `v1` reçoit des corps `v1`, jusqu'au retrait de `v1`.
- Le bac à sable (T-113) répond exactement comme la production, version
  comprise : il n'a pas de calendrier propre.
- Les routes internes (rappels de Jasmin sous `/rappels/jasmin/`) ne sont pas
  publiques ; elles changent avec le code qui les appelle et n'entrent pas dans
  cette politique.
