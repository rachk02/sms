/*
 * Éditeur de message : compteur en direct, caractères coûteux surlignés,
 * conversion en un clic (T-57). Aucun aller-retour au serveur.
 *
 * Les balises comptent pour leur valeur de repli : le compteur de la page
 * donne un ordre de grandeur, l'aperçu de la campagne compte le destinataire
 * le plus long.
 */
document.addEventListener("alpine:init", function () {
  "use strict";

  var BALISE = /\{\s*([a-z0-9_]+)\s*(?:\|([^{}]*))?\}/g;

  function echapper(c) {
    return c.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  window.Alpine.data("compositeur", function (idTable, idReplis, idTexte) {
    var table = JSON.parse(document.getElementById(idTable).textContent);
    var replis = JSON.parse(document.getElementById(idReplis).textContent);
    var moteur = window.EncodageSms.creer(table);

    return {
      texte: JSON.parse(document.getElementById(idTexte).textContent),

      get rendu() {
        return this.texte.replace(BALISE, function (tout, nom, repli) {
          return (repli && repli.trim()) || replis[nom] || "";
        });
      },

      get analyse() {
        return moteur.analyser(this.rendu);
      },

      get convertibles() {
        return this.analyse.hors_gsm.filter(moteur.convertible);
      },

      get surligne() {
        return Array.from(this.texte).map(function (c) {
          return moteur.horsGsm(c) ? "<mark>" + echapper(c) + "</mark>" : echapper(c);
        }).join("");
      },

      convertir: function () {
        this.texte = moteur.convertir(this.texte);
      },
    };
  });
});
