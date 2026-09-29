"""Contrat de la couche d'abstraction (T-19) et correspondance des statuts (T-20)."""

import inspect
import logging
import re
from datetime import datetime
from typing import ClassVar

import pytest

from apps.core.etats import Etat
from apps.telecom import passerelles
from apps.telecom.models import Connecteur
from apps.telecom.passerelles import (
    Accuse,
    EtatLiaison,
    Limites,
    Passerelle,
    PasserelleIndisponible,
    Simulateur,
    SortSoumission,
    Soumission,
    Statut,
)

FOURNISSEURS = re.compile(r"smpp|http|jasmin|kannel", re.IGNORECASE)
SOUMISSION = Soumission(numero="+22670123456", texte="Bonjour", expediteur="EXEMPLE")


class Factice(Passerelle):
    """Adaptateur minimal : le contrat, et rien d'autre."""

    correspondance: ClassVar[dict[str, Etat]] = {"OK_REMIS": Etat.LIVRE, "PERDU": Etat.NON_LIVRE}

    def soumettre(self, soumission):
        self._exiger_disponible()
        return "ref-1"

    def statut(self, soumission, reference_smsc=None):
        return Statut(SortSoumission.INCONNUE)

    def accuses_dus(self, maintenant=None):
        return []

    def etat(self):
        return EtatLiaison.CONNECTE


def _connecteur(**champs):
    return Connecteur(nom="essai", type=Connecteur.Type.SIMULE, parametres={}, **champs)


def test_le_contrat_ne_nomme_aucun_protocole_ni_aucun_fournisseur():
    textes = [inspect.getdoc(Passerelle) or ""]
    for nom in ("soumettre", "statut", "accuses_dus", "etat", "limites", "declarer_indisponible"):
        methode = getattr(Passerelle, nom)
        textes += [nom, str(inspect.signature(methode)), inspect.getdoc(methode) or ""]
    for type_echange in (Soumission, Accuse, Limites, Statut):
        textes += [type_echange.__name__, *type_echange.__dataclass_fields__]
    trouves = sorted({m.group(0) for texte in textes for m in FOURNISSEURS.finditer(texte)})
    assert trouves == [], f"le contrat nomme {trouves}"


def test_un_adaptateur_minimal_satisfait_le_contrat():
    adaptateur = Factice(_connecteur())
    assert adaptateur.soumettre(SOUMISSION) == "ref-1"
    assert adaptateur.statut(SOUMISSION).sort == SortSoumission.INCONNUE
    assert adaptateur.disponible


def test_un_adaptateur_incomplet_est_refuse():
    class Incomplet(Passerelle):
        def soumettre(self, soumission):
            return "x"

    with pytest.raises(TypeError, match="abstract"):
        Incomplet(_connecteur())


def test_deux_adaptateurs_aux_libelles_differents_donnent_le_meme_etat():
    assert Factice(_connecteur()).traduire("OK_REMIS") == Etat.LIVRE
    assert Simulateur(_connecteur()).traduire("DELIVRD") == Etat.LIVRE
    assert Factice(_connecteur()).traduire("perdu ") == Etat.NON_LIVRE


def test_un_libelle_inconnu_tombe_dans_l_etat_par_defaut_et_laisse_une_trace(caplog):
    with caplog.at_level(logging.WARNING, logger="apps.telecom.passerelles"):
        assert Simulateur(_connecteur()).traduire("ACCEPTD") == passerelles.ETAT_PAR_DEFAUT
    assert "ACCEPTD" in caplog.text and "inconnu" in caplog.text


def test_une_correspondance_vers_un_etat_qu_un_accuse_ne_peut_annoncer_est_refusee():
    with pytest.raises(TypeError, match="SANS_ACCUSE"):

        class Fautif(Factice):
            correspondance: ClassVar[dict[str, Etat]] = {"SILENCE": Etat.SANS_ACCUSE}

    with pytest.raises(TypeError, match="majuscules"):

        class Minuscules(Factice):
            correspondance: ClassVar[dict[str, Etat]] = {"delivrd": Etat.LIVRE}


def test_une_liaison_declaree_indisponible_refuse_les_soumissions():
    adaptateur = Factice(_connecteur())
    adaptateur.declarer_indisponible("maintenance opérateur")
    assert not adaptateur.disponible
    with pytest.raises(PasserelleIndisponible, match="maintenance"):
        adaptateur.soumettre(SOUMISSION)
    adaptateur.declarer_disponible()
    assert adaptateur.soumettre(SOUMISSION) == "ref-1"


def test_chaque_adaptateur_annonce_son_debit_et_ses_limites():
    limites = Factice(_connecteur(debit_max=25, sessions_max=2, fenetre=50)).limites()
    assert limites == Limites(messages_par_seconde=25, sessions=2, fenetre=50)


def test_le_simulateur_rend_le_sort_d_une_soumission():
    instant = datetime(2026, 9, 29, 12, 0)
    simulateur = Simulateur(_connecteur(), horloge=lambda: instant)
    assert simulateur.statut(SOUMISSION).sort == SortSoumission.INCONNUE
    reference = simulateur.soumettre(SOUMISSION)
    assert simulateur.statut(SOUMISSION) == Statut(SortSoumission.ACCEPTEE, reference)
    simulateur.accuses_dus(instant)
    assert simulateur.statut(SOUMISSION, reference) == Statut(
        SortSoumission.TERMINEE, reference, Etat.LIVRE
    )


def test_le_simulateur_peut_imiter_les_libelles_d_un_autre_operateur():
    simulateur = Simulateur(
        _connecteur_parametre({"libelles": {"livre": "DELIVRD", "echec": "UNDELIV"}})
    )
    simulateur.soumettre(SOUMISSION)
    assert [a.libelle_operateur for a in simulateur.accuses_dus()] == ["DELIVRD"]


def _connecteur_parametre(parametres):
    return Connecteur(nom="essai", type=Connecteur.Type.SIMULE, parametres=parametres)
