"""Grilles tarifaires au back-office (T-98).

Une version publiée ne se modifie plus, seule une nouvelle version la remplace.
La publication laisse sa trace. Un changement de prix sans date de prise
d'effet est refusé. Seul un super-administrateur y touche, sans double
validation (décision du 6 novembre 2026).
"""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.backoffice import roles
from apps.backoffice.services import preparer_version, publier_grille_version
from apps.comptes.services import creer_utilisateur
from apps.core.models import Audit
from apps.credits import services as credits
from apps.credits.models import Grille, PalierGrille, VersionGrille

pytestmark = pytest.mark.django_db(transaction=True)

PRIX = {"ORANGE": 20, "MOOV": 19, "TELECEL": 18}


@pytest.fixture
def chef(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="chef@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Chef",
        is_staff=True,
        is_superuser=True,
    )


def _saisie_date(instant):
    return timezone.localtime(instant).strftime("%Y-%m-%dT%H:%M")


def test_une_grille_se_prepare_et_se_publie_depuis_l_ecran(client, chef):
    client.force_login(chef)
    reponse = client.post(
        "/admin/credits/grille/add/", {"nom": "Grille publique", "nature": "PUBLIQUE"}
    )
    assert reponse.status_code == 302
    grille = Grille.objects.get(nature=Grille.Nature.PUBLIQUE)

    # Sans date de prise d'effet, rien n'est préparé.
    adresse = f"/admin/credits/grille/{grille.id}/versions/ajouter/"
    assert client.post(adresse, {"debut": ""}).status_code == 200
    assert not VersionGrille.objects.exists()

    debut = timezone.now() + timedelta(days=1)
    reponse = client.post(adresse, {"debut": _saisie_date(debut), "copier": "on"})
    assert reponse.status_code == 302
    version = VersionGrille.objects.get(grille=grille)
    for operateur, prix in PRIX.items():
        reponse = client.post(
            f"/admin/credits/grille/versions/{version.id}/",
            {
                "action": "fixer",
                "pays": "BF",
                "operateur": operateur,
                "volume_min": "0",
                "prix_unitaire": str(prix),
            },
        )
        assert reponse.status_code == 302
    assert version.paliers.count() == 3

    publication = f"/admin/credits/grille/versions/{version.id}/publier/"
    page = client.get(publication).content.decode()
    assert "ne change plus" in page and VersionGrille.objects.get(id=version.id).publiee is False
    assert client.post(publication, {}).status_code == 302
    version.refresh_from_db()
    assert version.publiee and version.publiee_par == chef
    trace = Audit.objects.get(action="credits.grille.publication")
    assert {p["operateur"]: p["prix_unitaire"] for p in trace.valeur_apres["prix"]} == PRIX

    # Publiée, elle ne change plus, même par l'écran.
    palier = version.paliers.get(operateur="ORANGE")
    client.post(
        f"/admin/credits/grille/versions/{version.id}/",
        {
            "action": "fixer",
            "pays": "BF",
            "operateur": "ORANGE",
            "volume_min": "0",
            "prix_unitaire": "1",
        },
    )
    client.post(
        f"/admin/credits/grille/versions/{version.id}/",
        {"action": "retirer", "palier": str(palier.id)},
    )
    assert PalierGrille.objects.get(id=palier.id).prix_unitaire == 20
    with pytest.raises(credits.TarifInvalide, match="ne change plus"):
        credits.fixer_palier(version, pays="BF", operateur="ORANGE", prix_unitaire=1)
    with pytest.raises(credits.TarifInvalide, match="ne change plus"):
        credits.retirer_palier(palier)


def test_une_nouvelle_version_remplace_la_precedente_a_sa_date(chef):
    grille = credits.creer_grille("Grille publique", auteur=chef)
    maintenant = timezone.now()
    premiere = preparer_version(grille, agent=chef, debut=maintenant, maintenant=maintenant)
    for operateur, prix in PRIX.items():
        credits.fixer_palier(premiere, pays="BF", operateur=operateur, prix_unitaire=prix)
    publier_grille_version(premiere, agent=chef, maintenant=maintenant)

    plus_tard = maintenant + timedelta(days=7)
    seconde = preparer_version(grille, agent=chef, debut=plus_tard, maintenant=maintenant)
    # Les prix en vigueur sont repris : seul celui qui change se saisit.
    assert seconde.paliers.count() == 3
    credits.fixer_palier(seconde, pays="BF", operateur="ORANGE", prix_unitaire=22)
    publier_grille_version(seconde, agent=chef, maintenant=maintenant)
    premiere.refresh_from_db()
    assert premiere.fin == plus_tard
    assert credits.version_en_vigueur_de(grille, maintenant) == premiere
    assert credits.version_en_vigueur_de(grille, plus_tard) == seconde


def test_un_changement_de_prix_sans_date_est_refuse(chef):
    grille = credits.creer_grille("Grille publique", auteur=chef)
    with pytest.raises(credits.TarifInvalide, match="date de prise d'effet"):
        preparer_version(grille, agent=chef, debut=None)
    sans_date = credits.creer_version(grille)
    credits.fixer_palier(sans_date, pays="BF", operateur="ORANGE", prix_unitaire=20)
    with pytest.raises(credits.TarifInvalide, match="date de prise d'effet"):
        publier_grille_version(sans_date, agent=chef)
    with pytest.raises(credits.TarifInvalide, match="passé"):
        preparer_version(grille, agent=chef, debut=timezone.now() - timedelta(hours=1))


def test_seul_un_super_administrateur_touche_aux_grilles(client, chef):
    finance = creer_utilisateur(
        courriel="finance@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Finance",
        is_staff=True,
    )
    finance.groups.add(Group.objects.get(name=roles.FINANCE))
    grille = credits.creer_grille("Grille publique", auteur=chef)
    client.force_login(finance)
    assert client.get("/admin/credits/grille/add/").status_code == 403
    assert client.get(f"/admin/credits/grille/{grille.id}/change/").status_code == 403
    with pytest.raises(credits.PublicationNonAutorisee):
        preparer_version(grille, agent=finance, debut=timezone.now() + timedelta(days=1))
