"""Écrans de l'import de contacts."""

from django.contrib import messages
from django.db import transaction
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.comptes.permissions import permission_requise
from apps.core.cloisonnement import poser_client
from apps.fichiers.services import FichierRefuse

from . import imports
from .lecture import FichierIllisible
from .models import Import

CHOIX_COLONNE = (
    ("", "Ignorer"),
    ("numero", "Numéro de téléphone"),
    ("nom", "Nom"),
    ("prenom", "Prénom"),
    ("ville", "Ville"),
    ("personnalise", "Champ personnalisé"),
)


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
        if choix == "personnalise":
            nom = (entete[rang] if rang < len(entete) else "") or f"colonne {rang + 1}"
            personnalises[nom.replace(" ", "_")] = rang
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
    return imports.mappage_precedent(client, lignes) or imports.proposer_mappage(lignes)


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
    par_rang |= {rang: "personnalise" for rang in propose["personnalises"].values()}
    contexte = {
        "import": import_,
        "lignes": lignes,
        "colonnes": [
            {"rang": rang, "choix": par_rang.get(rang, "")} for rang in range(propose["largeur"])
        ],
        "choix": CHOIX_COLONNE,
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
