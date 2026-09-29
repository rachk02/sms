"""Points d'entrée des workers.

Les workers ne sont pas des modules métier : ce sont des processus qui chargent
le projet, consomment une file et appellent les mêmes services que les vues.
Chacun se lance par `python -m workers.<nom>`.
"""
