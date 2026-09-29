from django.db import transaction

from .cloisonnement import poser_client


class CloisonnementMiddleware:
    """Ouvre la transaction de la requête et y pose le client de l'utilisateur.

    Un anonyme, ou un agent EL-IMMEKA sans compte client, laisse le réglage
    vide : les politiques de cloisonnement lui rendent alors zéro ligne.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        utilisateur = getattr(request, "user", None)
        client_id = None
        if utilisateur is not None and utilisateur.is_authenticated:
            client_id = getattr(utilisateur, "client_id", None)
        with transaction.atomic():
            poser_client(client_id)
            return self.get_response(request)


class JournalisationMiddleware:
    """Pose l'identifiant de la requête, le client et l'utilisateur dans le
    contexte de journalisation, et renvoie l'identifiant au client."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .journalisation import ENTETE_REQUETE, identifiant_requete, poser, retirer

        requete_id = identifiant_requete(request.headers.get(ENTETE_REQUETE))
        utilisateur = getattr(request, "user", None)
        authentifie = utilisateur is not None and utilisateur.is_authenticated
        jetons = poser(
            requete_id=requete_id,
            client_id=getattr(utilisateur, "client_id", None) if authentifie else None,
            utilisateur_id=utilisateur.pk if authentifie else None,
        )
        try:
            reponse = self.get_response(request)
        finally:
            retirer(jetons)
        reponse[ENTETE_REQUETE] = requete_id
        return reponse
