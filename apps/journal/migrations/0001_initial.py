"""Tables du journal des messages.

`journal_message` et `journal_evenement` sont partitionnées par mois. Django ne
sait pas créer une table partitionnée : l'état des modèles est déclaré
normalement, la base reçoit le SQL écrit ici (SeparateDatabaseAndState).

Les partitions ne sont joignables que par leur table mère : à leur création,
tous les droits accordés par défaut sur elles sont retirés, sauf au
propriétaire. La politique de cloisonnement, posée sur la table mère par une
migration dédiée de `core`, s'applique donc à toute lecture.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models

ETATS = [
    ("RECU", "Reçu"),
    ("EN_ATTENTE", "En attente"),
    ("EN_FILE", "En file"),
    ("SOUMIS", "Soumis"),
    ("LIVRE", "Livré"),
    ("NON_LIVRE", "Non livré"),
    ("EXPIRE", "Expiré"),
    ("SANS_ACCUSE", "Sans accusé"),
    ("REJETE", "Rejeté"),
    ("ANNULE", "Annulé"),
]

CREER_MESSAGE = """
CREATE TABLE journal_message (
    id uuid NOT NULL,
    client_id uuid NOT NULL
        REFERENCES comptes_client (id) DEFERRABLE INITIALLY DEFERRED,
    campagne_id uuid NULL,
    contact_id uuid NULL,
    numero_normalise varchar(20) NOT NULL,
    operateur varchar(20) NOT NULL,
    expediteur_libelle varchar(11) NOT NULL,
    texte text NOT NULL,
    encodage varchar(10) NOT NULL,
    segments smallint NOT NULL,
    cout_unitaire integer NOT NULL,
    cout_total integer NOT NULL,
    etat varchar(20) NOT NULL,
    motif_echec varchar(100) NOT NULL,
    connecteur_id uuid NULL,
    reference_smsc varchar(100) NOT NULL,
    cle_idempotence varchar(100) NOT NULL,
    date_reception timestamptz NOT NULL,
    soumission_debutee_le timestamptz NULL,
    date_soumission timestamptz NULL,
    date_accuse timestamptz NULL,
    modifie_le timestamptz NOT NULL,
    PRIMARY KEY (id, date_reception)
) PARTITION BY RANGE (date_reception)
"""

CREER_EVENEMENT = """
CREATE TABLE journal_evenement (
    id uuid NOT NULL,
    client_id uuid NOT NULL
        REFERENCES comptes_client (id) DEFERRABLE INITIALLY DEFERRED,
    message_id uuid NOT NULL,
    etat varchar(20) NOT NULL,
    horodatage timestamptz NOT NULL,
    source varchar(10) NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY (id, horodatage)
) PARTITION BY RANGE (horodatage)
"""

# Crée la partition du mois qui contient `mois`, si elle n'existe pas encore,
# et lui retire tous les droits sauf ceux du propriétaire. Les bornes sont
# exprimées en UTC, quel que soit le fuseau de la session.
CREER_FONCTION = """
CREATE FUNCTION journal_creer_partition(parent regclass, mois date) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    debut date := date_trunc('month', mois)::date;
    fin date := (date_trunc('month', mois) + interval '1 month')::date;
    nom text := parent::text || '_' || to_char(debut, 'YYYY_MM');
    beneficiaire text;
BEGIN
    IF parent NOT IN ('journal_message'::regclass, 'journal_evenement'::regclass) THEN
        RAISE EXCEPTION 'table non partitionnée par mois : %', parent;
    END IF;
    IF to_regclass(nom) IS NOT NULL THEN
        RETURN nom;
    END IF;
    EXECUTE format(
        'CREATE TABLE %I PARTITION OF %s FOR VALUES FROM (%L) TO (%L)',
        nom, parent, debut::text || ' 00:00:00+00', fin::text || ' 00:00:00+00'
    );
    FOR beneficiaire IN
        SELECT DISTINCT droit.grantee::regrole::text
        FROM pg_class classe, aclexplode(classe.relacl) droit
        WHERE classe.oid = nom::regclass
          AND droit.grantee <> classe.relowner
          AND droit.grantee <> 0
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE %I FROM %s', nom, beneficiaire);
    END LOOP;
    EXECUTE format('REVOKE ALL ON TABLE %I FROM PUBLIC', nom);
    RETURN nom;
END
$$
"""


def creer_partitions_initiales(apps, schema_editor):
    """Le mois courant et les deux suivants, pour chaque table partitionnée."""
    with schema_editor.connection.cursor() as curseur:
        for table in ("journal_message", "journal_evenement"):
            for decalage in range(3):
                curseur.execute(
                    "SELECT journal_creer_partition(%s::regclass,"
                    " (date_trunc('month', now()) + make_interval(months => %s))::date)",
                    [table, decalage],
                )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("comptes", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Destinataire",
            fields=[
                (
                    "pk",
                    models.CompositePrimaryKey(
                        "campagne_id",
                        "contact_id",
                        blank=True,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("campagne_id", models.UUIDField()),
                ("contact_id", models.UUIDField()),
                ("message_id", models.UUIDField()),
                ("message_date_reception", models.DateTimeField()),
                ("cree_le", models.DateTimeField(auto_now_add=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="comptes.client",
                    ),
                ),
            ],
            options={"db_table": "journal_destinataire"},
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(CREER_EVENEMENT, reverse_sql="DROP TABLE journal_evenement"),
                migrations.RunSQL(CREER_MESSAGE, reverse_sql="DROP TABLE journal_message"),
            ],
            state_operations=[
                migrations.CreateModel(
                    name="Evenement",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "id",
                                "horodatage",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("id", models.UUIDField(default=uuid.uuid7, editable=False)),
                        ("message_id", models.UUIDField()),
                        ("etat", models.CharField(choices=ETATS, max_length=20)),
                        ("horodatage", models.DateTimeField()),
                        (
                            "source",
                            models.CharField(
                                choices=[
                                    ("API", "API"),
                                    ("WORKER", "Worker"),
                                    ("ACCUSE", "Accusé"),
                                    ("ADMIN", "Administration"),
                                ],
                                max_length=10,
                            ),
                        ),
                        ("details", models.JSONField(blank=True, default=dict)),
                        (
                            "client",
                            models.ForeignKey(
                                db_index=False,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="comptes.client",
                            ),
                        ),
                    ],
                    options={"db_table": "journal_evenement"},
                ),
                migrations.CreateModel(
                    name="Message",
                    fields=[
                        (
                            "pk",
                            models.CompositePrimaryKey(
                                "id",
                                "date_reception",
                                blank=True,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("id", models.UUIDField(default=uuid.uuid7, editable=False)),
                        ("campagne_id", models.UUIDField(blank=True, null=True)),
                        ("contact_id", models.UUIDField(blank=True, null=True)),
                        ("numero_normalise", models.CharField(max_length=20)),
                        ("operateur", models.CharField(max_length=20)),
                        ("expediteur_libelle", models.CharField(max_length=11)),
                        ("texte", models.TextField()),
                        (
                            "encodage",
                            models.CharField(
                                choices=[("GSM7", "GSM 7 bits"), ("UCS2", "UCS-2")],
                                max_length=10,
                            ),
                        ),
                        ("segments", models.SmallIntegerField()),
                        ("cout_unitaire", models.IntegerField()),
                        ("cout_total", models.IntegerField()),
                        (
                            "etat",
                            models.CharField(choices=ETATS, default="RECU", max_length=20),
                        ),
                        ("motif_echec", models.CharField(blank=True, max_length=100)),
                        ("connecteur_id", models.UUIDField(blank=True, null=True)),
                        ("reference_smsc", models.CharField(blank=True, max_length=100)),
                        ("cle_idempotence", models.CharField(blank=True, max_length=100)),
                        ("date_reception", models.DateTimeField()),
                        ("soumission_debutee_le", models.DateTimeField(blank=True, null=True)),
                        ("date_soumission", models.DateTimeField(blank=True, null=True)),
                        ("date_accuse", models.DateTimeField(blank=True, null=True)),
                        ("modifie_le", models.DateTimeField(auto_now=True)),
                        (
                            "client",
                            models.ForeignKey(
                                db_index=False,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="comptes.client",
                            ),
                        ),
                    ],
                    options={"db_table": "journal_message"},
                ),
            ],
        ),
        # Index posés sur la table mère : PostgreSQL les propage à chaque partition,
        # y compris à celles créées plus tard.
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["client", "-date_reception"], name="message_client_date"),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["client", "numero_normalise", "-date_reception"],
                name="message_client_numero_date",
            ),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["campagne_id", "etat"], name="message_campagne_etat"),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(
                fields=["numero_normalise", "-date_reception"], name="message_numero_date"
            ),
        ),
        migrations.AddIndex(
            model_name="evenement",
            index=models.Index(fields=["message_id", "horodatage"], name="evenement_message"),
        ),
        migrations.AddIndex(
            model_name="evenement",
            index=models.Index(fields=["client", "-horodatage"], name="evenement_client_date"),
        ),
        migrations.RunSQL(CREER_FONCTION, reverse_sql="DROP FUNCTION journal_creer_partition"),
        migrations.RunPython(creer_partitions_initiales, migrations.RunPython.noop),
    ]
