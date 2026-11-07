"""Administration du back-office : écrans des agents EL-IMMEKA.

Servie dans l'espace du back-office seulement (voir config/urls.py).
"""

from datetime import datetime, time

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.comptes import second_facteur
from apps.comptes.models import Client, Dossier, Piece, Utilisateur
from apps.comptes.services import ClotureRefusee
from apps.conformite import services as conformite
from apps.conformite.models import Fenetre, JourFerie
from apps.core.models import Audit
from apps.core.numeros import NumeroInvalide, normaliser
from apps.credits import services as credits
from apps.expediteurs import services as expediteurs
from apps.expediteurs.models import Expediteur, ExpediteurReseau, Reserve
from apps.fichiers.models import Fichier
from apps.fichiers.services import lien
from apps.paiements import services as paiements
from apps.paiements.models import Facture, Reglage, Transaction

from . import selectors
from .forms import (
    AvoirForm,
    ClotureForm,
    EnregistrementClientForm,
    PaiementManuelForm,
    RemboursementForm,
    RolesForm,
)
from .models import DossierPreuve
from .roles import exiger
from .services import (
    CONTROLES_DE_REVUE,
    AttributionRefusee,
    DecisionRefusee,
    EnregistrementRefuse,
    PieceDeposee,
    approuver_dossier,
    attribuer_roles,
    cloturer_compte,
    enregistrer_client,
    rejeter_dossier,
)


def adresse_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR")


def _autorise(autorisation: bool) -> None:
    """Les vues réécrites ne passent plus par le contrôle standard de
    l'administration : chacune vérifie elle-même le droit du rôle (T-95)."""
    if not autorisation:
        raise PermissionDenied


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("raison_sociale", "forme", "rccm", "ifu", "palier", "etat", "cree_le")
    list_filter = ("forme", "palier", "etat")
    search_fields = ("raison_sociale", "rccm", "ifu")

    def get_urls(self):
        propres = [
            path(
                "<uuid:pk>/cloturer/",
                self.admin_site.admin_view(self.cloturer),
                name="comptes_client_cloturer",
            ),
            path(
                "<uuid:pk>/rembourser/",
                self.admin_site.admin_view(self.rembourser),
                name="comptes_client_rembourser",
            ),
        ]
        return propres + super().get_urls()

    def _formulaire(self, request, titre, introduction, bouton, formulaire):
        contexte = {
            **self.admin_site.each_context(request),
            "title": titre,
            "introduction": introduction,
            "bouton": bouton,
            "formulaire": formulaire,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/formulaire.html", contexte)

    def cloturer(self, request, pk):
        """Clôture sur demande écrite du client (T-89)."""
        exiger(request, "backoffice.cloturer_client")
        client = get_object_or_404(Client, pk=pk)
        formulaire = ClotureForm(request.POST or None)
        if request.method == "POST" and formulaire.is_valid():
            try:
                annulees = cloturer_compte(
                    client,
                    agent=request.user,
                    motif=formulaire.cleaned_data["motif"],
                    adresse_ip=adresse_ip(request),
                )
            except ClotureRefusee as refus:
                formulaire.add_error(None, str(refus))
            else:
                messages.success(
                    request,
                    f"{client} clos ; {len(annulees)} campagne(s) annulée(s). Les contacts "
                    "seront effacés dans trente jours.",
                )
                return redirect("admin:comptes_client_change", client.id)
        return self._formulaire(
            request,
            f"Clore le compte {client}",
            "Plus aucun envoi ni aucune connexion ; les campagnes en cours sont annulées. "
            "Factures, justificatifs et registre restent. Les contacts sont effacés trente "
            "jours après la clôture.",
            "Clore le compte",
            formulaire,
        )

    def rembourser(self, request, pk):
        """Remboursement de la part payée et non consommée, compte clos (T-89)."""
        exiger(request, "backoffice.rembourser_client")
        client = get_object_or_404(Client, pk=pk)
        part = credits.part_remboursable(client.id)
        formulaire = RemboursementForm(request.POST or None)
        if request.method == "POST" and formulaire.is_valid():
            try:
                resultat = paiements.rembourser_cloture(
                    client,
                    agent=request.user,
                    reference=formulaire.cleaned_data["reference"],
                    adresse_ip=adresse_ip(request),
                )
            except (paiements.RemboursementRefuse, paiements.FacturationImpossible) as refus:
                formulaire.add_error(None, str(refus))
            else:
                messages.success(
                    request,
                    f"{resultat.montant_ttc} FCFA TTC remboursés ({resultat.credits_rembourses} "
                    f"FCFA de crédits) ; avoirs {', '.join(resultat.avoirs) or 'aucun'} ; "
                    f"{resultat.credits_perdus} FCFA de crédits offerts perdus.",
                )
                return redirect("admin:comptes_client_change", client.id)
        return self._formulaire(
            request,
            f"Rembourser le solde de {client}",
            f"Crédits payés {part.payes} FCFA, offerts {part.offerts}, consommés "
            f"{part.consomme} ; solde {part.solde}. Remboursable : {part.remboursable} FCFA HT "
            f"de crédits ; perdus : {part.perdu} FCFA de crédits offerts. Le virement au client "
            "se fait hors de la plateforme : indiquez sa référence.",
            "Enregistrer le remboursement",
            formulaire,
        )

    def add_view(self, request, form_url="", extra_context=None):
        """Enregistrer un client signé : identité, représentant, pièces."""
        _autorise(self.has_add_permission(request))
        formulaire = EnregistrementClientForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and formulaire.is_valid():
            donnees = formulaire.cleaned_data
            pieces = {
                cle.removeprefix("piece_"): PieceDeposee(fichier.read(), fichier.name)
                for cle, fichier in donnees.items()
                if cle.startswith("piece_") and fichier
            }
            try:
                dossier = enregistrer_client(
                    identite=donnees,
                    pieces=pieces,
                    agent=request.user,
                    adresse_ip=adresse_ip(request),
                )
            except EnregistrementRefuse as refus:
                for champ, erreurs in refus.erreurs.items():
                    for erreur in erreurs:
                        formulaire.add_error(champ if champ in formulaire.fields else None, erreur)
            else:
                messages.success(
                    request, f"{dossier.client} enregistré ; dossier déposé pour revue."
                )
                return redirect("admin:comptes_dossier_change", dossier.id)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Enregistrer un client",
            "formulaire": formulaire,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/enregistrer_client.html", contexte)


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("client", "etat", "depose_le", "decide_le", "decide_par")
    list_filter = ("etat",)
    # La file de revue se prend par ancienneté : le plus vieux dossier d'abord.
    ordering = ("depose_le",)

    def has_add_permission(self, request):
        return False  # un dossier naît de l'enregistrement d'un client

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Fiche de revue : pièces par liens signés, cinq contrôles, décision."""
        _autorise(self.has_view_or_change_permission(request))
        dossier = self.get_object(request, object_id)
        if dossier is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        if request.method == "POST":
            _autorise(self.has_change_permission(request, dossier))
            try:
                if "approuver" in request.POST:
                    approuver_dossier(
                        dossier.id,
                        agent=request.user,
                        controles=request.POST.getlist("controles"),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dossier de {dossier.client} approuvé.")
                else:
                    rejeter_dossier(
                        dossier.id,
                        agent=request.user,
                        motif=request.POST.get("motif", ""),
                        detail=request.POST.get("detail", ""),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dossier de {dossier.client} rejeté.")
                return redirect("admin:comptes_dossier_changelist")
            except DecisionRefusee as refus:
                messages.error(request, str(refus))
        pieces = []
        for piece in Piece.objects.filter(dossier=dossier).order_by("type"):
            fichier = Fichier.objects.filter(id=piece.fichier_id).first()
            pieces.append({"piece": piece, "lien": lien(fichier) if fichier else None})
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Revue du dossier de {dossier.client}",
            "dossier": dossier,
            "pieces": pieces,
            "controles": CONTROLES_DE_REVUE,
            "motifs": Dossier.MotifRejet.choices,
            "opts": self.model._meta,
            "decidable": dossier.etat in (Dossier.Etat.DEPOSE, Dossier.Etat.EN_REVUE),
        }
        return TemplateResponse(request, "admin/backoffice/revue_dossier.html", contexte)


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    list_display = ("courriel", "nom", "client", "role", "is_active", "second_facteur_actif")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("courriel", "nom")
    exclude = (
        "password",
        "second_facteur_secret",
        "second_facteur_dernier_pas",
        "groups",
        "user_permissions",
    )

    def get_urls(self):
        propres = [
            path(
                "<uuid:pk>/reinitialiser-second-facteur/",
                self.admin_site.admin_view(self.reinitialiser_second_facteur),
                name="comptes_utilisateur_reinitialiser_second_facteur",
            ),
            path(
                "<uuid:pk>/roles/",
                self.admin_site.admin_view(self.roles),
                name="comptes_utilisateur_roles",
            ),
        ]
        return propres + super().get_urls()

    def roles(self, request, pk):
        """Rôles d'un agent (T-95) : super-administrateur seulement, jamais sur
        soi-même, motif obligatoire, trace au journal d'audit."""
        _autorise(request.user.is_superuser)
        agent = get_object_or_404(Utilisateur, pk=pk, is_staff=True, client__isnull=True)
        actuels = list(agent.groups.values_list("name", flat=True))
        formulaire = RolesForm(request.POST or None, initial={"roles": actuels})
        if request.method == "POST" and formulaire.is_valid():
            try:
                roles = attribuer_roles(
                    agent,
                    formulaire.cleaned_data["roles"],
                    auteur=request.user,
                    motif=formulaire.cleaned_data["motif"],
                    adresse_ip=adresse_ip(request),
                )
            except AttributionRefusee as refus:
                formulaire.add_error(None, str(refus))
            else:
                messages.success(request, f"Rôles de {agent} : {', '.join(roles) or 'aucun'}.")
                return redirect("admin:comptes_utilisateur_change", agent.pk)
        return TemplateResponse(
            request,
            "admin/backoffice/formulaire.html",
            {
                **self.admin_site.each_context(request),
                "title": f"Rôles de {agent}",
                "introduction": "Support : clients, dossiers, expéditeurs, tickets. "
                "Exploitation : liaisons, connecteurs, fenêtres, page d'état, anti-fraude. "
                "Finance : paiements, factures, crédits, remboursements. Le "
                "super-administrateur a tout et ne se donne pas par cet écran.",
                "bouton": "Enregistrer les rôles",
                "formulaire": formulaire,
                "opts": self.model._meta,
            },
        )

    def reinitialiser_second_facteur(self, request, pk):
        """Pour un utilisateur qui a perdu son second facteur : identité vérifiée
        par un canal indépendant, motif obligatoire, trace au journal d'audit."""
        exiger(request, "backoffice.reinitialiser_second_facteur")
        utilisateur = get_object_or_404(Utilisateur, pk=pk)
        if request.method == "POST":
            try:
                second_facteur.reinitialiser(
                    utilisateur,
                    agent=request.user,
                    motif=request.POST.get("motif", ""),
                    identite_verifiee=request.POST.get("identite_verifiee") == "oui",
                    adresse_ip=adresse_ip(request),
                )
            except second_facteur.CodeRefuse as refus:
                messages.error(request, str(refus))
            else:
                messages.success(request, f"Second facteur de {utilisateur} réinitialisé.")
                return redirect("admin:comptes_utilisateur_change", utilisateur.pk)
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Réinitialiser le second facteur de {utilisateur}",
            "utilisateur": utilisateur,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request, "admin/backoffice/reinitialiser_second_facteur.html", contexte
        )


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    """Consultation seule : personne, super-administrateur compris, ne peut
    ajouter, modifier ou supprimer une entrée depuis l'administration."""

    list_display = ("horodatage", "action", "objet_type", "objet_id", "auteur", "client", "motif")
    list_filter = ("action", "objet_type", "horodatage")
    search_fields = ("objet_id", "motif", "auteur__courriel", "client__raison_sociale")
    date_hierarchy = "horodatage"
    readonly_fields = [champ.name for champ in Audit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _date(valeur: str):
    """Une date saisie, à minuit à Ouagadougou ; None si vide ou invalide."""
    jour = parse_date(valeur or "")
    if jour is None:
        return None
    return timezone.make_aware(datetime.combine(jour, time.min))


@admin.register(Expediteur)
class ExpediteurAdmin(admin.ModelAdmin):
    """Fiche d'un identifiant : revue manuelle, dépôt et décision par réseau.
    Chaque décision passe par le service du module et laisse une trace au
    journal d'audit."""

    list_display = ("libelle", "client", "etat", "revue_manuelle", "cree_le")
    list_filter = ("etat", "revue_manuelle")
    search_fields = ("libelle", "client__raison_sociale")
    ordering = ("cree_le",)

    def has_add_permission(self, request):
        return False  # un identifiant se déclare depuis l'espace du client

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        _autorise(self.has_view_or_change_permission(request))
        expediteur = self.get_object(request, object_id)
        if expediteur is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        if request.method == "POST":
            _autorise(self.has_change_permission(request, expediteur))
            operateur = request.POST.get("operateur", "")
            try:
                if "lever_revue" in request.POST:
                    expediteurs.lever_revue(
                        expediteur.id,
                        agent=request.user,
                        motif=request.POST.get("motif", ""),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, "Revue levée : les dossiers peuvent être déposés.")
                elif "deposer" in request.POST:
                    expediteurs.deposer_dossier(
                        expediteur.id,
                        operateur,
                        agent=request.user,
                        reference=request.POST.get("reference", ""),
                        depose_le=_date(request.POST.get("date")),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dépôt chez {operateur} enregistré.")
                elif "decider" in request.POST:
                    expediteurs.decider(
                        expediteur.id,
                        operateur,
                        agent=request.user,
                        approuve=request.POST.get("decision") == "approuve",
                        decide_le=_date(request.POST.get("date")),
                        reference=request.POST.get("reference", ""),
                        motif=request.POST.get("motif", ""),
                        expire_le=_date(request.POST.get("expire_le")),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Décision de {operateur} enregistrée.")
                return redirect("admin:expediteurs_expediteur_change", expediteur.id)
            except expediteurs.DecisionRefusee as refus:
                messages.error(request, str(refus))
        mandat = Fichier.objects.filter(id=expediteur.justificatif_id).first()
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Identifiant {expediteur.libelle}",
            "expediteur": expediteur,
            "reseaux": ExpediteurReseau.objects.filter(expediteur=expediteur).order_by("operateur"),
            "lien_mandat": lien(mandat) if mandat else None,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/expediteur.html", contexte)


@admin.register(Reserve)
class ReserveAdmin(admin.ModelAdmin):
    list_display = ("libelle", "motif", "actif")
    list_filter = ("actif",)
    search_fields = ("libelle",)


@admin.register(JourFerie)
class JourFerieAdmin(admin.ModelAdmin):
    """Jours fériés, saisis par un agent année par année : les fêtes mobiles ne
    se calculent pas. Chaque saisie est tracée au journal d'audit."""

    list_display = ("date", "libelle", "pays")
    list_filter = ("pays",)
    date_hierarchy = "date"
    search_fields = ("libelle",)

    def save_model(self, request, obj, form, change):
        conformite.enregistrer_jour_ferie(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_model(self, request, obj):
        conformite.supprimer_jour_ferie(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_queryset(self, request, queryset):
        for jour in queryset:
            self.delete_model(request, jour)


@admin.register(Fenetre)
class FenetreAdmin(admin.ModelAdmin):
    """Fenêtre légale des envois marketing, jour par jour, en heure locale. Un
    jour sans ligne est fermé."""

    list_display = ("jour", "ouverture", "fermeture", "pays")
    list_filter = ("pays",)
    ordering = ("pays", "jour")

    def save_model(self, request, obj, form, change):
        conformite.regler_fenetre(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_model(self, request, obj):
        conformite.fermer_jour(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_queryset(self, request, queryset):
        for fenetre in queryset:
            self.delete_model(request, fenetre)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Paiements, en lecture seule : un paiement ne se modifie pas à la main.
    L'ajout est la saisie d'un paiement manuel (T-87)."""

    list_display = (
        "initie_le",
        "client",
        "fournisseur",
        "reference_transaction",
        "credits",
        "montant",
        "etat",
    )
    list_filter = ("fournisseur", "etat")
    search_fields = ("reference_transaction", "client__raison_sociale")
    date_hierarchy = "initie_le"

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def add_view(self, request, form_url="", extra_context=None):
        _autorise(self.has_add_permission(request))
        formulaire = PaiementManuelForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and formulaire.is_valid():
            donnees = formulaire.cleaned_data
            fichier = donnees.get("justificatif")
            try:
                transaction_ = paiements.saisir_manuel(
                    donnees["client"],
                    fournisseur=donnees["fournisseur"],
                    credits_ht=donnees["credits"],
                    reference=donnees["reference"],
                    motif=donnees["motif"],
                    justificatif=fichier.read() if fichier else None,
                    nom_justificatif=fichier.name if fichier else "",
                    agent=request.user,
                    adresse_ip=adresse_ip(request),
                )
            except paiements.SaisieRefusee as refus:
                formulaire.add_error(None, str(refus))
            else:
                messages.success(
                    request,
                    f"{transaction_.credits} FCFA de crédits portés au solde de "
                    f"{transaction_.client} ({transaction_.montant} FCFA payés).",
                )
                return redirect("admin:paiements_transaction_changelist")
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Saisir un paiement manuel",
            "formulaire": formulaire,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/paiement_manuel.html", contexte)


@admin.register(Facture)
class FactureAdmin(admin.ModelAdmin):
    """Factures et avoirs, en lecture seule : une facture émise ne se modifie
    pas. Une correction passe par un avoir, saisi depuis la facture."""

    list_display = ("numero", "type", "emise_le", "client", "montant_ht", "tva", "montant_ttc")
    list_filter = ("type", "annee")
    search_fields = ("numero", "client__raison_sociale")
    date_hierarchy = "emise_le"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        propres = [
            path(
                "<uuid:pk>/avoir/",
                self.admin_site.admin_view(self.avoir),
                name="paiements_facture_avoir",
            )
        ]
        return propres + super().get_urls()

    def avoir(self, request, pk):
        exiger(request, "backoffice.emettre_avoir")
        facture = get_object_or_404(Facture, pk=pk, type=Facture.Type.FACTURE)
        formulaire = AvoirForm(request.POST or None)
        if request.method == "POST" and formulaire.is_valid():
            try:
                avoir = paiements.emettre_avoir(
                    facture.id,
                    montant_ht=formulaire.cleaned_data["montant_ht"],
                    motif=formulaire.cleaned_data["motif"],
                    agent=request.user,
                    adresse_ip=adresse_ip(request),
                )
            except paiements.FacturationImpossible as refus:
                formulaire.add_error(None, str(refus))
            else:
                messages.success(request, f"Avoir {avoir.numero} émis sur {facture.numero}.")
                return redirect("admin:paiements_facture_changelist")
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Avoir sur la facture {facture.numero}",
            "introduction": (
                f"Facture de {facture.montant_ht} FCFA HT pour {facture.client}. "
                "L'avoir corrige tout ou partie de la facture, qui, elle, ne change pas."
            ),
            "bouton": "Émettre l'avoir",
            "formulaire": formulaire,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/formulaire.html", contexte)


@admin.register(Reglage)
class ReglageAdmin(admin.ModelAdmin):
    """Réglages du rechargement : TVA, bornes et délai d'attente. Une seule ligne,
    chaque changement tracé au journal d'audit."""

    list_display = ("__str__", "tva_active", "taux_tva", "credits_min", "credits_max")

    def has_add_permission(self, request):
        return not Reglage.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        paiements.regler(obj, agent=request.user, adresse_ip=adresse_ip(request))


@admin.register(DossierPreuve)
class DossierPreuveAdmin(admin.ModelAdmin):
    """Dossier de preuve d'un numéro (T-75) : ce que demandera l'ARCEP ou un
    abonné mécontent. Premier clic, l'entrée du menu ; deuxième, la recherche du
    numéro, qui montre chaque client qui le connaît ou lui a écrit ; troisième,
    les messages d'un client et la preuve de la désinscription."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        propres = [
            path(
                "client/",
                self.admin_site.admin_view(self.client_view),
                name="backoffice_dossierpreuve_client",
            )
        ]
        return propres + super().get_urls()

    def _numero(self, request):
        saisi = request.GET.get("numero", "").strip()
        if not saisi:
            return saisi, None, ""
        try:
            return saisi, normaliser(saisi), ""
        except NumeroInvalide as refus:
            return saisi, None, f"Numéro invalide : {refus.cause}."

    def changelist_view(self, request, extra_context=None):
        _autorise(self.has_view_permission(request))
        saisi, numero, erreur = self._numero(request)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Dossier de preuve d'un numéro",
            "opts": self.model._meta,
            "saisi": saisi,
            "erreur": erreur,
            "dossier": selectors.dossier(numero) if numero else None,
        }
        return TemplateResponse(request, "admin/backoffice/dossier_preuve.html", contexte)

    def client_view(self, request):
        _autorise(self.has_view_permission(request))
        _, numero, erreur = self._numero(request)
        client = Client.objects.filter(id=request.GET.get("client") or None).first()
        if not numero or client is None:
            messages.error(request, erreur or "Numéro ou client introuvable.")
            return redirect("admin:backoffice_dossierpreuve_changelist")
        dossier = selectors.dossier(numero)
        ligne = next((ligne for ligne in dossier.clients if ligne.client == client), None)
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"{numero} chez {client.raison_sociale}",
            "opts": self.model._meta,
            "numero": numero,
            "client": client,
            "ligne": ligne,
            "dossier": dossier,
            "messages_recus": selectors.messages_recus(numero, client.id),
            "plafond": selectors.MESSAGES_AFFICHES,
        }
        return TemplateResponse(request, "admin/backoffice/dossier_preuve_client.html", contexte)
