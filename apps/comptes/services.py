"""Porte d'écriture du module comptes."""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.core.cloisonnement import client_pose, poser_client

from .models import CleApi, Client, GestionnaireUtilisateur, NumeroTest, Utilisateur

# Plafonds d'envoi par palier, en messages. Valeurs provisoires : la fiche F-11
# laisse les plafonds de chaque palier à trancher. Elles ne servent qu'à remplir
# les colonnes non nulles tant que la décision n'est pas prise.
PLAFONDS_PAR_PALIER = {
    Client.Palier.DECOUVERTE: {"horaire": 100, "journalier": 500},
    Client.Palier.VERIFIE: {"horaire": 1_000, "journalier": 5_000},
    Client.Palier.CONTRACTUEL: {"horaire": 50_000, "journalier": 500_000},
}


@transaction.atomic
def creer_client(*, raison_sociale, forme, palier=Client.Palier.DECOUVERTE, **champs) -> Client:
    """Crée un client. La politique de cloisonnement n'accepte une ligne de
    `comptes_client` que pour le client posé : le client créé est donc posé le
    temps de l'insertion, puis le client précédent est rétabli."""
    plafonds = PLAFONDS_PAR_PALIER[palier]
    champs.setdefault("plafond_horaire", plafonds["horaire"])
    champs.setdefault("plafond_journalier", plafonds["journalier"])
    client = Client(raison_sociale=raison_sociale, forme=forme, palier=palier, **champs)
    # L'unicité du RCCM et de l'IFU est vérifiée par la base : sous cloisonnement,
    # une vérification applicative ne verrait pas les autres clients.
    client.full_clean(validate_unique=False, validate_constraints=False)
    precedent = client_pose()
    poser_client(client.id)
    client.save(force_insert=True)
    poser_client(precedent or None)
    return client


@transaction.atomic
def changer_mode_reglement(client: Client, mode: str, *, auteur, motif: str) -> Client:
    """Passe un client à l'autre mode de règlement, par décision d'un agent,
    tracée au journal d'audit. Les campagnes déjà réservées gardent le mode de
    leur réservation : seul ce qui sera réservé ensuite suit le nouveau mode."""
    from apps.core.audit import auditer

    if mode not in Client.Reglement.values:
        raise ValueError(f"Mode de règlement inconnu : {mode!r}")
    if not motif or not motif.strip():
        raise ValueError("Un changement de mode de règlement exige un motif.")
    avant = Client.objects.select_for_update().values_list("mode_reglement", flat=True)
    avant = avant.get(id=client.id)
    if avant == mode:
        return client
    Client.objects.filter(id=client.id).update(mode_reglement=mode)
    auditer(
        "comptes.mode_reglement",
        client,
        auteur=auteur,
        client=client,
        avant={"mode_reglement": avant},
        apres={"mode_reglement": mode},
        motif=motif.strip(),
    )
    client.mode_reglement = mode
    return client


SEUIL_ALERTE_MAXIMUM = 1_000_000_000


@transaction.atomic
def regler_seuil_alerte(client: Client, seuil, *, auteur, adresse_ip=None) -> Client:
    """Règle le seuil d'alerte de solde bas (T-111), en FCFA entiers ; zéro
    pour aucune alerte. L'alerte est réarmée : le prochain mouvement qui laisse
    le disponible sous le nouveau seuil la déclenche."""
    from apps.core.audit import auditer

    try:
        seuil = int(str(seuil).strip())
    except ValueError:
        raise ValueError("Le seuil est un montant entier en FCFA.") from None
    if not 0 <= seuil <= SEUIL_ALERTE_MAXIMUM:
        raise ValueError("Le seuil va de zéro (aucune alerte) à un milliard de FCFA.")
    avant = Client.objects.select_for_update().values_list("seuil_alerte_solde", flat=True)
    avant = avant.get(id=client.id)
    Client.objects.filter(id=client.id).update(seuil_alerte_solde=seuil, alerte_solde_armee=True)
    auditer(
        "comptes.seuil_alerte_solde",
        client,
        auteur=auteur,
        client=client,
        avant={"seuil_alerte_solde": avant},
        apres={"seuil_alerte_solde": seuil},
        adresse_ip=adresse_ip,
    )
    client.seuil_alerte_solde, client.alerte_solde_armee = seuil, True
    return client


@transaction.atomic
def creer_utilisateur(*, courriel, mot_de_passe, nom, client=None, **champs) -> Utilisateur:
    return Utilisateur.objects.create_user(courriel, mot_de_passe, nom=nom, client=client, **champs)


class NumeroTestRefuse(ValueError):
    pass


CODE_VALIDITE = timedelta(minutes=10)


@transaction.atomic
def declarer_numero_test(client, numero_normalise: str) -> tuple[NumeroTest, str]:
    """Déclare un numéro de test et renvoie le code à six chiffres à lui envoyer.
    Seul le condensat du code est conservé."""
    if client.numeros_test.count() >= NumeroTest.MAXIMUM_PAR_CLIENT:
        raise NumeroTestRefuse(f"{NumeroTest.MAXIMUM_PAR_CLIENT} numéros de test au plus.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    numero, _ = NumeroTest.objects.update_or_create(
        client=client,
        numero_normalise=numero_normalise,
        defaults={
            "code_condensat": make_password(code),
            "code_expire_le": timezone.now() + CODE_VALIDITE,
            "verifie_le": None,
        },
    )
    return numero, code


@transaction.atomic
def verifier_numero_test(client, numero_normalise: str, code: str) -> bool:
    numero = (
        NumeroTest.objects.select_for_update()
        .filter(client=client, numero_normalise=numero_normalise)
        .first()
    )
    if (
        numero is None
        or not numero.code_condensat
        or numero.code_expire_le is None
        or numero.code_expire_le < timezone.now()
        or not check_password(code, numero.code_condensat)
    ):
        return False
    numero.verifie_le, numero.code_condensat = timezone.now(), ""
    numero.save(update_fields=["verifie_le", "code_condensat", "modifie_le"])
    return True


MOTIF_BAC_A_SABLE = "BAC_A_SABLE_NUMERO_NON_VERIFIE"


MOTIF_COMPTE_NON_ACTIF = "COMPTE_NON_ACTIF"


def controle_compte_actif(message) -> str | None:
    """Contrôle avant soumission (T-95) : le message d'un compte suspendu ou
    clos ne part pas, quel que soit le chemin par lequel il est venu (envoi
    unitaire, API). Les messages d'une campagne en pause sont retenus avant ce
    contrôle, et repartent à la reprise. Un message système, obligation de la
    plateforme envers l'abonné (confirmation d'un STOP), part toujours."""
    if message.nature == "SYSTEME":
        return None
    if message.client.etat == Client.Etat.ACTIF:
        return None
    return MOTIF_COMPTE_NON_ACTIF


def controle_bac_a_sable(message) -> str | None:
    """Contrôle avant soumission, enregistré au démarrage (voir apps.core.controles)."""
    if envoi_autorise(message.client, message.numero_normalise):
        return None
    return MOTIF_BAC_A_SABLE


def envoi_autorise(client, numero_normalise: str) -> bool:
    """Le bac à sable : un compte au palier Découverte n'envoie qu'à ses numéros
    de test vérifiés. Contrôlé dans le moteur d'envoi, jamais dans le portail
    seul, qui serait contourné par le premier appel d'API."""
    if client.palier != Client.Palier.DECOUVERTE:
        return True
    return NumeroTest.objects.filter(
        client=client, numero_normalise=numero_normalise, verifie_le__isnull=False
    ).exists()


# Membres et invitations (T-32, T-36) ---------------------------------------

ROLES_DE_LANCEMENT = (Utilisateur.Role.ADMIN, Utilisateur.Role.UTILISATEUR)


class MembreRefuse(ValueError):
    pass


def _lien(chemin: str, jeton: str) -> str:
    from django.conf import settings

    return f"{settings.PORTAIL_URL}/{chemin}/{jeton}"


def envoyer_invitation(utilisateur: Utilisateur) -> None:
    from apps.core.courriels import envoyer

    from . import jetons

    lien = _lien("invitation", jetons.creer(utilisateur, jetons.INVITATION))
    envoyer(
        utilisateur.courriel,
        "Votre accès à EL-IMMEKA SMS",
        f"Bonjour {utilisateur.nom},\n\n"
        f"Un accès au compte {utilisateur.client} vous est ouvert. Pour le "
        f"créer, choisissez votre mot de passe par ce lien, valable sept jours :\n\n"
        f"{lien}\n\nSi vous n'attendiez pas ce message, ignorez-le.",
    )


def envoyer_verification(utilisateur: Utilisateur) -> None:
    from apps.core.courriels import envoyer

    from . import jetons

    lien = _lien("verification", jetons.creer(utilisateur, jetons.VERIFICATION))
    envoyer(
        utilisateur.courriel,
        "Vérifiez votre adresse, EL-IMMEKA SMS",
        f"Bonjour {utilisateur.nom},\n\nPour vérifier votre adresse, ouvrez ce lien, "
        f"valable vingt-quatre heures :\n\n{lien}",
    )


@transaction.atomic
def inviter(*, client, courriel: str, nom: str, role: str, auteur, adresse_ip=None) -> Utilisateur:
    from apps.core.audit import auditer

    courriel = GestionnaireUtilisateur.normaliser_courriel(courriel)
    if role not in ROLES_DE_LANCEMENT:
        raise MembreRefuse("Rôle non ouvert au lancement.")
    existant = Utilisateur.objects.filter(courriel=courriel).first()
    if existant is not None:
        if existant.client_id == client.id:
            raise MembreRefuse(f"{courriel} est déjà membre de ce compte.")
        raise MembreRefuse(f"{courriel} ne peut pas être invitée.")
    membre = Utilisateur.objects.create_user(
        courriel, None, nom=nom.strip(), client=client, role=role
    )
    envoyer_invitation(membre)
    auditer(
        "comptes.membre.invite",
        membre,
        auteur=auteur,
        client=client,
        apres={"courriel": courriel, "role": role},
        adresse_ip=adresse_ip,
    )
    return membre


@transaction.atomic
def accepter_invitation(jeton: str, mot_de_passe: str) -> Utilisateur:
    """Pose le mot de passe et vérifie l'adresse : le lien prouve que la
    personne reçoit bien les courriels de cette adresse. Lève LienExpire,
    LienInvalide, ou ValidationError si le mot de passe est refusé."""
    from django.contrib.auth.password_validation import validate_password

    from . import jetons

    membre = jetons.lire(jeton, jetons.INVITATION)
    validate_password(mot_de_passe, membre)
    membre.set_password(mot_de_passe)
    membre.courriel_verifie_le = timezone.now()
    membre.save(update_fields=["password", "courriel_verifie_le", "modifie_le"])
    return membre


@transaction.atomic
def verifier_courriel(jeton: str) -> Utilisateur:
    from . import jetons

    utilisateur = jetons.lire(jeton, jetons.VERIFICATION)
    utilisateur.courriel_verifie_le = timezone.now()
    utilisateur.save(update_fields=["courriel_verifie_le", "modifie_le"])
    return utilisateur


@transaction.atomic
def renvoyer_lien(courriel: str) -> None:
    """Renvoie un lien à qui n'a pas encore activé son accès ou vérifié son
    adresse. Sans effet visible sinon : la réponse est la même que l'adresse
    existe ou non."""
    utilisateur = Utilisateur.objects.filter(
        courriel=GestionnaireUtilisateur.normaliser_courriel(courriel), is_active=True
    ).first()
    if utilisateur is None or utilisateur.courriel_verifie_le is not None:
        return
    # Page anonyme : aucun client posé. Le courriel nomme le compte, qu'on ne
    # verrait pas sans poser celui de l'utilisateur, le temps de l'envoi.
    poser_client(utilisateur.client_id)
    if utilisateur.has_usable_password():
        envoyer_verification(utilisateur)
    else:
        envoyer_invitation(utilisateur)


def _administrateurs_verrouilles(client) -> list:
    return list(
        Utilisateur.objects.select_for_update()
        .filter(client=client, role=Utilisateur.Role.ADMIN, is_active=True)
        .values_list("pk", flat=True)
    )


@transaction.atomic
def changer_role(membre: Utilisateur, role: str, *, auteur, adresse_ip=None) -> Utilisateur:
    from apps.core.audit import auditer

    if role not in ROLES_DE_LANCEMENT:
        raise MembreRefuse("Rôle non ouvert au lancement.")
    administrateurs = _administrateurs_verrouilles(membre.client)
    if role != Utilisateur.Role.ADMIN and administrateurs == [membre.pk]:
        raise MembreRefuse("Le dernier administrateur du compte ne peut pas être rétrogradé.")
    avant = membre.role
    if avant == role:
        return membre
    Utilisateur.objects.filter(pk=membre.pk).update(role=role, modifie_le=timezone.now())
    auditer(
        "comptes.role.modifie",
        membre,
        auteur=auteur,
        client=membre.client,
        avant={"role": avant},
        apres={"role": role},
        adresse_ip=adresse_ip,
    )
    membre.role = role
    return membre


@transaction.atomic
def retirer_membre(membre: Utilisateur, *, auteur, adresse_ip=None) -> None:
    """Retire l'accès sans effacer : les actions passées gardent leur auteur."""
    from apps.core.audit import auditer

    administrateurs = _administrateurs_verrouilles(membre.client)
    if administrateurs == [membre.pk]:
        raise MembreRefuse("Le dernier administrateur du compte ne peut pas être retiré.")
    Utilisateur.objects.filter(pk=membre.pk).update(is_active=False, modifie_le=timezone.now())
    auditer(
        "comptes.membre.retire",
        membre,
        auteur=auteur,
        client=membre.client,
        avant={"actif": True, "role": membre.role},
        apres={"actif": False},
        adresse_ip=adresse_ip,
    )


# Clôture (T-89)


class ClotureRefusee(ValueError):
    pass


@transaction.atomic
def clore(client: Client, *, auteur, motif: str, adresse_ip=None) -> Client:
    """Clôt un compte : plus aucun envoi, plus aucune connexion de ses membres.
    Les pièces comptables et les traces restent ; les contacts sont effacés au
    terme du délai, par l'ordonnanceur. Tracé au journal d'audit."""
    from apps.core.audit import auditer

    motif = (motif or "").strip()
    if not motif:
        raise ClotureRefusee("Une clôture exige un motif : la demande écrite du client.")
    client = Client.objects.select_for_update().get(id=client.id)
    if client.etat == Client.Etat.CLOTURE:
        raise ClotureRefusee("Ce compte est déjà clos.")
    avant = client.etat
    client.etat = Client.Etat.CLOTURE
    client.cloture_le = timezone.now()
    client.save(update_fields=["etat", "cloture_le", "modifie_le"])
    membres = Utilisateur.objects.filter(client=client, is_active=True)
    retires = list(membres.values_list("id", flat=True))
    membres.update(is_active=False)
    auditer(
        "comptes.cloture",
        client,
        auteur=auteur,
        client=client,
        avant={"etat": avant},
        apres={"etat": client.etat, "membres_desactives": [str(i) for i in retires]},
        motif=motif,
        adresse_ip=adresse_ip,
    )
    return client


# Suspension et réactivation (T-95)


class SuspensionRefusee(ValueError):
    pass


@transaction.atomic
def suspendre(
    client: Client, *, auteur, motif: str, adresse_ip=None, details: dict | None = None
) -> Client:
    """Suspend un compte actif : plus aucun envoi, ni confirmé ni soumis.
    `auteur` est nul quand la suspension est automatique (T-101). `details`
    complète la trace : les campagnes mises en pause, par exemple."""
    from apps.core.audit import auditer

    motif = (motif or "").strip()
    if not motif:
        raise SuspensionRefusee("Une suspension exige un motif.")
    client = Client.objects.select_for_update().get(id=client.id)
    if client.etat != Client.Etat.ACTIF:
        raise SuspensionRefusee(f"Ce compte est {client.get_etat_display().lower()}.")
    client.etat = Client.Etat.SUSPENDU
    client.save(update_fields=["etat", "modifie_le"])
    auditer(
        "comptes.suspension",
        client,
        auteur=auteur,
        client=client,
        avant={"etat": Client.Etat.ACTIF},
        apres={"etat": client.etat, **(details or {})},
        motif=motif,
        adresse_ip=adresse_ip,
    )
    return client


@transaction.atomic
def reactiver(
    client: Client, *, auteur, motif: str, adresse_ip=None, details: dict | None = None
) -> Client:
    """Réactive un compte suspendu. Un compte clos ne se réactive pas."""
    from apps.core.audit import auditer

    motif = (motif or "").strip()
    if not motif:
        raise SuspensionRefusee("Une réactivation exige un motif.")
    client = Client.objects.select_for_update().get(id=client.id)
    if client.etat != Client.Etat.SUSPENDU:
        raise SuspensionRefusee(f"Ce compte est {client.get_etat_display().lower()}.")
    client.etat = Client.Etat.ACTIF
    client.save(update_fields=["etat", "modifie_le"])
    auditer(
        "comptes.reactivation",
        client,
        auteur=auteur,
        client=client,
        avant={"etat": Client.Etat.SUSPENDU},
        apres={"etat": client.etat, **(details or {})},
        motif=motif,
        adresse_ip=adresse_ip,
    )
    return client


# Clés d'API (T-106)

# Argon2id à coût réduit, propre aux clés : le secret est tiré au hasard sur
# 256 bits, une attaque par force brute est hors de portée quel que soit le
# coût, et la vérification doit tenir sous dix millisecondes à chaque appel.
# Le mot de passe d'un utilisateur, choisi par un humain, garde le coût fort.
_CONDENSEUR_CLE = None
REGLAGE_PREFIXE = "app.cle_prefixe"
# Une clé n'est réécrite (dernière utilisation) qu'une fois par minute au plus.
RAFRAICHISSEMENT_USAGE = timedelta(minutes=1)


def _condenseur():
    global _CONDENSEUR_CLE
    if _CONDENSEUR_CLE is None:
        from argon2 import PasswordHasher, Type

        _CONDENSEUR_CLE = PasswordHasher(time_cost=2, memory_cost=1024, parallelism=1, type=Type.ID)
    return _CONDENSEUR_CLE


class CleRefusee(ValueError):
    pass


def _decouper(jeton: str) -> tuple[str, str, str] | None:
    """`sk_<environnement>_<prefixe>_<secret>` : environnement, préfixe, secret."""
    parties = (jeton or "").split("_", 3)
    if len(parties) != 4 or parties[0] != "sk":
        return None
    environnement = parties[1].upper()
    if environnement not in CleApi.Environnement.values or not parties[2] or not parties[3]:
        return None
    return environnement, parties[2], parties[3]


@transaction.atomic
def creer_cle(
    *, client, nom: str, auteur, environnement=CleApi.Environnement.LIVE, adresse_ip=None
) -> tuple[CleApi, str]:
    """Crée une clé et rend la clé complète, qui n'est plus jamais lisible
    ensuite : seul son condensat est conservé."""
    from apps.core.audit import auditer

    nom = (nom or "").strip()
    if not nom:
        raise CleRefusee("Une clé porte un nom.")
    if environnement not in CleApi.Environnement.values:
        raise CleRefusee("Environnement inconnu.")
    secret = secrets.token_urlsafe(32)
    while True:
        prefixe = secrets.token_hex(5)
        # Le préfixe est unique sur toute la table, que le cloisonnement cache :
        # la vérification passe par la lecture par préfixe.
        if _lire_par_prefixe(prefixe) is None:
            break
    cle = CleApi.objects.create(
        client=client,
        nom=nom,
        environnement=environnement,
        prefixe=prefixe,
        condensat=_condenseur().hash(secret),
        cree_par=auteur,
    )
    auditer(
        "comptes.cle_api.creation",
        cle,
        auteur=auteur,
        client=client,
        apres={"nom": nom, "prefixe": prefixe, "environnement": environnement},
        adresse_ip=adresse_ip,
    )
    return cle, f"sk_{environnement.lower()}_{prefixe}_{secret}"


@transaction.atomic
def revoquer_cle(cle: CleApi, *, auteur, adresse_ip=None, maintenant=None) -> CleApi:
    """Révoque une clé. La vérification relit l'état en base à chaque appel :
    la clé est refusée dès la validation de cette transaction."""
    from apps.core.audit import auditer

    if cle.etat == CleApi.Etat.REVOQUEE:
        return cle
    cle.etat = CleApi.Etat.REVOQUEE
    cle.revoquee_le = maintenant or timezone.now()
    cle.save(update_fields=["etat", "revoquee_le", "modifie_le"])
    auditer(
        "comptes.cle_api.revocation",
        cle,
        auteur=auteur,
        client=cle.client,
        apres={"prefixe": cle.prefixe},
        adresse_ip=adresse_ip,
    )
    return cle


def _lire_par_prefixe(prefixe: str) -> CleApi | None:
    """Lit la clé d'un préfixe, quel que soit le client posé, par la politique
    de lecture par préfixe (`core` 0040). Le réglage ne vit que le temps de
    cette lecture, local à la transaction."""
    from django.db import connection

    with connection.cursor() as curseur:
        curseur.execute("SELECT set_config(%s, %s, true)", [REGLAGE_PREFIXE, prefixe])
    try:
        return CleApi.objects.filter(prefixe=prefixe).first()
    finally:
        with connection.cursor() as curseur:
            curseur.execute("SELECT set_config(%s, '', true)", [REGLAGE_PREFIXE])


def verifier_cle(jeton: str, *, version: str = "1", maintenant=None) -> CleApi | None:
    """Vérifie une clé présentée à l'API et pose son client. Rend None pour
    une clé inconnue, mal formée, révoquée ou fausse, sans dire laquelle.

    À appeler dans la transaction de la requête. Aucun cache : l'état est relu
    en base à chaque appel, si bien qu'une révocation vaut immédiatement.
    """
    from argon2.exceptions import VerificationError

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("verifier_cle doit être appelé dans une transaction.")
    morceaux = _decouper(jeton)
    cle = _lire_par_prefixe(morceaux[1]) if morceaux else None
    if cle is None or cle.etat != CleApi.Etat.ACTIVE or cle.environnement != morceaux[0]:
        return None
    try:
        _condenseur().verify(cle.condensat, morceaux[2])
    except VerificationError:
        return None
    poser_client(cle.client_id)
    maintenant = maintenant or timezone.now()
    if (
        cle.derniere_utilisation_le is None
        or maintenant - cle.derniere_utilisation_le >= RAFRAICHISSEMENT_USAGE
        or cle.derniere_version != version
    ):
        CleApi.objects.filter(id=cle.id).update(
            derniere_utilisation_le=maintenant, derniere_version=version
        )
        cle.derniere_utilisation_le, cle.derniere_version = maintenant, version
    return cle
