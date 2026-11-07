"""Conformité : fenêtre légale d'envoi et jours fériés (lot 6). Les
consentements suivent avec T-75.

Le journal d'audit est passé dans `core` (voir apps/core/models.py).
"""

from django.db import models

from apps.core.models import Identifiable


class Fenetre(Identifiable):
    """Plage horaire où un message marketing peut partir, un jour de la semaine
    donné, en heure locale du pays. Un jour sans ligne est fermé. Tenue en
    base : elle se modifie sans redéploiement."""

    class Jour(models.IntegerChoices):
        LUNDI = 1, "Lundi"
        MARDI = 2, "Mardi"
        MERCREDI = 3, "Mercredi"
        JEUDI = 4, "Jeudi"
        VENDREDI = 5, "Vendredi"
        SAMEDI = 6, "Samedi"
        DIMANCHE = 7, "Dimanche"

    pays = models.CharField(max_length=2, default="BF")
    jour = models.PositiveSmallIntegerField(choices=Jour)
    ouverture = models.TimeField()
    fermeture = models.TimeField()

    class Meta:
        db_table = "conformite_fenetre"
        verbose_name = "fenêtre légale"
        verbose_name_plural = "fenêtre légale"
        constraints = [
            models.UniqueConstraint(fields=["pays", "jour"], name="fenetre_un_jour_par_pays"),
            models.CheckConstraint(
                condition=models.Q(ouverture__lt=models.F("fermeture")),
                name="fenetre_ouverture_avant_fermeture",
            ),
        ]

    def __str__(self):
        heures = f"{self.ouverture:%H:%M}-{self.fermeture:%H:%M}"
        return f"{self.pays} {self.get_jour_display()} {heures}"


class JourFerie(Identifiable):
    """Jour où aucun message marketing ne part, dans un pays. Les fêtes mobiles
    se saisissent année par année : rien ne les calcule."""

    pays = models.CharField(max_length=2, default="BF")
    date = models.DateField()
    libelle = models.CharField(max_length=100)

    class Meta:
        db_table = "conformite_jour_ferie"
        verbose_name = "jour férié"
        verbose_name_plural = "jours fériés"
        constraints = [
            models.UniqueConstraint(fields=["pays", "date"], name="jour_ferie_unique_par_pays")
        ]
        ordering = ["date"]

    def __str__(self):
        return f"{self.pays} {self.date:%d/%m/%Y} {self.libelle}"


class MotProscrit(Identifiable):
    """Dictionnaire anti-spam (T-99) : une expression proscrite dans le texte
    des campagnes. Un avertissement prévient le client à la composition ; un
    blocage refuse la campagne et nomme l'expression. `forme` est l'expression
    normalisée (`conformite.antispam.normaliser`), sur laquelle porte la
    comparaison. Commune à tous les clients, lue par chacun, écrite par le
    back-office ; elle se modifie sans redéploiement."""

    class Gravite(models.TextChoices):
        AVERTISSEMENT = "AVERTISSEMENT", "Avertissement"
        BLOCAGE = "BLOCAGE", "Blocage"

    expression = models.CharField(max_length=100)
    forme = models.CharField(max_length=100, unique=True, editable=False)
    gravite = models.CharField(max_length=15, choices=Gravite, default=Gravite.BLOCAGE)
    motif = models.CharField(
        max_length=255, blank=True, help_text="Montré au client avec l'expression en cause."
    )
    actif = models.BooleanField(default=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conformite_mot_proscrit"
        verbose_name = "mot proscrit"
        verbose_name_plural = "mots proscrits"

    def __str__(self):
        return self.expression
