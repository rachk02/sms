"""Lectures des campagnes."""

from dataclasses import dataclass

from django.db.models import Sum

from apps.credits.models import Reservation
from apps.journal.models import Compteur

from .models import Campagne

COLONNES = ("emis", "livres", "echecs", "sans_accuse", "refuses", "annules")


@dataclass(frozen=True)
class Suivi:
    # Partis vers le réseau : ils ne se rattrapent pas.
    partis: int
    livres: int
    echecs: int
    sans_accuse: int
    # Refusés avant le réseau (liste noire, expéditeur, passerelle), sans frais.
    refuses: int
    annules: int
    # Pas encore partis : en file, retenus, en cours de soumission ou pas encore publiés.
    non_soumis: int
    # Coût consommé sur la réservation, plafonné au coût annoncé.
    cout_consomme: int
    # [(réseau, expéditeur, partis)], les plus nombreux d'abord.
    par_expediteur: list

    @property
    def progression(self) -> int:
        """Part des destinataires dont le sort est réglé, en pourcentage."""
        total = self.partis + self.refuses + self.annules + self.non_soumis
        return round(100 * (total - self.non_soumis) / total) if total else 0


def suivi(campagne: Campagne) -> Suivi | None:
    """Où en est une campagne confirmée, lu sur les compteurs pré-agrégés et sur
    sa réservation : une poignée de lignes, quel que soit le nombre de messages.
    Jamais de lecture de `journal_message`."""
    if not campagne.confirmee_le or campagne.recurrente:
        return None
    lignes = (
        Compteur.objects.filter(client_id=campagne.client_id, campagne_id=campagne.id)
        .values("operateur", "expediteur_libelle")
        .annotate(**{c: Sum(c) for c in COLONNES})
    )
    totaux = dict.fromkeys(COLONNES, 0)
    par_expediteur = []
    for ligne in lignes:
        for colonne in COLONNES:
            totaux[colonne] += ligne[colonne]
        if ligne["emis"]:
            par_expediteur.append((ligne["operateur"], ligne["expediteur_libelle"], ligne["emis"]))
    reste = campagne.destinataires_prevus - totaux["emis"] - totaux["refuses"]
    if campagne.etat == Campagne.Etat.ANNULEE:
        # Tout ce qui n'est pas parti ni refusé est annulé, ou en passe de l'être.
        annules, non_soumis = max(reste, 0), 0
    else:
        annules, non_soumis = totaux["annules"], max(reste - totaux["annules"], 0)
    if campagne.terminee_le:
        cout = campagne.cout_reel
    else:
        reservation = (
            Reservation.objects.filter(campagne_id=campagne.id)
            .values_list("montant_initial", "montant_restant")
            .first()
        )
        cout = reservation[0] - reservation[1] if reservation else 0
    return Suivi(
        partis=totaux["emis"],
        livres=totaux["livres"],
        echecs=totaux["echecs"],
        sans_accuse=totaux["sans_accuse"],
        refuses=totaux["refuses"],
        annules=annules,
        non_soumis=non_soumis,
        cout_consomme=cout,
        par_expediteur=sorted(par_expediteur, key=lambda ligne: -ligne[2]),
    )


def en_direct(campagne: Campagne) -> bool:
    """Vrai tant que la campagne peut encore bouger : le suivi se rafraîchit."""
    return campagne.etat in (
        Campagne.Etat.EN_ATTENTE,
        Campagne.Etat.EN_COURS,
        Campagne.Etat.EN_PAUSE,
    ) or (campagne.etat == Campagne.Etat.ANNULEE and not campagne.terminee_le)
