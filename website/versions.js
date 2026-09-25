// Page Télécharger : les versions viennent directement des releases GitHub,
// il n'y a donc rien à mettre à jour sur le site quand on en publie une.

(function () {
  "use strict";

  var DEPOT = "kevin-belanger/CloneGator";
  var liste = document.getElementById("versions");

  function echapper(texte) {
    return String(texte).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function date(iso) {
    return new Date(iso).toLocaleDateString("fr-CA", { year: "numeric", month: "long", day: "numeric" });
  }

  function taille(octets) {
    return (octets / 1024).toLocaleString("fr-CA", { maximumFractionDigits: 0 }) + " Kio";
  }

  // Le strict nécessaire de Markdown pour les notes de version : paragraphes,
  // listes à tirets, blocs de code, `code` et **gras**.
  function notes(texte) {
    var html = "", liste_ouverte = false, code = null;
    function enLigne(t) {
      return echapper(t)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    }
    function fermerListe() {
      if (liste_ouverte) { html += "</ul>"; liste_ouverte = false; }
    }
    var paragraphe = [];
    function fermerParagraphe() {
      if (paragraphe.length) { html += "<p>" + enLigne(paragraphe.join(" ")) + "</p>"; paragraphe = []; }
    }
    (texte || "").replace(/\r/g, "").split("\n").forEach(function (ligne) {
      if (code !== null) {
        if (/^```/.test(ligne)) { html += "<pre><code>" + echapper(code.join("\n")) + "</code></pre>"; code = null; }
        else code.push(ligne);
        return;
      }
      if (/^```/.test(ligne)) { fermerParagraphe(); fermerListe(); code = []; return; }
      var puce = ligne.match(/^\s*[-*]\s+(.*)$/);
      if (puce) {
        fermerParagraphe();
        if (!liste_ouverte) { html += "<ul>"; liste_ouverte = true; }
        html += "<li>" + enLigne(puce[1]) + "</li>";
        return;
      }
      if (!ligne.trim()) { fermerParagraphe(); fermerListe(); return; }
      if (liste_ouverte && /^\s+/.test(ligne)) { html = html.replace(/<\/li>$/, " " + enLigne(ligne.trim()) + "</li>"); return; }
      fermerListe();
      paragraphe.push(ligne.trim());
    });
    if (code !== null) html += "<pre><code>" + echapper(code.join("\n")) + "</code></pre>";
    fermerParagraphe();
    fermerListe();
    return html;
  }

  // Le paquet versionné, pas la copie « clonegator.deb » qui sert à l'adresse courte.
  function paquet(release) {
    var debs = (release.assets || []).filter(function (a) { return /\.deb$/.test(a.name); });
    return debs.filter(function (a) { return a.name !== "clonegator.deb"; })[0] || debs[0];
  }

  function afficher(releases) {
    releases = releases.filter(function (r) { return !r.draft; });
    if (!releases.length) {
      liste.innerHTML = '<li class="doux">Aucune version publiée pour l\'instant.</li>';
      return;
    }

    var derniere = releases[0];
    document.getElementById("derniere-nom").textContent = derniere.name || derniere.tag_name;
    document.getElementById("derniere-date").textContent = "Publiée le " + date(derniere.published_at);

    liste.innerHTML = releases.map(function (r, rang) {
      var fichier = paquet(r);
      return "<li>" +
        "<h3>" + echapper(r.name || r.tag_name) +
        (rang === 0 ? '<span class="pastille">dernière</span>' : "") + "</h3>" +
        '<div class="doux">' + date(r.published_at) +
        (fichier ? ' · <a href="' + echapper(fichier.browser_download_url) + '">' +
          echapper(fichier.name) + "</a> (" + taille(fichier.size) + ")" : "") +
        ' · <a href="' + echapper(r.html_url) + '">sur GitHub</a></div>' +
        '<div class="notes">' + notes(r.body) + "</div>" +
        "</li>";
    }).join("");
  }

  fetch("https://api.github.com/repos/" + DEPOT + "/releases?per_page=50", {
    headers: { Accept: "application/vnd.github+json" }
  })
    .then(function (reponse) {
      if (!reponse.ok) throw new Error("HTTP " + reponse.status);
      return reponse.json();
    })
    .then(afficher)
    .catch(function () {
      liste.innerHTML = '<li class="doux">La liste n\'a pas pu être chargée depuis GitHub. ' +
        'Elle est consultable directement sur ' +
        '<a href="https://github.com/' + DEPOT + '/releases">la page des versions</a>.</li>';
    });
})();
