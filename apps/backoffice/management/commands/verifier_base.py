"""Vérifie qu'une base est complète et protégée, et compte ses lignes.

Sert après une restauration : la base restaurée doit passer tous les contrôles
et rendre les mêmes comptes que la base d'origine.

    manage.py verifier_base [--comptages fichier.json] [--comparer fichier.json]
"""

import json
import sys

from django.core.management.base import BaseCommand
from django.db import connections
from django.db.migrations.executor import MigrationExecutor

CLOISONNEES = (
    "comptes_client",
    "conformite_alerte_fraude",
    "support_ticket",
    "support_message",
    "support_piece",
    "journal_message",
    "journal_evenement",
    "journal_destinataire",
    "journal_compteur",
    "credits_registre",
    "credits_compte",
    "credits_reservation",
    "telecom_message_entrant",
    "comptes_dossier",
    "comptes_piece",
    "comptes_numero_test",
    # Plus une politique de lecture par préfixe, pour vérifier une clé.
    "comptes_cle_api",
    "api_idempotence",
    "api_point_reception",
    "api_livraison",
    "api_message_essai",
    "fichiers_fichier",
    "campagnes_campagne",
    "campagnes_destinataire",
    "campagnes_modele",
    "contacts_champ",
    "contacts_contact",
    "contacts_export",
    "contacts_import",
    "contacts_import_rejet",
    "contacts_import_transit",
    "contacts_liste",
    "contacts_liste_contact",
    "contacts_liste_noire",
    "telecom_mot_cle",
    "contacts_segment",
    "expediteurs_expediteur",
    # Lecture et création seules pour le client, lecture seule pour tous.
    "expediteurs_reseau",
    "expediteurs_reserve",
    "conformite_fenetre",
    "conformite_jour_ferie",
    "conformite_mot_proscrit",
    "conformite_regle_fraude",
    # Grille publique pour tous, grille négociée pour son client, en lecture seule.
    "credits_grille",
    "credits_grille_version",
    "credits_grille_palier",
    "paiements_transaction",
    # Lus par tous, écrits par le back-office.
    "paiements_reglage",
    "paiements_facture",
)
RESERVEES_AUX_WORKERS = (
    "telecom_connecteur",
    "telecom_correlation",
    "telecom_accuse_orphelin",
    "telecom_incident_liaison",
    "support_incident",
    "support_publication_incident",
    "telecom_route",
    "core_evenement_sortant",
    "core_audit",
    "comptes_connexion",
    "telecom_prefixe",
    "paiements_numerotation",
)


def _lignes(sql, parametres=None, using="worker"):
    with connections[using].cursor() as curseur:
        curseur.execute(sql, parametres)
        return curseur.fetchall()


class Command(BaseCommand):
    help = "Contrôle la structure et la protection de la base, et compte ses lignes."

    def add_arguments(self, parser):
        parser.add_argument("--comptages", help="Écrit les comptes par table dans ce fichier.")
        parser.add_argument("--comparer", help="Compare les comptes à ceux de ce fichier.")

    def handle(self, *args, comptages=None, comparer=None, **options):
        fautes = []

        executeur = MigrationExecutor(connections["proprietaire"])
        manquantes = executeur.migration_plan(executeur.loader.graph.leaf_nodes())
        if manquantes:
            fautes.append(f"{len(manquantes)} migration(s) non appliquée(s)")

        securite = dict(
            (nom, (active, forcee))
            for nom, active, forcee in _lignes(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                " WHERE relname = ANY(%s)",
                [list(CLOISONNEES + RESERVEES_AUX_WORKERS)],
            )
        )
        politiques = {nom for (nom,) in _lignes("SELECT tablename FROM pg_policies")}
        for table in CLOISONNEES + RESERVEES_AUX_WORKERS:
            if securite.get(table) != (True, True):
                fautes.append(f"{table} : sécurité par ligne absente ou non forcée")
        for table in CLOISONNEES:
            if table not in politiques:
                fautes.append(f"{table} : politique de cloisonnement absente")

        for description, sql in (
            (
                "fonction de partitionnement",
                "SELECT 1 FROM pg_proc WHERE proname = 'journal_creer_partition'",
            ),
            (
                "écriture seule du registre",
                "SELECT 1 FROM pg_trigger WHERE tgname = 'credits_registre_ecriture_seule'",
            ),
            (
                "écriture seule de l'audit",
                "SELECT 1 FROM pg_trigger WHERE tgname = 'core_audit_ecriture_seule'",
            ),
            (
                "partition du mois courant",
                "SELECT 1 FROM pg_class"
                " WHERE relname = 'journal_message_' || to_char(now(), 'YYYY_MM')",
            ),
        ):
            if not _lignes(sql):
                fautes.append(f"{description} absente")

        tables = [
            nom
            for (nom,) in _lignes(
                "SELECT relname FROM pg_class JOIN pg_namespace ON pg_namespace.oid = relnamespace"
                " WHERE nspname = 'public' AND relkind IN ('r', 'p') AND NOT relispartition"
                " ORDER BY relname"
            )
        ]
        comptes = {table: _lignes(f'SELECT count(*) FROM "{table}"')[0][0] for table in tables}

        if comptages:
            with open(comptages, "w", encoding="utf-8") as fichier:
                json.dump(comptes, fichier, indent=2, sort_keys=True)
        if comparer:
            with open(comparer, encoding="utf-8") as fichier:
                attendus = json.load(fichier)
            for table in sorted(set(attendus) | set(comptes)):
                if attendus.get(table) != comptes.get(table):
                    trouve, attendu = comptes.get(table), attendus.get(table)
                    fautes.append(f"{table} : {trouve} ligne(s), {attendu} attendue(s)")

        base = connections["worker"].settings_dict["NAME"]
        self.stdout.write(f"Base {base} : {len(tables)} tables, {sum(comptes.values())} lignes.")
        for faute in fautes:
            self.stdout.write(f"  FAUTE {faute}")
        if fautes:
            sys.exit(1)
        self.stdout.write("  Tous les contrôles passent.")
