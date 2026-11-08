"""Sème la règle PLAFOND_COMPTE (T-101).

`conformite_regle_fraude` est cloisonnée depuis 0036 : lecture par tous, aucune
écriture, et le cloisonnement est forcé, même pour le propriétaire. Le semis
lève donc le forçage le temps de l'insertion, puis le remet, dans cette
migration de `core` où vivent les politiques.

Seuils à confirmer avec l'utilisateur, en pour cent du plafond du compte :
l'alerte part au premier envoi refusé, au-delà de 100 % ; la suspension
automatique quand les envois tentés atteignent 200 % sur la même heure ou le
même jour.
"""

from django.db import migrations

TABLE = "conformite_regle_fraude"

REGLE = {
    "code": "PLAFOND_COMPTE",
    "seuil_alerte": 100,
    "seuil_critique": 200,
    "fenetre_minutes": 0,
    "echantillon_min": 0,
}


def _sans_forcage(sql: str) -> list[str]:
    return [
        f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY",
        sql,
        f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    ]


SEMER = _sans_forcage(
    f"INSERT INTO {TABLE} "
    "(id, code, seuil_alerte, seuil_critique, fenetre_minutes, echantillon_min, actif, modifie_le) "
    f"VALUES (gen_random_uuid(), '{REGLE['code']}', {REGLE['seuil_alerte']}, "
    f"{REGLE['seuil_critique']}, {REGLE['fenetre_minutes']}, {REGLE['echantillon_min']}, "
    "true, now()) ON CONFLICT (code) DO NOTHING"
)
RETIRER = _sans_forcage(f"DELETE FROM {TABLE} WHERE code = '{REGLE['code']}'")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0036_cloisonnement_fraude"),
        ("conformite", "0006_plafond_compte"),
    ]

    operations = [migrations.RunSQL(SEMER, RETIRER)]
