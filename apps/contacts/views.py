"""Écrans de l'import de contacts."""

from django.contrib import messages
from django.db import transaction
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.comptes.permissions import permission_requise
from apps.core.cloisonnement import poser_client
from apps.fichiers.services import FichierRefuse

from . import imports
from .champs import noms_declares
from .lecture import FichierIllisible
from .models import ChampPersonnalise, Import

CHOIX_FIXES = (
    ("", "Ignorer"),
    ("numero", "Numéro de téléphone"),
    ("nom", "Nom"),
    ("prenom", "Prénom"),
    ("ville", "Ville"),
)


def _choix_colonne(client):
    """Les champs fixes, puis les champs personnalisés déclarés par le client."""
    declares = ChampPersonnalise.objects.filter(client=client).order_by("libelle")
    return CHOIX_FIXES + tuple((f"champ:{c.nom}", f"Champ : {c.libelle}") for c in declares)


def _import(request, pk) -> Import:
    # contacts_import est cloisonnée : un import d'un autre compte est invisible.
    return get_object_or_404(Import, pk=pk)


@permission_requise("contacts.importer")
def deposer(request):
    erreur = ""
    if request.method == "POST":
        fichier = request.FILES.get("fichier")
        if fichier is None:
            erreur = "Choisissez un fichier."
        else:
            try:
                import_ = imports.deposer(
                    client=request.user.client, flux=fichier.file, nom_origine=fichier.name
                )
            except FichierRefuse as refus:
                erreur = str(refus)
            else:
                return redirect("import-mappage", import_.pk)
    return render(request, "contacts/deposer.html", {"erreur": erreur})


def _mappage_saisi(request, lignes: list[list[str]], largeur: int) -> dict:
    """Le mappage tel que l'utilisateur l'a corrigé, colonne par colonne."""
    entetes = request.POST.get("entetes") == "oui"
    entete = [imports.cle_entete(v) for v in lignes[0]] if entetes else []
    colonnes, personnalises = {}, {}
    for rang in range(largeur):
        choix = request.POST.get(f"colonne_{rang}", "")
        if choix.startswith("champ:"):
            personnalises[choix.removeprefix("champ:")] = rang
        elif choix:
            colonnes[choix] = rang
    return {
        "entetes": entetes,
        "entete": entete,
        "largeur": largeur,
        "colonnes": colonnes,
        "personnalises": personnalises,
    }


def _mappage_propose(client, lignes) -> dict:
    return imports.mappage_precedent(client, lignes) or imports.proposer_mappage(
        lignes, champs=noms_declares(client)
    )


@permission_requise("contacts.importer")
def mappage(request, pk):
    import_ = _import(request, pk)
    if import_.etat != Import.Etat.DEPOSE:
        return redirect("import-suivi", import_.pk)
    try:
        lignes = imports.apercu(import_)
        propose = _mappage_propose(request.user.client, lignes)
    except (FichierIllisible, imports.MappageInvalide) as erreur:
        return render(request, "contacts/illisible.html", {"import": import_, "erreur": erreur})
    erreur = ""
    if request.method == "POST":
        try:
            imports.lancer(import_, _mappage_saisi(request, lignes, propose["largeur"]))
        except imports.MappageInvalide as refus:
            erreur = str(refus)
        else:
            messages.success(request, "Import lancé : vous pouvez quitter cette page.")
            return redirect("import-suivi", import_.pk)
    par_rang = {rang: champ for champ, rang in propose["colonnes"].items()}
    par_rang |= {rang: f"champ:{nom}" for nom, rang in propose["personnalises"].items()}
    contexte = {
        "import": import_,
        "lignes": lignes,
        "colonnes": [
            {"rang": rang, "choix": par_rang.get(rang, "")} for rang in range(propose["largeur"])
        ],
        "choix": _choix_colonne(request.user.client),
        "entetes": propose["entetes"],
        "erreur": erreur,
    }
    return render(request, "contacts/mappage.html", contexte)


@permission_requise("contacts.importer")
def suivi(request, pk):
    import_ = _import(request, pk)
    return render(request, "contacts/suivi.html", _contexte_suivi(import_))


def _contexte_suivi(import_) -> dict:
    fini = import_.etat in (Import.Etat.TERMINE, Import.Etat.ECHOUE)
    return {
        "import": import_,
        "fini": fini,
        "cause": imports.cause_dominante(import_) if fini and import_.lignes_rejetees else None,
    }


@permission_requise("contacts.importer")
def rejets(request, pk):
    import_ = _import(request, pk)
    if import_.etat != Import.Etat.TERMINE:
        raise Http404
    reponse = StreamingHttpResponse(_rapport_cloisonne(import_), content_type="text/csv")
    reponse["Content-Disposition"] = f'attachment; filename="rejets-{import_.pk}.csv"'
    return reponse


def _rapport_cloisonne(import_):
    """Le rapport est lu pendant l'envoi de la réponse, donc après la fin de la
    transaction de la requête et de son client posé : sans cela, le
    cloisonnement ne rendrait aucune ligne. Le générateur pose le client lui-même."""
    with transaction.atomic():
        poser_client(import_.client_id)
        yield from imports.rapport_csv(import_)
