# Passerelle SMS : Jasmin ou une alternative

Note de la fiche F-03, écrite après la première prise en main réelle de Jasmin
(T-22, 29 septembre 2026). La décision revient à la direction du projet ; ce
document en donne la matière.

## Ce qui a été fait

Jasmin 0.11.0 (`jookies/jasmin`) tourne dans le docker-compose, raccordé au
RabbitMQ et au Redis de la plateforme, avec un connecteur SMPP vers le
simulateur SMPPSim. La configuration est entièrement versionnée
(`scripts/configurer_jasmin.py` pilote la console d'administration) et un
message soumis par l'API HTTP de Jasmin revient avec son accusé final, relayé
par Jasmin après rapprochement.

## Ce que la prise en main a appris

1. **Jasmin 0.11 et RabbitMQ 4 sont incompatibles par défaut.** Jasmin déclare
   des files transitoires non exclusives, que RabbitMQ 4 refuse. Elles sont
   réautorisées (`docker/rabbitmq/10-jasmin.conf`), mais RabbitMQ a annoncé le
   retrait de cette possibilité dans une version majeure à venir. Ce jour-là,
   Jasmin ne démarrera plus sans correctif de son code.
2. **La dernière version date de novembre 2023** (Python 3.11, Twisted 23.10).
   Le rythme de publication irrégulier annoncé par le plan se confirme : un
   correctif dont nous aurions besoin, il faudrait probablement l'écrire.
3. **Un défaut de Jasmin impose un redémarrage après reconfiguration.** Retirer
   puis recréer un connecteur laisse l'ancien consommateur attaché à sa file :
   les messages suivants sont rejetés (« 'NoneType' object has no attribute
   'get' »). Le script de configuration redémarre Jasmin à chaque passage.
4. **L'image ne règle que l'hôte et le port de RabbitMQ et de Redis.** Compte,
   hôte virtuel, base Redis et identifiants de la console se posent par un
   script de démarrage (`docker/jasmin/demarrer.sh`).
5. **Jasmin valide strictement les trames SMPP.** Le premier simulateur essayé,
   `ukarim/smscsim`, envoie un champ trop long ; Jasmin a refusé ses accusés.
   C'est une qualité : une trame non conforme d'un opérateur serait vue.
6. **Les limites SMPP s'appliquent partout** (mot de passe de huit caractères,
   identifiants courts). Elles viennent du protocole, pas de Jasmin.

## Options

| Option | Pour | Contre |
| --- | --- | --- |
| Garder Jasmin | Routage, files, sessions, lissage de débit et accusés déjà faits ; fonctionne | Incompatibilités à venir avec RabbitMQ, maintenance probablement à notre charge, défaut de reconfiguration |
| Kannel | Très éprouvé chez les opérateurs | C, configuration par fichiers, écosystème distinct du nôtre |
| Client SMPP maison (bibliothèque Python asynchrone) | Même langage, même bus, plus de logiciel tiers à exploiter | Sessions, fenêtre, reprise et débit à écrire et à tester soi-même |

## Recommandation

Garder Jasmin pour le lot 2, derrière la couche d'abstraction, qui rend le
changement peu coûteux : seul l'adaptateur changerait. Réexaminer avant la mise
en service, au vu de deux signaux : une nouvelle version de Jasmin compatible
avec RabbitMQ 4 sans réglage déprécié, et le comportement réel face aux SMSC
des opérateurs. Sans nouvelle version d'ici là, préparer le client SMPP maison
comme adaptateur de remplacement.

**Décision : à prendre.**
