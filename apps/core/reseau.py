"""L'adresse source d'une requête, lue de la même façon partout (T-84).

Derrière un frontal de confiance seulement (`PROXIES_DE_CONFIANCE`), c'est la
dernière adresse de X-Forwarded-For, celle que ce frontal a ajoutée. Venu
d'ailleurs, l'en-tête se forge et ne vaut rien : c'est l'adresse de la
connexion qui compte.
"""

import ipaddress

from django.conf import settings


def dans_plages(adresse: str, plages) -> bool:
    try:
        ip = ipaddress.ip_address(adresse)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(plage, strict=False) for plage in plages)


def adresse_source(request) -> str:
    adresse = request.META.get("REMOTE_ADDR", "")
    transmise = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if transmise and dans_plages(adresse, settings.PROXIES_DE_CONFIANCE):
        return transmise.split(",")[-1].strip()
    return adresse
