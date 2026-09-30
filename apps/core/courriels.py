"""Envoi des courriels transactionnels.

Envoyé après la validation de la transaction qui l'a décidé : un courriel ne
part jamais pour une action annulée. Le passage par la file
`courriels.sortants`, le prestataire, les modèles versionnés et le journal des
envois viennent avec le module notifications (lot 8) ; d'ici là, l'envoi passe
par le moteur de courriel de Django, la console en développement.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

journal = logging.getLogger(__name__)


def envoyer(destinataire: str, sujet: str, texte: str) -> None:
    def partir():
        send_mail(sujet, texte, settings.DEFAULT_FROM_EMAIL, [destinataire])
        journal.info("Courriel « %s » envoyé.", sujet)

    transaction.on_commit(partir)
