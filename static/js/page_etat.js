// Page publique d'état du service (T-103).
//
// Servie par un hébergement statique séparé de la plateforme, elle ne lit que
// le fichier etat.json posé à côté d'elle. Si la plateforme ne publie plus, le
// fichier reste : la page affiche le dernier état connu et dit depuis quand il
// n'a pas été rafraîchi. Si le fichier ne se lit plus, l'état déjà affiché
// reste à l'écran.
(function (racine) {
  "use strict";

  // Publié chaque minute : au-delà de trois minutes, l'état est périmé.
  const SEUIL_PERIME_MS = 3 * 60 * 1000;

  function modele(etat, maintenant) {
    const retard = Math.max(0, maintenant - Date.parse(etat.genere_le));
    return {
      perime: retard > SEUIL_PERIME_MS,
      retard_minutes: Math.floor(retard / 60000),
      genere_le: etat.genere_le,
      composants: etat.composants.map((c) => ({
        code: c.code,
        libelle: c.libelle,
        etat: c.etat,
        libelle_etat: c.libelle_etat,
      })),
      en_cours: etat.en_cours || [],
      prevues: etat.prevues || [],
      historique: etat.historique || [],
    };
  }

  function date(iso) {
    return iso ? new Date(iso).toLocaleString("fr-FR", { timeZone: "Africa/Ouagadougou" }) : "";
  }

  function element(doc, balise, texte, classe) {
    const noeud = doc.createElement(balise);
    if (texte !== undefined) noeud.textContent = texte;
    if (classe) noeud.className = classe;
    return noeud;
  }

  function incidents(doc, conteneur, liste, vide) {
    conteneur.replaceChildren();
    if (!liste.length) {
      conteneur.appendChild(element(doc, "p", vide, "discret"));
      return;
    }
    for (const incident of liste) {
      const bloc = element(doc, "article", undefined, "incident");
      bloc.appendChild(element(doc, "h3", `${incident.titre} (${incident.libelle_etat})`));
      bloc.appendChild(
        element(
          doc,
          "p",
          `${incident.composants.join(", ")} ; depuis le ${date(incident.debut_le)}` +
            (incident.fin_le ? `, jusqu'au ${date(incident.fin_le)}` : ""),
          "discret",
        ),
      );
      for (const publication of incident.publications.slice().reverse()) {
        bloc.appendChild(element(doc, "p", `${date(publication.publiee_le)} : ${publication.texte}`));
      }
      conteneur.appendChild(bloc);
    }
  }

  function afficher(doc, m) {
    const bandeau = doc.getElementById("bandeau");
    bandeau.hidden = !m.perime;
    bandeau.textContent = m.perime
      ? `État affiché : le dernier connu, publié il y a ${m.retard_minutes} minutes. ` +
        "La plateforme ne publie plus pour l'instant."
      : "";
    doc.getElementById("genere").textContent = `Mis à jour le ${date(m.genere_le)}.`;
    const liste = doc.getElementById("composants");
    liste.replaceChildren();
    for (const c of m.composants) {
      const ligne = element(doc, "li", undefined, `composant ${c.etat.toLowerCase()}`);
      ligne.appendChild(element(doc, "span", c.libelle));
      ligne.appendChild(element(doc, "strong", c.libelle_etat));
      liste.appendChild(ligne);
    }
    incidents(doc, doc.getElementById("en-cours"), m.en_cours, "Aucun incident en cours.");
    incidents(doc, doc.getElementById("prevues"), m.prevues, "Aucune maintenance prévue.");
    incidents(doc, doc.getElementById("historique"), m.historique, "Aucun incident sur 90 jours.");
  }

  async function charger(doc, fenetre) {
    try {
      const reponse = await fenetre.fetch("etat.json", { cache: "no-store" });
      if (!reponse.ok) throw new Error(String(reponse.status));
      afficher(doc, modele(await reponse.json(), Date.now()));
    } catch (erreur) {
      const bandeau = doc.getElementById("bandeau");
      bandeau.hidden = false;
      bandeau.textContent = "L'état ne se lit plus pour l'instant ; le dernier affiché reste à l'écran.";
    }
  }

  racine.PageEtat = { modele, afficher, charger, SEUIL_PERIME_MS };

  if (typeof document !== "undefined" && typeof window !== "undefined") {
    charger(document, window);
    setInterval(() => charger(document, window), 60 * 1000);
  }
})(globalThis);
