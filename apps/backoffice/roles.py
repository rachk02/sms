"""Les rôles des agents EL-IMMEKA (décision du 7 novembre 2026).

Trois rôles cumulables, portés par des groupes Django, et le super-administrateur,
porté par le drapeau `is_superuser`, qui a tout. Un agent sans aucun rôle se
connecte au back-office et n'y voit rien.

Les groupes et leurs permissions sont réécrits après chaque migration, à partir
de la table ci-dessous : elle seule fait foi, une permission ajoutée à la main
dans un groupe disparaît à la migration suivante.
"""

from django.core.exceptions import ImproperlyConfigured, PermissionDenied

SUPPORT, EXPLOITATION, FINANCE = "Support", "Exploitation", "Finance"

_VOIR = ("view",)
_TOUT = ("view", "add", "change", "delete")

# rôle : {modèle "app.modele": actions, ou permission "app.codename" seule}
ROLES = {
    SUPPORT: {
        "comptes.client": ("view", "add", "change"),
        "comptes.dossier": ("view", "change"),
        "comptes.utilisateur": _VOIR,
        "core.audit": _VOIR,
        "expediteurs.expediteur": ("view", "change"),
        "expediteurs.reserve": _TOUT,
        "backoffice.dossierpreuve": _VOIR,
        "backoffice.suspendre_client": None,
        "backoffice.cloturer_client": None,
        "backoffice.reinitialiser_second_facteur": None,
    },
    EXPLOITATION: {
        "comptes.client": _VOIR,
        "core.audit": _VOIR,
        "conformite.fenetre": _TOUT,
        "conformite.jourferie": _TOUT,
        "telecom.incidentliaison": _VOIR,
        "telecom.connecteur": ("view", "add", "change"),
        "telecom.route": _TOUT,
        "conformite.motproscrit": _TOUT,
        "conformite.reglefraude": ("view", "change"),
        "conformite.alertefraude": ("view", "change"),
    },
    FINANCE: {
        "comptes.client": _VOIR,
        "core.audit": _VOIR,
        "paiements.transaction": ("view", "add"),
        "paiements.facture": _VOIR,
        "paiements.reglage": ("view", "add", "change"),
        "backoffice.crediter_client": None,
        "backoffice.rembourser_client": None,
        "backoffice.emettre_avoir": None,
    },
}


def codenames(role: str) -> list[tuple[str, str]]:
    """(app, codename) de chaque permission d'un rôle."""
    paires = []
    for cible, actions in ROLES[role].items():
        app, nom = cible.split(".", 1)
        if actions is None:
            paires.append((app, nom))
        else:
            paires += [(app, f"{action}_{nom}") for action in actions]
    return paires


def synchroniser(using: str = "default") -> None:
    """Crée les groupes des rôles et leur donne exactement leurs permissions.
    Rejouable : appelé après chaque migration, et après chaque remise à zéro
    de la base de test."""
    from django.contrib.auth.models import Group, Permission

    for role in ROLES:
        voulues = []
        for app, codename in codenames(role):
            permission = (
                Permission.objects.using(using)
                .filter(content_type__app_label=app, codename=codename)
                .first()
            )
            if permission is None:
                raise ImproperlyConfigured(f"Rôle {role} : permission inconnue {app}.{codename}.")
            voulues.append(permission)
        groupe, _ = Group.objects.using(using).get_or_create(name=role)
        groupe.permissions.set(voulues)


def apres_migration(sender, using="default", **kwargs) -> None:
    synchroniser(using)


def exiger(request, permission: str) -> None:
    """Pour une vue du back-office hors des écrans standard : `admin_view` ne
    vérifie que le statut d'agent, pas le rôle."""
    if not request.user.has_perm(permission):
        raise PermissionDenied
