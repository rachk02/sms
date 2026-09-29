# Journal du projet, EL-IMMEKA SMS

Chaque remarque de l'utilisateur, chaque correction ou suggestion appliquée avec Claude Code
est consignée ici, datée et horodatée à la seconde.

Format : `[AAAA-MM-JJ HH:MM:SS] Type - Description`

Types : `Correction`, `Suggestion`, `Remarque`, `Instruction permanente`, `Suivi`

Règles, reprises de `~/.claude/CLAUDE.md` :

- Une entrée par événement notable : une remarque qui déclenche une action, une correction de
  bug, une suggestion appliquée. Pas de granularité excessive, les commandes intermédiaires
  n'ont pas à figurer ici.
- L'horodatage vient toujours de la commande système au moment de l'écriture, jamais d'une
  estimation.
- Les entrées s'ajoutent à la fin, dans l'ordre chronologique. Rien ne se réécrit.
- Ce journal complète la mémoire persistante, il ne la remplace pas : la mémoire garde les
  faits durables sur le projet, le journal garde l'historique brut, lisible tel quel.

---

[2026-09-29 14:36:08] Instruction permanente - Mise en place du journal de projet et d'un `CLAUDE.md` à la racine, à la demande de l'utilisateur, avec les deux règles de travail : journal daté, et aucune attribution d'outil dans les commits et les pull requests. Constat fait au passage : la charte du dépôt existait déjà sous `EL-IMMEKA-SMS-dossiers/00-CLAUDE.md` et annonce être « lue au début de chaque session », ce qui était faux là où elle se trouvait. Claude Code charge `CLAUDE.md` à la racine, pas un fichier numéroté dans un sous-dossier. Le `CLAUDE.md` racine l'importe désormais, sans la déplacer.
[2026-09-29 14:43:40] Instruction permanente - Initialisation du dépôt git (branche main, remote origin https://github.com/rachk02/sms.git) et création d'un .gitignore pour projet Django. Le dossier docs_of_interest/ est exclu du suivi, ainsi que .venv/, .idea/, __pycache__/, .env et .claude/settings.local.json. Aucun commit ni push à ce stade.
[2026-09-29 14:46:03] Instruction permanente - L'utilisateur demande d'ignorer pour l'instant la charte docs_of_interest/00-CLAUDE.md (import cassé dans CLAUDE.md laissé en l'état), puis de faire le premier commit et le push sur origin/main.
[2026-09-29 14:48:02] Instruction permanente - Aucun emoji et jamais de tiret cadratin (em dash), nulle part : code, commentaires, gabarits, documentation, messages de commit, descriptions de PR, journal. À la place du tiret cadratin : tiret simple, virgule, deux-points ou point-virgule.
[2026-09-29 14:50:39] Instruction permanente - Commit et push automatiques : chaque modification terminée est commitée et poussée sur origin sans demander confirmation, journal compris. Règle ajoutée à CLAUDE.md, section « Commit et push automatiques ».
