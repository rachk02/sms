"""Import de contacts : dépôt, aperçu et mappage, traitement, rapport.

Le traitement tourne dans le worker d'import, jamais dans la requête web. Les
lignes valides sont écrites par paquets dans une table de transit, puis versées
d'un seul coup dans le répertoire, dans une seule transaction : un import
interrompu n'a rien intégré, et se reprend depuis le début. Import partiel
(décision F-12) : les lignes invalides sont rangées avec leur motif, les autres
importées. Le compte final tient toujours : acceptées plus rejetées plus
doublons égale le nombre de lignes du fichier.
"""

import csv
import io
import re
import unicodedata
import uuid
from collections import Counter
from itertools import islice

from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from apps.core.evenements import publier
from apps.core.numeros import NumeroInvalide, normaliser
from apps.fichiers import services as fichiers
from apps.fichiers.models import Fichier
from apps.telecom.prefixes import classer, table

from . import listes
from .champs import nom_de, noms_declares
from .lecture import FichierIllisible, lecteur_pour
from .models import Import, ImportRejet, ImportTransit, Liste

PAQUET = 1000
LIGNES_APERCU = 20
CHAMPS_FIXES = ("numero", "nom", "prenom", "ville")
MAX_CHAMPS_PERSONNALISES = 10
FILE_IMPORT = "import.contacts"

SYNONYMES = {
    "numero": {"numero", "telephone", "tel", "mobile", "portable", "gsm", "phone", "cellulaire"},
    "nom": {"nom", "name", "nom de famille", "last name"},
    "prenom": {"prenom", "prenoms", "first name"},
    "ville": {"ville", "city", "localite", "commune"},
}


class MappageInvalide(ValueError):
    pass


def cle_entete(entete: str) -> str:
    sans_accent = unicodedata.normalize("NFKD", entete).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", sans_accent.lower()).strip()


def _est_numero(valeur: str) -> bool:
    try:
        normaliser(valeur)
    except NumeroInvalide, KeyError:
        return False
    return True


# Dépôt ---------------------------------------------------------------------------


def deposer(*, client, flux, nom_origine: str) -> Import:
    """Dépose le fichier (type reconnu au contenu, 200 Mo au plus) et crée
    l'import, en attente de son mappage. Lève FichierRefuse."""
    fichier = fichiers.deposer(
        client=client, famille=Fichier.Famille.IMPORT, flux=flux, nom_origine=nom_origine
    )
    return Import.objects.create(client=client, fichier_id=fichier.id, nom_origine=nom_origine)


def _lignes(import_):
    fichier = Fichier.objects.get(id=import_.fichier_id)
    lecteur = lecteur_pour(f"x.{fichiers.extension(fichier)}")
    flux = fichiers.ouvrir(fichier)
    try:
        yield from lecteur(flux)
    finally:
        flux.close()


# Aperçu et mappage (T-46) ----------------------------------------------------------


def apercu(import_) -> list[list[str]]:
    """Les vingt premières lignes du fichier, telles que lues."""
    return [valeurs for _, valeurs in islice(_lignes(import_), LIGNES_APERCU)]


def proposer_mappage(
    lignes: list[list[str]], precedent: dict | None = None, champs: set[str] = frozenset()
) -> dict:
    """Propose un mappage : par les entêtes s'il y en a, sinon par position, la
    colonne du numéro étant celle dont les valeurs se lisent le plus comme des
    numéros. Un mappage précédent de même forme est repris tel quel."""
    if not lignes:
        raise MappageInvalide("Le fichier ne contient aucune ligne.")
    largeur = max(len(ligne) for ligne in lignes)
    premiere = lignes[0]
    entetes = not any(_est_numero(v) for v in premiere) and any(v.strip() for v in premiere)
    entete = [cle_entete(v) for v in premiere] if entetes else []
    if precedent and precedent.get("largeur") == largeur and precedent.get("entete") == entete:
        return precedent
    colonnes: dict[str, int] = {}
    personnalises: dict[str, int] = {}
    if entetes:
        for rang, entete in enumerate(premiere):
            cle = cle_entete(entete)
            fixe = next((c for c, noms in SYNONYMES.items() if cle in noms), None)
            if fixe and fixe not in colonnes:
                colonnes[fixe] = rang
            elif nom_de(cle) in champs:
                # Seuls les champs déclarés par le client reçoivent une colonne.
                personnalises[nom_de(cle)] = rang
    if "numero" not in colonnes:
        donnees = lignes[1:] if entetes else lignes
        scores = Counter(
            rang for ligne in donnees for rang, v in enumerate(ligne) if _est_numero(v)
        )
        if scores:
            colonnes["numero"] = scores.most_common(1)[0][0]
            personnalises = {n: r for n, r in personnalises.items() if r != colonnes["numero"]}
    return {
        "entetes": entetes,
        "entete": entete,
        "largeur": largeur,
        "colonnes": colonnes,
        "personnalises": personnalises,
    }


def _verifier_mappage(mappage: dict, champs: set[str]) -> dict:
    colonnes = mappage.get("colonnes") or {}
    non_declares = set(mappage.get("personnalises") or {}) - champs
    if non_declares:
        raise MappageInvalide(
            f"Champs personnalisés non déclarés : {', '.join(sorted(non_declares))}."
        )
    if "numero" not in colonnes:
        raise MappageInvalide("Indiquez la colonne qui porte le numéro de téléphone.")
    inconnus = set(colonnes) - set(CHAMPS_FIXES)
    if inconnus:
        raise MappageInvalide(f"Champs inconnus : {', '.join(sorted(inconnus))}.")
    if len(mappage.get("personnalises") or {}) > MAX_CHAMPS_PERSONNALISES:
        raise MappageInvalide(f"{MAX_CHAMPS_PERSONNALISES} champs personnalisés au plus.")
    return mappage


def mappage_precedent(client, lignes: list[list[str]]) -> dict | None:
    """Le mappage du dernier import terminé du client dont le fichier avait la
    même forme (même largeur, mêmes entêtes) : proposé par défaut."""
    champs = noms_declares(client)
    propose = proposer_mappage(lignes, champs=champs)
    for ancien in Import.objects.filter(client=client, etat=Import.Etat.TERMINE).order_by(
        "-termine_le"
    )[:20]:
        mappage = ancien.mappage
        if (
            mappage.get("largeur") == propose["largeur"]
            and mappage.get("entete") == propose["entete"]
        ):
            # Un champ supprimé depuis ne se propose plus.
            personnalises = mappage.get("personnalises") or {}
            return mappage | {
                "personnalises": {n: r for n, r in personnalises.items() if n in champs}
            }
    return None


@transaction.atomic
def lancer(import_, mappage: dict) -> Import:
    """Confirme le mappage et met l'import en file. Aucun import ne démarre sans
    mappage confirmé."""
    import_ = Import.objects.select_for_update().get(id=import_.id)
    if import_.etat != Import.Etat.DEPOSE:
        raise MappageInvalide("Cet import est déjà lancé.")
    if liste_id := mappage.get("liste_id"):
        try:
            liste_id = str(uuid.UUID(str(liste_id)))
        except ValueError:
            raise MappageInvalide("Liste introuvable pour ce compte.") from None
        if not Liste.objects.filter(id=liste_id, client_id=import_.client_id).exists():
            raise MappageInvalide("Liste introuvable pour ce compte.")
        mappage = mappage | {"liste_id": liste_id}
    import_.mappage = _verifier_mappage(mappage, noms_declares(import_.client))
    import_.etat = Import.Etat.EN_FILE
    import_.save(update_fields=["mappage", "etat", "modifie_le"])
    publier(
        FILE_IMPORT,
        echange="sms",
        client_id=import_.client_id,
        objet_id=import_.id,
        donnees={"import_id": str(import_.id)},
        cle_idempotence=f"import:{import_.id}",
    )
    return import_


# Traitement (T-47) --------------------------------------------------------------


def _valeur(valeurs: list[str], rang: int | None) -> str:
    if rang is None or rang >= len(valeurs):
        return ""
    return valeurs[rang].strip()


def _contenu(valeurs: list[str]) -> str:
    tampon = io.StringIO()
    csv.writer(tampon, delimiter=";").writerow(valeurs)
    return tampon.getvalue().rstrip("\r\n")


def _vider(import_) -> None:
    ImportTransit.objects.filter(import_lie=import_).delete()
    ImportRejet.objects.filter(import_lie=import_).delete()


def _ecrire_paquet(import_, valides, rejets, total, rejetees) -> None:
    with transaction.atomic():
        ImportTransit.objects.bulk_create(valides)
        ImportRejet.objects.bulk_create(rejets)
        # La progression affichée est celle des lignes réellement traitées.
        Import.objects.filter(id=import_.id).update(
            lignes_total=total, lignes_rejetees=rejetees, modifie_le=timezone.now()
        )


FUSION = """
INSERT INTO contacts_contact (
    id, client_id, numero_normalise, numero_origine, operateur_presume,
    operateur_confirme, nom, prenom, ville, champs, origine, consentement_source,
    actif, cree_le, modifie_le
)
SELECT DISTINCT ON (numero_normalise)
    gen_random_uuid(), client_id, numero_normalise, numero_origine, operateur_presume,
    '', nom, prenom, ville, champs, 'IMPORT', '', true, now(), now()
FROM contacts_import_transit
WHERE import_id = %s
ORDER BY numero_normalise, numero_ligne
ON CONFLICT (client_id, numero_normalise) DO NOTHING
"""


def traiter(import_id) -> Import:
    """Traite un import de bout en bout. Appelé par le worker d'import, sous son
    rôle. Rejouable : un traitement interrompu repart de zéro."""
    import_ = Import.objects.get(id=import_id)
    if import_.etat == Import.Etat.TERMINE:
        return import_
    if import_.etat == Import.Etat.DEPOSE:
        raise MappageInvalide("Aucun import ne démarre sans mappage confirmé.")
    mappage = import_.mappage
    colonnes, personnalises = mappage["colonnes"], mappage.get("personnalises") or {}
    prefixes = table(import_.client.pays)
    _vider(import_)
    Import.objects.filter(id=import_.id).update(
        etat=Import.Etat.EN_COURS, demarre_le=timezone.now(), erreur="", lignes_total=0
    )
    total = rejetees = 0
    valides, rejets = [], []
    try:
        for numero_ligne, valeurs in _lignes(import_):
            if mappage["entetes"] and numero_ligne == 1:
                continue
            if not any(v.strip() for v in valeurs):
                continue  # ligne vide : ni importée ni rejetée
            total += 1
            brut = _valeur(valeurs, colonnes["numero"])
            try:
                if not brut:
                    raise NumeroInvalide(brut, "colonne du numéro vide")
                numero = normaliser(brut, import_.client.pays)
            except NumeroInvalide as refus:
                rejetees += 1
                rejets.append(
                    ImportRejet(
                        client_id=import_.client_id,
                        import_lie=import_,
                        numero_ligne=numero_ligne,
                        contenu_ligne=_contenu(valeurs),
                        motif=refus.cause,
                    )
                )
            else:
                valides.append(
                    ImportTransit(
                        client_id=import_.client_id,
                        import_lie=import_,
                        numero_ligne=numero_ligne,
                        numero_normalise=numero,
                        numero_origine=brut[:50],
                        operateur_presume=classer(numero, import_.client.pays, prefixes),
                        nom=_valeur(valeurs, colonnes.get("nom"))[:150],
                        prenom=_valeur(valeurs, colonnes.get("prenom"))[:150],
                        ville=_valeur(valeurs, colonnes.get("ville"))[:100],
                        champs={
                            nom: _valeur(valeurs, rang)
                            for nom, rang in personnalises.items()
                            if _valeur(valeurs, rang)
                        },
                    )
                )
            if len(valides) + len(rejets) >= PAQUET:
                _ecrire_paquet(import_, valides, rejets, total, rejetees)
                valides, rejets = [], []
        _ecrire_paquet(import_, valides, rejets, total, rejetees)
    except FichierIllisible as erreur:
        # Une moitié de fichier importée est pire qu'un échec franc.
        _vider(import_)
        Import.objects.filter(id=import_.id).update(
            etat=Import.Etat.ECHOUE,
            erreur=str(erreur),
            lignes_rejetees=0,
            termine_le=timezone.now(),
        )
        return Import.objects.get(id=import_.id)

    with transaction.atomic():
        with connection.cursor() as curseur:
            curseur.execute(FUSION, [str(import_.id)])
            acceptees = curseur.rowcount
        if mappage.get("liste_id"):
            # Ajout en masse : tous les numéros du fichier, nouveaux ou déjà connus.
            listes.ajouter_depuis_import(mappage["liste_id"], import_.id, import_.client_id)
        ImportTransit.objects.filter(import_lie=import_).delete()
        Import.objects.filter(id=import_.id).update(
            etat=Import.Etat.TERMINE,
            lignes_total=total,
            lignes_acceptees=acceptees,
            lignes_rejetees=rejetees,
            doublons=total - rejetees - acceptees,
            termine_le=timezone.now(),
        )
    return Import.objects.get(id=import_.id)


# Rapport des rejets (T-48) ------------------------------------------------------


def rapport_csv(import_):
    """Les lignes rejetées, telles que lues, suivies de leur motif : le fichier se
    corrige et se réimporte, la colonne du motif étant alors ignorée."""
    yield "numero_ligne;motif;ligne_d_origine\n"
    for rejet in ImportRejet.objects.filter(import_lie=import_).order_by("numero_ligne").iterator():
        tampon = io.StringIO()
        csv.writer(tampon, delimiter=";").writerow(
            [rejet.numero_ligne, rejet.motif, rejet.contenu_ligne]
        )
        yield tampon.getvalue()


def cause_dominante(import_) -> str | None:
    """Le motif de rejet le plus fréquent, compté par la base."""
    plus_frequent = (
        ImportRejet.objects.filter(import_lie=import_)
        .values("motif")
        .annotate(nombre=Count("id"))
        .order_by("-nombre")
        .first()
    )
    return plus_frequent["motif"] if plus_frequent else None
