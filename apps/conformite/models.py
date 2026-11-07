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


class RegleFraude(Identifiable):
    """Une règle de détection de trafic artificiel (T-100) et ses seuils, tenus
    en base. Au seuil d'alerte, un humain est prévenu ; au seuil critique,
    l'envoi est en plus bloqué. Sens des seuils selon la règle :

    - CONTIGUS : longueur de la plus longue série de numéros consécutifs d'une
      campagne ;
    - CODES_PLAGE : codes envoyés hors campagne vers une même plage, sur la
      fenêtre, quand le client n'y avait rien envoyé depuis trente jours ;
    - LIVRAISON_PLAGE : taux de livraison sur une plage, en pour cent, sur la
      fenêtre et au moins `echantillon_min` accusés ; l'alerte part sous le
      seuil, et non au-dessus."""

    class Code(models.TextChoices):
        CONTIGUS = "CONTIGUS", "Numéros contigus"
        CODES_PLAGE = "CODES_PLAGE", "Codes vers une plage inhabituelle"
        LIVRAISON_PLAGE = "LIVRAISON_PLAGE", "Livraison basse sur une plage"

    code = models.CharField(max_length=20, choices=Code, unique=True)
    seuil_alerte = models.PositiveIntegerField()
    seuil_critique = models.PositiveIntegerField()
    fenetre_minutes = models.PositiveIntegerField(default=60)
    echantillon_min = models.PositiveIntegerField(default=0)
    actif = models.BooleanField(default=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "conformite_regle_fraude"
        verbose_name = "règle anti-fraude"
        verbose_name_plural = "règles anti-fraude"

    def __str__(self):
        return self.get_code_display()


class AlerteFraude(Identifiable):
    """Une alerte de trafic artificiel, ouverte jusqu'à son traitement par un
    agent. Une seule alerte ouverte par client, règle, plage et campagne : une
    détection répétée la met à jour. Critique, elle bloque les envois du client
    vers la plage tant qu'elle n'est pas traitée."""

    class Gravite(models.TextChoices):
        ALERTE = "ALERTE", "Alerte"
        CRITIQUE = "CRITIQUE", "Critique"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    regle = models.CharField(max_length=20, choices=RegleFraude.Code)
    gravite = models.CharField(max_length=10, choices=Gravite)
    plage = models.CharField(max_length=20, blank=True)
    campagne_id = models.UUIDField(null=True, blank=True)
    valeur = models.IntegerField()
    details = models.JSONField(default=dict, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)
    traitee_le = models.DateTimeField(null=True, blank=True)
    traitee_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    traitement = models.TextField(blank=True)

    class Meta:
        db_table = "conformite_alerte_fraude"
        verbose_name = "alerte anti-fraude"
        verbose_name_plural = "alertes anti-fraude"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "regle", "plage", "campagne_id"],
                condition=models.Q(traitee_le__isnull=True),
                nulls_distinct=False,
                name="alerte_fraude_une_ouverte",
            )
        ]
        indexes = [
            models.Index(
                fields=["client", "plage"],
                condition=models.Q(traitee_le__isnull=True),
                name="alerte_fraude_ouverte",
            )
        ]

    def __str__(self):
        return f"{self.get_regle_display()} {self.plage or self.campagne_id}"
