"""Rôles des agents (T-95) : chaque écran du back-office, chaque rôle.

Les agents de ces tests ne sont pas super-administrateurs : Django accorde tout
à un super-administrateur sans rien vérifier, et le chemin des permissions ne
serait jamais parcouru. Une vue ajoutée au back-office sans être déclarée ici
fait échouer le test, comme ATTENDUS pour les routes du portail.
"""

import uuid

import pytest
from django.contrib import admin
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.backoffice import roles
from apps.backoffice.services import AttributionRefusee, attribuer_roles
from apps.comptes.services import creer_utilisateur
from apps.core.models import Audit

pytestmark = pytest.mark.django_db(transaction=True)

SUPER = "super-administrateur"
SANS_ROLE = "sans rôle"
PROFILS = (roles.SUPPORT, roles.EXPLOITATION, roles.FINANCE, SUPER, SANS_ROLE)

# Vues réécrites hors des écrans standard : route, droit exigé, avec ou sans
# identifiant. SUPER : réservée au super-administrateur.
PERSONNALISEES = {
    "comptes_client_cloturer": ("backoffice.cloturer_client", True),
    "comptes_client_rembourser": ("backoffice.rembourser_client", True),
    "comptes_client_suspendre": ("backoffice.suspendre_client", True),
    "comptes_client_reactiver": ("backoffice.suspendre_client", True),
    "comptes_client_crediter": ("backoffice.crediter_client", True),
    "comptes_utilisateur_reinitialiser_second_facteur": (
        "backoffice.reinitialiser_second_facteur",
        True,
    ),
    "comptes_utilisateur_roles": (SUPER, True),
    "paiements_facture_avoir": ("backoffice.emettre_avoir", True),
    "backoffice_dossierpreuve_client": ("backoffice.view_dossierpreuve", False),
    "conformite_alertefraude_traiter": ("conformite.change_alertefraude", True),
    "support_ticket_saisir": ("support.add_ticket", False),
    "support_ticket_traiter": ("support.change_ticket", True),
    "credits_grille_version_ajouter": (SUPER, True),
    "credits_grille_version": (SUPER, True),
    "credits_grille_version_publier": (SUPER, True),
    "telecom_incidentliaison_supervision": ("telecom.view_incidentliaison", False),
    "telecom_incidentliaison_supervision_fragment": ("telecom.view_incidentliaison", False),
}
STANDARD = ("_changelist", "_add", "_change", "_delete", "_history")


def _droits(profil) -> set[str]:
    if profil in (SUPER, SANS_ROLE):
        return set()
    return {f"{app}.{codename}" for app, codename in roles.codenames(profil)}


@pytest.fixture
def agents(en_tant_que_backoffice):
    crees = {}
    for rang, profil in enumerate(PROFILS):
        agent = creer_utilisateur(
            courriel=f"agent{rang}@el-immeka.test",
            mot_de_passe="une-phrase-solide",
            nom=profil,
            is_staff=True,
            is_superuser=profil == SUPER,
        )
        if profil in roles.ROLES:
            agent.groups.add(Group.objects.get(name=profil))
        crees[profil] = agent
    return crees


def test_les_groupes_portent_exactement_leurs_droits(agents):
    for role in roles.ROLES:
        groupe = Group.objects.get(name=role)
        portes = {f"{p.content_type.app_label}.{p.codename}" for p in groupe.permissions.all()}
        assert portes == _droits(role)
    # Rejouée, la synchronisation ne change rien.
    roles.synchroniser()
    assert Group.objects.filter(name__in=roles.ROLES).count() == 3


def test_chaque_vue_personnalisee_est_declaree():
    trouvees = {
        url.name
        for modele_admin in admin.site._registry.values()
        for url in modele_admin.get_urls()
        if url.name and not url.name.endswith(STANDARD) and url.name != "autocomplete"
    }
    assert trouvees == set(PERSONNALISEES)


@pytest.mark.parametrize("profil", PROFILS)
def test_chaque_liste_s_ouvre_selon_le_role(client, agents, profil):
    client.force_login(agents[profil])
    droits = _droits(profil)
    for modele in admin.site._registry:
        app, nom = modele._meta.app_label, modele._meta.model_name
        attendu = profil == SUPER or bool({f"{app}.view_{nom}", f"{app}.change_{nom}"} & droits)
        reponse = client.get(reverse(f"admin:{app}_{nom}_changelist"))
        assert (reponse.status_code == 200) == attendu, (profil, app, nom, reponse.status_code)
        if not attendu:
            assert reponse.status_code == 403


@pytest.mark.parametrize("profil", PROFILS)
@pytest.mark.parametrize("nom", sorted(PERSONNALISEES))
def test_chaque_vue_personnalisee_refuse_sans_le_droit(client, agents, profil, nom):
    droit, avec_identifiant = PERSONNALISEES[nom]
    client.force_login(agents[profil])
    adresse = reverse(f"admin:{nom}", args=[uuid.uuid4()] if avec_identifiant else [])
    statut = client.get(adresse).status_code
    autorise = profil == SUPER or droit in _droits(profil)
    # Autorisé, l'objet inventé est introuvable ou la page renvoie au menu.
    assert (statut == 403) != autorise, (profil, nom, statut)


@pytest.mark.parametrize(
    ("profil", "nom"),
    [(roles.SUPPORT, "client"), (roles.FINANCE, "transaction"), (roles.EXPLOITATION, "client")],
)
def test_les_ajouts_reecrits_verifient_le_droit(client, agents, profil, nom):
    app = "comptes" if nom == "client" else "paiements"
    client.force_login(agents[profil])
    statut = client.get(reverse(f"admin:{app}_{nom}_add")).status_code
    assert (statut == 200) == (f"{app}.add_{nom}" in _droits(profil))


def test_seul_un_super_administrateur_attribue_des_roles_et_jamais_a_lui_meme(agents):
    cible, chef = agents[SANS_ROLE], agents[SUPER]
    with pytest.raises(AttributionRefusee, match="super-administrateur"):
        attribuer_roles(cible, [roles.FINANCE], auteur=agents[roles.SUPPORT], motif="Renfort")
    with pytest.raises(AttributionRefusee, match="propres rôles"):
        attribuer_roles(chef, [roles.FINANCE], auteur=chef, motif="Moi")
    with pytest.raises(AttributionRefusee, match="motif"):
        attribuer_roles(cible, [roles.FINANCE], auteur=chef, motif=" ")

    attribuer_roles(cible, [roles.FINANCE, roles.SUPPORT], auteur=chef, motif="Renfort")
    cible = type(cible).objects.get(pk=cible.pk)
    assert cible.has_perm("backoffice.emettre_avoir") and cible.has_perm("comptes.add_client")
    assert not cible.has_perm("conformite.change_fenetre")
    trace = Audit.objects.get(action="backoffice.roles")
    assert trace.valeur_avant == {"roles": []}
    assert trace.valeur_apres == {"roles": [roles.FINANCE, roles.SUPPORT]}
