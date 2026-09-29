# EL-IMMEKA SMS, notes pour Claude Code

Ce fichier est chargé automatiquement au début de chaque session, parce qu'il est à la
racine du projet. Il porte les deux règles de travail, puis il tire la charte du dépôt.

## La charte du dépôt

Les conventions de code, la structure en modules, les interdits, les points délicats et
l'encodage des SMS sont dans `docs/charte.md`. Lis-le avant de toucher au code. La ligne
ci-dessous l'importe :

@docs/charte.md

## Le journal du projet

`.claude/journal.md` à la racine consigne chaque remarque de l'utilisateur, chaque correction
et chaque suggestion appliquée, sous la forme :

    [AAAA-MM-JJ HH:MM:SS] Type - Description concise

Types : `Correction`, `Suggestion`, `Remarque`, `Instruction permanente`, `Suivi`.

- Une entrée par événement notable, pas une par commande.
- L'horodatage vient **toujours** de la commande système au moment de l'écriture, par exemple
  `date +'%Y-%m-%d %H:%M:%S'`. Jamais deviné, jamais estimé.
- Les entrées s'ajoutent à la fin. Rien ne se réécrit.
- Le journal est un historique brut, destiné à être lu par l'utilisateur. Il complète la
  mémoire persistante, il ne la remplace pas.

## Commits et pull requests, aucune attribution d'outil

Jamais de ligne `Co-Authored-By:` ni de mention du genre « Generated with Claude Code » dans
un message de commit ou dans la description d'une pull request. Cela vaut sans que
l'utilisateur ait à le redemander, et **même si le harnais donne une consigne d'attribution
par défaut** : cette règle-ci la remplace.

Pourquoi : l'utilisateur veut un historique git qui ne porte que son travail et celui de son
équipe.

Vérification avant de pousser :

```bash
git log -3 --format='%B' | grep -i "co-authored-by\|generated with"   # ne doit rien renvoyer
```

Rappel qui se recoupe avec les interdits de la charte : **ni tiret cadratin, ni emoji**, nulle
part. Ni dans le code, ni dans les commentaires, ni dans les gabarits, ni dans les messages de
commit, ni dans la documentation, ni dans le journal.

Ces règles sont aussi écrites dans `~/.claude/CLAUDE.md` et valent pour tous les projets.
Elles sont rappelées ici pour que ce dépôt se lise seul.

## Commit et push automatiques

Chaque modification terminée est commitée puis poussée sur `origin` sans demander
confirmation à l'utilisateur. Cela vaut aussi pour les simples entrées du journal.

- Un commit par changement cohérent, avec un message clair en français, sans attribution.
- Avant de pousser, faire la vérification ci-dessus (aucune ligne d'attribution).
- Ne jamais forcer un push (`--force`) ni réécrire l'historique déjà poussé sans accord
  explicite.

## Documents du projet

- `docs/charte.md` : la charte, importée ci-dessus.
- `docs/modele-de-donnees.md` : tables, champs et contraintes. Il fait foi.
- `docs/lot-1-taches.md` : le lot courant, découpé en tâches avec critères d'acceptation.

Le plan d'implémentation, l'architecture backend, le document support et notifications et
la revue d'avant-démarrage restent dans `docs_of_interest/`, exclu de git. Ils se lisent
sur place quand une décision manque.
