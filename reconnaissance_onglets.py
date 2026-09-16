"""Releve la structure reelle des barres d'onglets des pages de module.

Pourquoi ce script : extracteur/ena.py ne clique aujourd'hui qu'un seul nom
d'onglet, « Contenu du module », releve en phase 0 sur un cours a deux
onglets. Un cours monte autrement -- « Presentation / Theorie / Travaux
pratiques » -- voit son clic echouer en silence, et seul l'onglet affiche par
defaut est archive. Les fichiers des autres onglets sont perdus sans qu'aucun
echec ne soit consigne.

Pour corriger il faut savoir a quoi ressemble vraiment la barre d'onglets.
Ce script ne telecharge rien et n'ecrit rien dans l'archive.

    python reconnaissance_onglets.py                 -> liste vos cours
    python reconnaissance_onglets.py 100002          -> sonde ce cours
    python reconnaissance_onglets.py 100002 > out.txt

Aucune navigation manuelle n'est demandee : le script construit lui-meme les
URL des pages de module et les visite. La premiere version de ce script
laissait l'utilisateur naviguer a la main, et lisait la mauvaise page --
monPortail ouvre le site de cours dans un NOUVEL onglet, que session.page ne
suit pas.
"""

import json
import sys

from extracteur.auth import SessionNavigateur
from extracteur.ena import Ena

# Nombre de modules sondes par cours. Trois suffisent a voir si la barre
# d'onglets varie d'un module a l'autre, sans faire durer la reconnaissance.
MODULES_SONDES = 3

# Extrait tout ce qui permettrait de reconstruire un selecteur d'enumeration :
# les candidats onglets, leur parent commun, et le HTML de ce parent.
SONDE = r"""
() => {
  const texte = (el) => (el.innerText || el.textContent || '').trim();
  const bref = (v, n) => (v === null || v === undefined) ? null : v.toString().slice(0, n);

  // 1. Tout ce qui ressemble a un onglet, par role ou par classe.
  const parRole = Array.from(document.querySelectorAll('[role=tab], [role=tablist]'));

  // 2. Les elements cliquables courts, alignes horizontalement, typiques
  //    d'une barre d'onglets : on les laisse parler plutot que de supposer
  //    une classe precise.
  const cliquables = Array.from(document.querySelectorAll('a, button, li, span'))
    .filter(el => {
      const t = texte(el);
      return t.length > 0 && t.length < 40 && el.children.length <= 1;
    });

  // 3. Regroupement par parent : une barre d'onglets, c'est plusieurs freres
  //    cliquables sous un meme parent.
  const parParent = new Map();
  for (const el of cliquables) {
    const p = el.parentElement;
    if (!p) continue;
    if (!parParent.has(p)) parParent.set(p, []);
    parParent.get(p).push(el);
  }

  const groupes = [];
  for (const [parent, enfants] of parParent.entries()) {
    if (enfants.length < 2 || enfants.length > 12) continue;
    const textes = enfants.map(texte).filter(t => t);
    if (textes.length < 2) continue;
    groupes.push({
      parent_balise: parent.tagName.toLowerCase(),
      parent_classe: bref(parent.className, 160),
      parent_id: parent.id || null,
      parent_role: parent.getAttribute('role'),
      enfants: enfants.map(el => ({
        balise: el.tagName.toLowerCase(),
        texte: texte(el).slice(0, 60),
        classe: bref(el.className, 120),
        id: el.id || null,
        role: el.getAttribute('role'),
        href: el.getAttribute('href'),
        aria_selected: el.getAttribute('aria-selected'),
        onclick_present: el.hasAttribute('onclick'),
      })),
      parent_html: bref(parent.outerHTML, 1800),
    });
  }

  return {
    titre_page: document.title,
    par_role: parRole.map(el => ({
      balise: el.tagName.toLowerCase(),
      texte: texte(el).slice(0, 80),
      classe: bref(el.className, 120),
      role: el.getAttribute('role'),
      aria_selected: el.getAttribute('aria-selected'),
    })),
    liens_diese: Array.from(document.querySelectorAll("a[href='#']"))
      .slice(0, 20)
      .map(el => ({ texte: texte(el).slice(0, 60), classe: bref(el.className, 120), id: el.id || null })),
    groupes_de_freres: groupes.slice(0, 10),
    nombre_liens_fichier: document.querySelectorAll("a[href*='/analytique/evenement/fichier']").length,
  };
}
"""


def _lister_les_cours(ena) -> int:
    print("\nVos cours, par session :\n")
    for session_cours in ena.sessions_disponibles():
        print(f"  {session_cours.libelle}")
        for cours in ena.sites_de_session(session_cours):
            print(f"     idSite={cours.id_site:<10} {cours.sigle or ''} {cours.titre}")
    print("\nRelancez avec l'idSite du cours a sonder, par exemple :")
    print("  python reconnaissance_onglets.py 100002 > reconnaissance.txt 2>&1")
    return 0


def _sonder_le_cours(ena, session, id_site: str) -> int:
    cours = None
    for session_cours in ena.sessions_disponibles():
        for candidat in ena.sites_de_session(session_cours):
            if candidat.id_site == id_site:
                cours = candidat
                break
        if cours is not None:
            break

    if cours is None:
        print(f"ECHEC : aucun cours ne correspond a idSite={id_site}.", file=sys.stderr)
        return 2

    print(f"\nCours : {cours.sigle or ''} {cours.titre}  (idSite={cours.id_site})")

    modules = ena.modules(cours)
    print(f"{len(modules)} module(s) trouve(s).")
    if not modules:
        print("Aucun module : ce cours passe par le repli du menu, pas par les modules.")
        return 0

    for module in modules[:MODULES_SONDES]:
        from extracteur.ena import URL

        url = URL.module(module.id_site, module.id_module)
        print("\n" + "=" * 72)
        print(f"MODULE : {module.titre}")
        print(f"URL    : {url}")
        print("=" * 72)
        try:
            ena._visiter(url)
            session.page.wait_for_timeout(1500)
            releve = session.page.evaluate(SONDE)
        except Exception as erreur:
            print(f"(echec de la sonde : {erreur})")
            continue
        print(json.dumps(releve, indent=2, ensure_ascii=False))

    return 0


def main() -> int:
    id_site = sys.argv[1].strip() if len(sys.argv) > 1 else None

    session = SessionNavigateur()
    session.ouvrir()
    print("Connectez-vous dans la fenetre du navigateur qui vient de s'ouvrir.")
    print("NE FERMEZ PAS cette fenetre : le script s'en sert ensuite tout seul.")
    if not session.attendre_connexion():
        print("ECHEC : connexion non detectee.", file=sys.stderr)
        session.fermer()
        return 1

    print("\nConnexion detectee. Le script navigue maintenant tout seul.\n")
    try:
        ena = Ena(session)
        if id_site is None:
            return _lister_les_cours(ena)
        code = _sonder_le_cours(ena, session, id_site)
        print("\n" + "=" * 72)
        print("Collez TOUTE cette sortie dans la conversation.")
        print("=" * 72)
        return code
    finally:
        session.fermer()


if __name__ == "__main__":
    raise SystemExit(main())
