"""Le numéro court de la plateforme, commun à tous les clients.

Source des messages tant qu'un identifiant alphanumérique n'est pas approuvé
sur le réseau visé, et seule adresse à laquelle un destinataire peut répondre
(STOP). Factice jusqu'à son attribution par l'ARCEP : un réglage, jamais une
constante du code.
"""

from django.conf import settings


def numero_court() -> str:
    return settings.SMS_NUMERO_COURT
