"""Prix d'un segment, en FCFA entiers.

Provisoire jusqu'aux grilles tarifaires datées (lot 7, T-81) : un prix unique
lu dans les réglages, sur une grille fictive. Tout calcul de coût passe par
cette fonction, que T-81 remplacera sans toucher aux appelants.
"""

from django.conf import settings


def prix_segment(client_id, operateur: str) -> int:
    return settings.SMS_PRIX_SEGMENT_PROVISOIRE
