"""Ordonnanceur : relais des événements, partitions à venir, puis au fil des lots
les envois programmés, les campagnes en attente de fenêtre et les rapprochements.

Un seul exemplaire actif. Le verrou consultatif PostgreSQL pris au démarrage
l'assure : un second exemplaire échoue à le prendre et reste en veille, prêt à
reprendre si le premier meurt, sa session et donc son verrou disparaissant avec
lui.
"""

import asyncio
import time

from .base import Worker, installer_arret, journal, lancer

CLE_VERROU = 72_016_001


def prendre_verrou(using: str = "default") -> bool:
    from django.db import connections

    with connections[using].cursor() as curseur:
        curseur.execute("SELECT pg_try_advisory_lock(%s)", [CLE_VERROU])
        return curseur.fetchone()[0]


def constater_sans_accuse(maintenant=None, lots_max: int = 20) -> int:
    """Constate les messages restés sans accusé, lot par lot, jusqu'à épuisement
    ou `lots_max` lots : le passage suivant reprend le reste. Renvoie le nombre
    de messages constatés."""
    from apps.journal import services

    total = 0
    for _ in range(lots_max):
        lot = services.constater_sans_accuse(maintenant)
        total += len(lot)
        if len(lot) < services.TAILLE_SANS_ACCUSE:
            break
    if total:
        journal.info("%s messages passés sans accusé.", total)
    return total


# Envois hors campagne réglés à la livraison dont le message a atteint un état
# final : la réservation propre au message est encore active. Lecture par la clé
# exacte du message, date de réception comprise.
UNITAIRES_A_SOLDER = """
SELECT r.client_id, r.message_id, m.cout_total, m.etat
FROM credits_reservation AS r
JOIN journal_message AS m ON m.id = r.message_id AND m.date_reception = r.message_date_reception
WHERE r.etat = 'ACTIVE' AND r.mode = 'LIVRAISON' AND r.message_id IS NOT NULL
  AND m.etat = ANY(%s)
ORDER BY r.cree_le
LIMIT %s
"""


def solder_envois_unitaires(taille: int = 500) -> int:
    """Règle les envois hors campagne réglés à la livraison qui ont leur état
    final. Le pont entre le journal et les crédits, qui ne s'importent pas l'un
    l'autre. Renvoie le nombre d'envois réglés."""
    from django.db import connection

    from apps.core.etats import ETATS_TERMINAUX
    from apps.credits.services import solder_final

    with connection.cursor() as curseur:
        curseur.execute(UNITAIRES_A_SOLDER, [sorted(ETATS_TERMINAUX), taille])
        lignes = curseur.fetchall()
    for client_id, message_id, cout, etat in lignes:
        solder_final(client_id=client_id, message_id=message_id, montant=cout, etat=etat)
    return len(lignes)


class Ordonnanceur(Worker):
    nom = "ordonnanceur"
    attente_verrou_s = 5.0
    periode_relais_s = 1.0
    periode_partitions_s = 3_600.0
    periode_supervision_s = 15.0
    periode_sans_accuse_s = 60.0
    periode_rapprochement_s = 86_400.0
    periode_paiements_s = 60.0
    periode_reprise_paiements_s = 900.0

    async def _attendre(self, secondes: float) -> None:
        try:
            await asyncio.wait_for(self.arret.wait(), timeout=secondes)
        except TimeoutError:
            pass

    async def executer(self, installer_signaux: bool = True) -> None:
        from asgiref.sync import sync_to_async
        from django.db import connections

        from apps.campagnes.diffusion import publier as publier_campagnes
        from apps.campagnes.planification import planifier
        from apps.core import bus
        from apps.credits.rapprochement import rapprocher
        from apps.journal.services import creer_partitions_a_venir
        from apps.paiements.services import relancer as relancer_paiements
        from apps.telecom.sante import surveiller
        from apps.telecom.supervision import publier_etat_liaisons

        self.arret = asyncio.Event()
        if installer_signaux:
            installer_arret(asyncio.get_running_loop(), self.arret)
        try:
            while not self.arret.is_set():
                if await sync_to_async(prendre_verrou)():
                    break
                journal.info("Ordonnanceur en veille : un autre exemplaire est actif.")
                await self._attendre(self.attente_verrou_s)
            if self.arret.is_set():
                return
            journal.info("Ordonnanceur actif.")
            connexion = await bus.connecter()
            try:
                canal = await connexion.channel()
                # -inf : les deux tâches tournent dès le premier tour, quelle que soit
                # l'origine arbitraire de time.monotonic().
                derniere_partition = derniere_supervision = dernier_constat = float("-inf")
                dernier_rapprochement = derniere_relance = derniere_reprise = float("-inf")
                while not self.arret.is_set():
                    if time.monotonic() - derniere_partition >= self.periode_partitions_s:
                        await sync_to_async(creer_partitions_a_venir)()
                        derniere_partition = time.monotonic()
                    if time.monotonic() - derniere_supervision >= self.periode_supervision_s:
                        # Santé d'abord, pour que l'état publié soit le plus récent.
                        await sync_to_async(surveiller)()
                        await sync_to_async(publier_etat_liaisons)()
                        derniere_supervision = time.monotonic()
                    if time.monotonic() - dernier_constat >= self.periode_sans_accuse_s:
                        await sync_to_async(constater_sans_accuse)()
                        await sync_to_async(solder_envois_unitaires)()
                        dernier_constat = time.monotonic()
                    # Une fois par jour, et au démarrage : le rapprochement ne fait que
                    # lire et alerter, le refaire après un redémarrage ne coûte rien.
                    if time.monotonic() - dernier_rapprochement >= self.periode_rapprochement_s:
                        await sync_to_async(rapprocher)()
                        dernier_rapprochement = time.monotonic()
                    # Paiements en attente revérifiés, rappel reçu ou non, puis
                    # expirés au-delà du délai (T-86).
                    if time.monotonic() - derniere_relance >= self.periode_paiements_s:
                        reprise = (
                            time.monotonic() - derniere_reprise >= self.periode_reprise_paiements_s
                        )
                        await sync_to_async(relancer_paiements)(reprendre_expires=reprise)
                        derniere_relance = time.monotonic()
                        if reprise:
                            derniere_reprise = derniere_relance
                    # Envois programmés et occurrences échues, puis quelques lots
                    # par campagne à chaque tour : le relais et les autres
                    # campagnes avancent aussi.
                    await sync_to_async(planifier)()
                    await sync_to_async(publier_campagnes)()
                    await bus.relayer(canal)
                    await self._attendre(self.periode_relais_s)
            finally:
                await connexion.close()
        finally:
            await sync_to_async(connections.close_all)()
            journal.info("Ordonnanceur arrêté.")


if __name__ == "__main__":
    lancer(Ordonnanceur)
