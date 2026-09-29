"""Journal d'audit des actions touchant à l'argent, aux accès ou à la conformité.

Voir docs/modele-de-donnees.md, section conformite, et la fiche F-43. En ajout
seul : le modèle refuse modification et suppression, et un déclencheur
PostgreSQL le refuse aussi, pour tous les rôles, back-office compris. Un
journal que son propre sujet peut modifier ne prouve rien.
"""

from django.db import models

from apps.core.models import Identifiable


class EcritureSeuleError(Exception):
    pass


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise EcritureSeuleError("conformite_audit est en écriture seule.")

    def delete(self):
        raise EcritureSeuleError("conformite_audit est en écriture seule.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise EcritureSeuleError("conformite_audit est en écriture seule.")


class Audit(Identifiable):
    auteur = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Le compte client concerné, s'il y en a un : sert à remettre au client
    # l'extrait qui le regarde, sans rien lui montrer des autres.
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=100)
    objet_type = models.CharField(max_length=100)
    objet_id = models.CharField(max_length=100)
    valeur_avant = models.JSONField(null=True, blank=True)
    valeur_apres = models.JSONField(null=True, blank=True)
    motif = models.TextField(blank=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    horodatage = models.DateTimeField(auto_now_add=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        db_table = "conformite_audit"
        ordering = ["-horodatage"]
        indexes = [
            models.Index(fields=["objet_type", "objet_id"], name="audit_objet"),
            models.Index(fields=["client", "-horodatage"], name="audit_client_date"),
            models.Index(fields=["action", "-horodatage"], name="audit_action_date"),
        ]

    def __str__(self):
        return f"{self.horodatage:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise EcritureSeuleError("conformite_audit est en écriture seule.")
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise EcritureSeuleError("conformite_audit est en écriture seule.")
