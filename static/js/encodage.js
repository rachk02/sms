/*
 * Compteur de segments et conversion GSM, dans la page (T-57).
 *
 * Même calcul que apps/core/encodage.py, sur les tables qu'il fournit
 * (table_pour_le_navigateur) : le compteur qui suit la saisie ne peut pas
 * annoncer autre chose que ce qui sera facturé. Un test compare les deux.
 */
(function (racine) {
  "use strict";

  function creer(table) {
    var gsm = new Set(Array.from(table.gsm));
    var extension = new Set(Array.from(table.extension));

    function coutGsm(c) {
      return extension.has(c) ? 2 : 1;
    }

    function coutUcs2(c) {
      return c.codePointAt(0) > 0xffff ? 2 : 1;
    }

    function segments(couts, simple, parSegment) {
      var total = couts.reduce(function (a, b) { return a + b; }, 0);
      if (total <= simple) {
        return 1;
      }
      var nombre = 1;
      var rempli = 0;
      couts.forEach(function (cout) {
        if (rempli + cout > parSegment) {
          nombre += 1;
          rempli = 0;
        }
        rempli += cout;
      });
      return nombre;
    }

    function horsGsm(c) {
      return !gsm.has(c) && !extension.has(c);
    }

    function analyser(texte) {
      var caracteres = Array.from(texte);
      var hors = Array.from(new Set(caracteres.filter(horsGsm))).sort();
      var couts;
      if (hors.length === 0) {
        couts = caracteres.map(coutGsm);
        return {
          encodage: "GSM7",
          unites: couts.reduce(function (a, b) { return a + b; }, 0),
          segments: segments(couts, table.gsm_simple, table.gsm_segment),
          capacite: table.gsm_simple,
          hors_gsm: hors,
        };
      }
      couts = caracteres.map(coutUcs2);
      return {
        encodage: "UCS2",
        unites: couts.reduce(function (a, b) { return a + b; }, 0),
        segments: segments(couts, table.ucs2_simple, table.ucs2_segment),
        capacite: table.ucs2_simple,
        hors_gsm: hors,
      };
    }

    function convertir(texte) {
      return Array.from(texte).map(function (c) {
        return Object.prototype.hasOwnProperty.call(table.conversions, c) ? table.conversions[c] : c;
      }).join("");
    }

    function convertible(c) {
      return Object.prototype.hasOwnProperty.call(table.conversions, c);
    }

    return { analyser: analyser, convertir: convertir, convertible: convertible, horsGsm: horsGsm };
  }

  racine.EncodageSms = { creer: creer };
})(typeof globalThis !== "undefined" ? globalThis : this);
