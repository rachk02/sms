"""Porte d'écriture du module conformite. Chaque changement de la fenêtre
légale ou des jours fériés est tracé au journal d'audit : il décide de ce qui
part et de ce qui attend."""

from django.db import transaction

from apps.core.audit import auditer

from .models import Fenetre, JourFerie


def _jour_ferie(jour: JourFerie) -> dict:
    return {"pays": jour.pays, "date": jour.date.isoformat(), "libelle": jour.libelle}


def _fenetre(fenetre: Fenetre) -> dict:
    return {
        "pays": fenetre.pays,
        "jour": fenetre.jour,
        "ouverture": fenetre.ouverture.isoformat(timespec="minutes"),
        "fermeture": fenetre.fermeture.isoformat(timespec="minutes"),
    }


@transaction.atomic
def enregistrer_jour_ferie(jour: JourFerie, *, agent, adresse_ip=None) -> JourFerie:
    """Ajoute ou corrige un jour férié. Il bloque les campagnes marketing de ce
    jour dès l'enregistrement, sans redémarrage."""
    avant = None
    if not jour._state.adding:
        ancien = JourFerie.objects.filter(id=jour.id).first()
        avant = _jour_ferie(ancien) if ancien else None
    jour.full_clean()
    jour.save()
    auditer(
        "conformite.jour_ferie.enregistre",
        jour,
        auteur=agent,
        avant=avant,
        apres=_jour_ferie(jour),
        adresse_ip=adresse_ip,
    )
    return jour


@transaction.atomic
def supprimer_jour_ferie(jour: JourFerie, *, agent, adresse_ip=None) -> None:
    auditer(
        "conformite.jour_ferie.supprime",
        jour,
        auteur=agent,
        avant=_jour_ferie(jour),
        adresse_ip=adresse_ip,
    )
    jour.delete()


@transaction.atomic
def regler_fenetre(fenetre: Fenetre, *, agent, adresse_ip=None) -> Fenetre:
    """Ouvre un jour ou en change les heures."""
    avant = None
    if not fenetre._state.adding:
        ancienne = Fenetre.objects.filter(id=fenetre.id).first()
        avant = _fenetre(ancienne) if ancienne else None
    fenetre.full_clean()
    fenetre.save()
    auditer(
        "conformite.fenetre.reglee",
        fenetre,
        auteur=agent,
        avant=avant,
        apres=_fenetre(fenetre),
        adresse_ip=adresse_ip,
    )
    return fenetre


@transaction.atomic
def fermer_jour(fenetre: Fenetre, *, agent, adresse_ip=None) -> None:
    """Ferme un jour de la semaine : plus aucun envoi marketing ce jour-là."""
    auditer(
        "conformite.fenetre.fermee",
        fenetre,
        auteur=agent,
        avant=_fenetre(fenetre),
        adresse_ip=adresse_ip,
    )
    fenetre.delete()


# Dictionnaire anti-spam (T-99)


class MotRefuse(ValueError):
    pass


def _mot(mot) -> dict:
    return {
        "expression": mot.expression,
        "forme": mot.forme,
        "gravite": mot.gravite,
        "motif": mot.motif,
        "actif": mot.actif,
    }


@transaction.atomic
def enregistrer_mot_proscrit(mot, *, agent, adresse_ip=None):
    from .antispam import LETTRES_MIN, normaliser
    from .models import MotProscrit

    mot.expression = (mot.expression or "").strip()
    mot.forme = normaliser(mot.expression)
    if len(mot.forme) < LETTRES_MIN:
        raise MotRefuse(
            f"« {mot.expression} » se lit « {mot.forme} » une fois normalisé : il faut au "
            f"moins {LETTRES_MIN} lettres, sinon il se retrouverait partout."
        )
    doublon = MotProscrit.objects.filter(forme=mot.forme).exclude(id=mot.id).first()
    if doublon:
        raise MotRefuse(
            f"« {mot.expression} » se lit comme « {doublon.expression} », déjà présent."
        )
    avant = None
    if not mot._state.adding:
        avant = _mot(MotProscrit.objects.get(id=mot.id))
    mot.save()
    auditer(
        "conformite.mot_proscrit.creation"
        if avant is None
        else "conformite.mot_proscrit.modification",
        mot,
        auteur=agent,
        avant=avant,
        apres=_mot(mot),
        adresse_ip=adresse_ip,
    )
    return mot


@transaction.atomic
def supprimer_mot_proscrit(mot, *, agent, adresse_ip=None) -> None:
    auditer(
        "conformite.mot_proscrit.suppression",
        mot,
        auteur=agent,
        avant=_mot(mot),
        adresse_ip=adresse_ip,
    )
    mot.delete()
