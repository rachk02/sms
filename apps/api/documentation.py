"""Documentation de l'API publique, issue du code (T-110).

Une seule source : ce module. Le schéma OpenAPI (`/v1/openapi.json`) et la
page de documentation (`/documentation/api`) en sont tirés, comme les exemples
dans les quatre langages. Les chemins viennent des routes déclarées dans
`apps/api/urls.py`, les codes de refus des envois de
`campagnes.services.accepter_unitaires`, les événements de webhook du modèle
`PointReception`. Les tests (`apps/api/tests/test_documentation.py`) refusent
une route sans fiche, une fiche sans route, des méthodes qui diffèrent de
celles de la vue, un code d'erreur rendu par le code mais absent d'ici ou
documenté ici mais jamais rendu, et un texte d'en-tête qui s'écarterait de la
politique de version.
"""

import json
import re
from dataclasses import dataclass, field

from django.urls import URLPattern

from apps.campagnes.services import LOT_MAXIMUM, SEGMENTS_MAXIMUM

from .auth import VERSION
from .models import PointReception
from .webhooks import TOLERANCE_S

TITRE = "API EL-IMMEKA SMS"
CLE_EXEMPLE = "sk_test_0a1b2c3d4e_VotreSecretIci"
MESSAGE_EXEMPLE = "019a5f3e-7c2b-7d41-9a8e-3f2b1c0d4e5a"

# Texte repris tel quel de docs/api/politique-de-version.md (T-105) ; un test
# vérifie qu'il n'en diverge pas.
REGLES_INTEGRATEUR = (
    "Ignorez les champs que vous ne connaissez pas. Une réponse peut gagner un "
    "champ à tout moment, sans changement de version : ne validez pas les réponses "
    "contre un schéma strict qui refuserait un champ supplémentaire, et ne "
    "reconstruisez pas un objet en exigeant qu'il n'ait que les champs documentés.",
    "Prévoyez une branche par défaut pour toute liste fermée. Un état de message "
    "ou un code d'erreur nouveau peut apparaître ; il est annoncé à l'avance au "
    "journal des modifications, mais votre code ne doit pas tomber s'il le reçoit "
    "avant d'avoir été mis à jour. Traitez un état inconnu comme un état non final, "
    "et un code d'erreur inconnu selon son code HTTP.",
    "Ne dépendez pas de l'ordre des champs dans une réponse, ni de l'ordre des en-têtes.",
)

AUTHENTIFICATION = (
    "Chaque appel porte la clé du compte dans l'en-tête Authorization: Bearer <clé>. "
    "Une clé se crée dans l'espace du compte, rubrique Clés d'API, par un "
    "administrateur ; elle n'est montrée qu'une fois. Une clé révoquée est refusée "
    "aussitôt. Les clés sk_live_ envoient de vrais messages et consomment du crédit."
)

BAC_A_SABLE = (
    "Une clé sk_test_ ouvre le bac à sable : mêmes routes, mêmes contrôles, réponses "
    "de même forme qu'en production, mais aucun message ne part, aucun crédit n'est "
    "consommé et le plafond du compte n'est pas décompté. Le dernier chiffre du numéro "
    "choisit l'accusé simulé : 0 à 6 livré, 7 non livré, 8 expiré, 9 sans accusé. Le "
    "message est soumis une seconde après sa réception, son accusé arrive quatre "
    "secondes après (vingt pour le sans accusé), et l'issue part en webhook avec "
    "environnement TEST dans les données. Les messages d'essai restent lisibles trente "
    "jours, uniquement avec une clé d'essai ; une clé d'idempotence d'essai ne sert "
    "jamais en production."
)

QUOTAS = (
    "Chaque clé dispose d'une rafale par seconde et d'un débit soutenu par minute ; "
    "le compte, d'un plafond horaire et journalier de messages. Les en-têtes "
    "Quota-Cle-Seconde-Restant, Quota-Cle-Minute-Restant, Quota-Compte-Heure-Restant "
    "et Quota-Compte-Jour-Restant (et leurs pendants -Limite) accompagnent chaque "
    "réponse. Au-delà du quota de la clé : 429 QUOTA_CLE_DEPASSE et Retry-After, en "
    "secondes."
)

IDEMPOTENCE = (
    "Un POST /v1/messages peut porter l'en-tête Idempotency-Key (255 caractères au "
    "plus), recommandé : rejoué avec la même clé et le même corps dans les "
    "vingt-quatre heures, il rend la même réponse, avec Idempotent-Rejoue: true, sans "
    "envoyer une seconde fois. Après une coupure réseau, rejouez toujours avec la "
    "même clé."
)

# Codes d'erreur de l'enveloppe {"erreur": {"code", "message", "requete_id"}}.
ERREURS = {
    "CLE_INVALIDE": (401, "Clé absente, inconnue, mal formée ou révoquée."),
    "METHODE_REFUSEE": (405, "Méthode HTTP non prise en charge par cette route."),
    "QUOTA_CLE_DEPASSE": (429, "Quota de la clé atteint ; réessayer après Retry-After."),
    "CORPS_INVALIDE": (400, "Corps illisible, pas un objet JSON, ou liste de messages vide."),
    "LOT_TROP_GRAND": (400, f"Plus de {LOT_MAXIMUM} messages dans un lot."),
    "IDEMPOTENCE_INVALIDE": (400, "Clé d'idempotence de plus de 255 caractères."),
    "IDEMPOTENCE_REUTILISEE": (422, "Clé d'idempotence déjà servie pour un autre corps."),
    "ENVOI_REFUSE": (422, "Envoi refusé pour tout le compte (compte suspendu, par exemple)."),
    "SOLDE_OCCUPE": (503, "Solde occupé par une autre opération ; réessayer après Retry-After."),
    "RECHERCHE_REFUSEE": (400, "Plage de dates, filtre, curseur ou limite invalide."),
    "MESSAGE_INCONNU": (404, "Aucun message de ce compte ne porte cet identifiant."),
}

# Refus message par message d'un envoi, dans `messages[].erreur.code`.
REFUS = {
    "NUMERO_INVALIDE": "Numéro illisible ou hors du plan de numérotation.",
    "RESEAU_INCONNU": "Le préfixe du numéro ne correspond à aucun réseau servi.",
    "TEXTE_VIDE": "Texte absent ou blanc.",
    "TEXTE_TROP_LONG": f"Plus de {SEGMENTS_MAXIMUM} segments.",
    "EXPEDITEUR_NON_AUTORISE": "Expéditeur non approuvé sur le réseau du numéro.",
    "TARIF_ABSENT": "Aucun tarif en vigueur pour ce réseau.",
    "PLAFOND_COMPTE": "Plafond horaire ou journalier du compte atteint.",
    "SOLDE_INSUFFISANT": "Disponible insuffisant pour ce message.",
}

ETATS = {
    "RECU": "Accepté, en attente de traitement.",
    "EN_ATTENTE": "Retenu (fenêtre légale d'envoi, par exemple).",
    "EN_FILE": "Dans la file de son réseau.",
    "SOUMIS": "Remis à l'opérateur ; l'accusé est attendu.",
    "LIVRE": "Livré au téléphone (final).",
    "NON_LIVRE": "Non livré selon l'opérateur (final).",
    "EXPIRE": "Expiré chez l'opérateur sans livraison (final).",
    "SANS_ACCUSE": "Aucun accusé reçu dans le délai (final).",
    "REJETE": "Refusé avant ou à la soumission ; le crédit est rendu (final).",
    "ANNULE": "Annulé avant la soumission (final).",
}


@dataclass(frozen=True)
class Parametre:
    nom: str
    ou: str  # path, query, header
    description: str
    obligatoire: bool = False
    type: str = "string"


@dataclass(frozen=True)
class Fiche:
    nom: str  # nom de la route dans apps/api/urls.py
    methode: str
    resume: str
    description: str
    reponse: dict
    statut: int = 200
    parametres: tuple[Parametre, ...] = ()
    corps: dict | None = None
    erreurs: tuple[str, ...] = ()
    chemin_exemple: dict = field(default_factory=dict)
    requete_exemple: str = ""


ENVOI_REPONSE = {
    "acceptes": 1,
    "rejetes": 1,
    "messages": [
        {
            "rang": 0,
            "id": MESSAGE_EXEMPLE,
            "etat": "RECU",
            "numero": "+22670000001",
            "reseau": "ORANGE",
            "expediteur": "BOUTIQUE",
            "segments": 1,
            "cout": 20,
            "recu_le": "2026-11-08T09:15:02.481+00:00",
        },
        {
            "rang": 1,
            "erreur": {"code": "NUMERO_INVALIDE", "message": "Numéro invalide : trop court."},
        },
    ],
}

LIGNE_JOURNAL = {
    "id": MESSAGE_EXEMPLE,
    "etat": "LIVRE",
    "numero": "+22670000001",
    "reseau": "ORANGE",
    "expediteur": "BOUTIQUE",
    "cout": 20,
    "motif_echec": None,
    "campagne_id": None,
    "recu_le": "2026-11-08T09:15:02.481+00:00",
}

FICHES = (
    Fiche(
        nom="api-messages",
        methode="POST",
        resume="Envoyer un message ou un lot",
        description=(
            "Un message (l'objet seul) ou un lot sous `messages`, "
            f"{LOT_MAXIMUM} au plus. Réponse 202 dès l'acceptation, un identifiant par "
            "message accepté et un refus nommé pour chaque autre, dans l'ordre et par "
            "rang ; un refus n'annule pas les autres. Rien d'accepté : 422 avec le même "
            "détail. Le suivi est asynchrone : statut par message ou webhook. "
            "`expediteur` est facultatif (numéro court par défaut) ; un champ inconnu "
            "est ignoré."
        ),
        statut=202,
        parametres=(
            Parametre("Idempotency-Key", "header", "Clé d'idempotence, 255 caractères au plus."),
        ),
        corps={
            "messages": [
                {
                    "numero": "70000001",
                    "texte": "Votre commande est prête.",
                    "expediteur": "BOUTIQUE",
                },
                {"numero": "abc", "texte": "Votre commande est prête."},
            ]
        },
        reponse=ENVOI_REPONSE,
        erreurs=(
            "CORPS_INVALIDE",
            "LOT_TROP_GRAND",
            "IDEMPOTENCE_INVALIDE",
            "IDEMPOTENCE_REUTILISEE",
            "ENVOI_REFUSE",
            "SOLDE_OCCUPE",
        ),
    ),
    Fiche(
        nom="api-messages",
        methode="GET",
        resume="Lire le journal des messages",
        description=(
            "Des plus récents aux plus anciens, bornés par une plage de dates : `debut` "
            "et `fin` (AAAA-MM-JJ), 31 jours au plus ; sans aucune des deux, les sept "
            "derniers jours. Pagination par curseur : `suivant` se repasse dans "
            "`apres`, stable même si des messages arrivent pendant le parcours."
        ),
        parametres=(
            Parametre("debut", "query", "Premier jour inclus, AAAA-MM-JJ."),
            Parametre("fin", "query", "Dernier jour inclus, AAAA-MM-JJ."),
            Parametre("etat", "query", "Un état de message."),
            Parametre("numero", "query", "Numéro du destinataire."),
            Parametre("campagne", "query", "Identifiant de campagne."),
            Parametre("limite", "query", "De 1 à 200, 50 par défaut.", type="integer"),
            Parametre("apres", "query", "Curseur rendu dans `suivant`."),
        ),
        reponse={
            "messages": [LIGNE_JOURNAL],
            "suivant": "MjAyNi0xMS0wOFQwOToxNTow",
            "a_suivre": True,
        },
        erreurs=("RECHERCHE_REFUSEE",),
        requete_exemple="?debut=2026-11-01&fin=2026-11-08&limite=50",
    ),
    Fiche(
        nom="api-message",
        methode="GET",
        resume="Statut d'un message",
        description="Le statut d'un message, son coût, ses instants et son historique daté.",
        parametres=(
            Parametre("message_id", "path", "Identifiant rendu à l'envoi.", obligatoire=True),
        ),
        reponse={
            **LIGNE_JOURNAL,
            "segments": 1,
            "encodage": "GSM",
            "soumis_le": "2026-11-08T09:15:03.120+00:00",
            "accuse_le": "2026-11-08T09:15:07.954+00:00",
            "diagnostic": None,
            "historique": [
                {"etat": "RECU", "horodatage": "2026-11-08T09:15:02.481+00:00"},
                {"etat": "SOUMIS", "horodatage": "2026-11-08T09:15:03.120+00:00"},
                {"etat": "LIVRE", "horodatage": "2026-11-08T09:15:07.954+00:00"},
            ],
        },
        erreurs=("MESSAGE_INCONNU",),
        chemin_exemple={"message_id": MESSAGE_EXEMPLE},
    ),
    Fiche(
        nom="api-solde",
        methode="GET",
        resume="Solde du compte",
        description="Acquis, réservé et disponible, en FCFA entiers.",
        reponse={"acquis": 150000, "reserve": 2400, "disponible": 147600, "devise": "XOF"},
    ),
    Fiche(
        nom="api-expediteurs",
        methode="GET",
        resume="Identifiants d'expéditeur",
        description=(
            "Chaque identifiant, son état et son état sur chaque réseau (`null` : jamais "
            "soumis sur ce réseau)."
        ),
        reponse={
            "expediteurs": [
                {
                    "libelle": "BOUTIQUE",
                    "etat": "ACTIF",
                    "par_defaut": True,
                    "reseaux": {
                        "ORANGE": {"etat": "APPROUVE", "expire_le": None},
                        "MOOV": {"etat": "EN_EXAMEN", "expire_le": None},
                        "TELECEL": None,
                    },
                }
            ]
        },
    ),
)

# Erreurs communes à toute route authentifiée.
ERREURS_COMMUNES = ("CLE_INVALIDE", "METHODE_REFUSEE", "QUOTA_CLE_DEPASSE")


def routes_publiques() -> dict[str, str]:
    """Nom de route vers chemin OpenAPI, tirés de `apps/api/urls.py`."""
    from . import urls

    return {
        motif.name: "/" + re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", str(motif.pattern))
        for motif in urls.urlpatterns
        if isinstance(motif, URLPattern)
        and str(motif.pattern).startswith("v1/")
        and motif.name != "api-openapi"
    }


def chemin(fiche: Fiche) -> str:
    return routes_publiques()[fiche.nom]


def _url_exemple(fiche: Fiche, base: str) -> str:
    adresse = chemin(fiche)
    for nom, valeur in fiche.chemin_exemple.items():
        adresse = adresse.replace("{" + nom + "}", valeur)
    return f"{base}{adresse}{fiche.requete_exemple}"


def exemples(fiche: Fiche, base: str) -> dict[str, str]:
    """Un appel complet dans chaque langage, tiré de la fiche."""
    url = _url_exemple(fiche, base)
    corps = json.dumps(fiche.corps, ensure_ascii=False, indent=2) if fiche.corps else ""
    idempotence = fiche.methode == "POST"
    curl = [f"curl -X {fiche.methode} '{url}'", f"  -H 'Authorization: Bearer {CLE_EXEMPLE}'"]
    if idempotence:
        curl.append("  -H 'Idempotency-Key: commande-42'")
    if corps:
        en_ligne = json.dumps(fiche.corps, ensure_ascii=False)
        curl += ["  -H 'Content-Type: application/json'", f"  -d '{en_ligne}'"]
    python = [
        "import requests",
        "",
        f"reponse = requests.{fiche.methode.lower()}(",
        f'    "{url}",',
        f'    headers={{"Authorization": "Bearer {CLE_EXEMPLE}"'
        + (', "Idempotency-Key": "commande-42"' if idempotence else "")
        + "},",
    ]
    if corps:
        python.append(f"    json={corps.replace(chr(10), chr(10) + '    ')},")
    python += ["    timeout=30,", ")", "print(reponse.status_code, reponse.json())"]
    entetes_js = [f'"Authorization": "Bearer {CLE_EXEMPLE}"']
    if idempotence:
        entetes_js.append('"Idempotency-Key": "commande-42"')
    if corps:
        entetes_js.append('"Content-Type": "application/json"')
    javascript = [
        f'const reponse = await fetch("{url}", {{',
        f'  method: "{fiche.methode}",',
        "  headers: { " + ", ".join(entetes_js) + " },",
    ]
    if corps:
        javascript.append(f"  body: JSON.stringify({corps.replace(chr(10), chr(10) + '  ')}),")
    javascript += ["});", "console.log(reponse.status, await reponse.json());"]
    entetes_php = [f"'Authorization: Bearer {CLE_EXEMPLE}'"]
    if idempotence:
        entetes_php.append("'Idempotency-Key: commande-42'")
    if corps:
        entetes_php.append("'Content-Type: application/json'")
    php = [
        "<?php",
        f"$ch = curl_init('{url}');",
        "curl_setopt_array($ch, [",
        f"    CURLOPT_CUSTOMREQUEST => '{fiche.methode}',",
        "    CURLOPT_RETURNTRANSFER => true,",
        "    CURLOPT_HTTPHEADER => [" + ", ".join(entetes_php) + "],",
    ]
    if corps:
        php.append(
            "    CURLOPT_POSTFIELDS => json_encode("
            + _php(fiche.corps)
            + ", JSON_UNESCAPED_UNICODE),"
        )
    php += [
        "]);",
        "$corps = json_decode(curl_exec($ch), true);",
        "echo curl_getinfo($ch, CURLINFO_HTTP_CODE), PHP_EOL;",
        "print_r($corps);",
    ]
    return {
        "cURL": " \\\n".join(curl),
        "PHP": "\n".join(php),
        "Python": "\n".join(python),
        "JavaScript": "\n".join(javascript),
    }


def _php(valeur) -> str:
    if isinstance(valeur, dict):
        return "[" + ", ".join(f"'{cle}' => {_php(v)}" for cle, v in valeur.items()) + "]"
    if isinstance(valeur, list):
        return "[" + ", ".join(_php(v) for v in valeur) + "]"
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, int | float):
        return str(valeur)
    return "'" + str(valeur).replace("\\", "\\\\").replace("'", "\\'") + "'"


# Webhooks

EVENEMENTS = dict(PointReception.Evenement.choices)

WEBHOOKS = (
    "Déclarez un point de réception dans l'espace du compte, rubrique Webhooks : une "
    "adresse https publique et les événements voulus. Le secret de signature (whsec_...) "
    "n'est montré qu'une fois. Chaque événement arrive en POST JSON : id (stable : "
    "dédoublonnez sur lui), type, cree_le, donnees. Répondez 2xx en moins de dix "
    "secondes ; toute autre réponse, une redirection ou un silence est réessayé après "
    "une minute, cinq, trente, deux heures et six heures, puis abandonné. Cinq échecs "
    "de suite désactivent le point et préviennent celui qui l'a créé. L'en-tête "
    'Signature porte t=<horodatage>,v1=<HMAC-SHA256 hexadécimal de "<t>.<corps>">, '
    f"avec votre secret ; refusez une signature fausse ou un horodatage de plus de "
    f"{TOLERANCE_S // 60} minutes. Le journal des livraisons est dans le même écran."
)

VERIFICATION_SIGNATURE = {
    "Python": """import hashlib, hmac, time

def signature_valide(secret: str, corps: bytes, entete: str) -> bool:
    champs = dict(m.split("=", 1) for m in entete.split(",") if "=" in m)
    t = int(champs.get("t", "0"))
    if abs(time.time() - t) > 300:
        return False
    attendue = hmac.new(secret.encode(), f"{t}.".encode() + corps, hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendue, champs.get("v1", ""))""",
    "PHP": """<?php
function signature_valide(string $secret, string $corps, string $entete): bool {
    parse_str(str_replace(',', '&', $entete), $champs);
    $t = (int) ($champs['t'] ?? 0);
    if (abs(time() - $t) > 300) { return false; }
    $attendue = hash_hmac('sha256', $t . '.' . $corps, $secret);
    return hash_equals($attendue, $champs['v1'] ?? '');
}""",
    "JavaScript": """import crypto from "node:crypto";

function signatureValide(secret, corps, entete) {
  const champs = Object.fromEntries(entete.split(",").map((m) => m.split("=", 2)));
  const t = Number(champs.t || 0);
  if (Math.abs(Date.now() / 1000 - t) > 300) return false;
  const attendue = crypto.createHmac("sha256", secret).update(`${t}.${corps}`).digest("hex");
  const recue = String(champs.v1 || "");
  return recue.length === attendue.length &&
    crypto.timingSafeEqual(Buffer.from(attendue), Buffer.from(recue));
}""",
    "cURL": (
        "# Calcul de contrôle en ligne de commande, corps lu tel que reçu :\n"
        'printf \'%s.%s\' "$T" "$(cat corps.json)" | openssl dgst -sha256 -hmac "$SECRET"'
    ),
}


# Schéma OpenAPI


def _schema_valeur(valeur) -> dict:
    if isinstance(valeur, bool):
        return {"type": "boolean"}
    if isinstance(valeur, int):
        return {"type": "integer"}
    if isinstance(valeur, float):
        return {"type": "number"}
    if valeur is None:
        return {}
    if isinstance(valeur, list):
        return {"type": "array", "items": _schema_valeur(valeur[0]) if valeur else {}}
    if isinstance(valeur, dict):
        return {"type": "object", "properties": {c: _schema_valeur(v) for c, v in valeur.items()}}
    return {"type": "string"}


def openapi(base: str = "") -> dict:
    chemins: dict = {}
    for fiche in FICHES:
        operation = {
            "operationId": f"{fiche.methode.lower()}_{fiche.nom.replace('-', '_')}",
            "summary": fiche.resume,
            "description": fiche.description,
            "parameters": [
                {
                    "name": p.nom,
                    "in": p.ou,
                    "required": p.obligatoire or p.ou == "path",
                    "description": p.description,
                    "schema": {"type": p.type},
                }
                for p in fiche.parametres
            ],
            "responses": {
                str(fiche.statut): {
                    "description": "Succès",
                    "content": {
                        "application/json": {
                            "schema": _schema_valeur(fiche.reponse),
                            "example": fiche.reponse,
                        }
                    },
                }
            },
            "x-exemples": exemples(fiche, base),
        }
        if fiche.corps:
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": _schema_valeur(fiche.corps),
                        "example": fiche.corps,
                    }
                },
            }
        for code in (*fiche.erreurs, *ERREURS_COMMUNES):
            statut, description = ERREURS[code]
            entree = operation["responses"].setdefault(
                str(statut),
                {
                    "description": "",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Erreur"}}
                    },
                },
            )
            entree["description"] = (entree["description"] + f" {code} : {description}").strip()
        chemins.setdefault(chemin(fiche), {})[fiche.methode.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {
            "title": TITRE,
            "version": VERSION,
            "description": "\n\n".join(
                (*REGLES_INTEGRATEUR, AUTHENTIFICATION, BAC_A_SABLE, QUOTAS, IDEMPOTENCE)
            ),
        },
        "servers": [{"url": base or "/"}],
        "security": [{"cle": []}],
        "paths": chemins,
        "components": {
            "securitySchemes": {"cle": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "Erreur": {
                    "type": "object",
                    "properties": {
                        "erreur": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string", "enum": sorted(ERREURS)},
                                "message": {"type": "string"},
                                "requete_id": {"type": "string"},
                            },
                        }
                    },
                },
                "Refus": {"type": "string", "enum": sorted(REFUS)},
                "Etat": {"type": "string", "enum": list(ETATS)},
            },
        },
        "webhooks": {
            nom: {
                "post": {
                    "summary": libelle,
                    "description": WEBHOOKS,
                    "responses": {"200": {"description": "Reçu"}},
                }
            }
            for nom, libelle in EVENEMENTS.items()
        },
    }
