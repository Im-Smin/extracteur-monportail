"""Trace le parcours des onglets d'un module, pas a pas, sans rien telecharger.

A quoi ca sert : distinguer une VRAIE boucle sans fin d'un parcours qui
termine mais visite beaucoup trop de pages. Les deux se ressemblent depuis la
console -- on voit repasser les memes noms d'onglets -- mais les correctifs
sont opposes.

    python diagnostic_onglets.py 146001              -> le 1er module du cours
    python diagnostic_onglets.py 146001 1310231      -> ce module precis
    python diagnostic_onglets.py 146001 tous         -> tous les modules

Chaque ligne du trace dit : le numero de page demande, celui reellement servi,
le chemin d'onglets lu dans le DOM, si la page a ete retenue ou sautee, et
combien de pages restent dans la file. Une file qui ne descend jamais signe
une boucle ; une file qui descend lentement signe un parcours trop large.
"""

import sys
import time

from extracteur.auth import SessionNavigateur
from extracteur.ena import URL, Ena
from extracteur.extraction import onglets_depuis_html, onglets_selectionnes_depuis_html

# Plafond du diagnostic, volontairement plus bas que celui de l'extracteur :
# on veut un verdict en quelques minutes, pas une reproduction complete.
PLAFOND = 40


def tracer_un_module(ena, module) -> dict:
    """Refait le parcours en largeur de Ena.pages_du_module, en imprimant
    chaque etape. Volontairement une copie, pas un appel : le but est de voir
    l'interieur, ce qu'un appel ne montrerait pas."""
    print("\n" + "=" * 78)
    print(f"MODULE : {module.titre}  (idModule={module.id_module})")
    print("=" * 78)

    a_visiter: list = [None]
    vus: set = set()
    retenues: list = []
    visites = 0
    debut = time.time()

    while a_visiter:
        if visites >= PLAFOND:
            print(f"\n  >>> PLAFOND DE DIAGNOSTIC ATTEINT ({PLAFOND} visites).")
            print(f"  >>> File encore pleine : {len(a_visiter)} page(s) en attente.")
            print(f"  >>> Pages distinctes retenues : {len(retenues)}")
            break

        id_demande = a_visiter.pop(0)
        visites += 1
        depart = time.time()
        html = ena._visiter(URL.module(module.id_site, module.id_module, id_demande))
        duree = time.time() - depart

        chaine = onglets_selectionnes_depuis_html(html)
        id_reel = chaine[-1].id_page if chaine else None
        chemin = " > ".join(o.titre for o in chaine) or "(aucun onglet)"

        deja = id_reel in vus
        if not deja:
            vus.add(id_reel)
            retenues.append(id_reel)

        tous = onglets_depuis_html(html)
        nouveaux = [o.id_page for o in tous if o.id_page not in vus and o.id_page not in a_visiter]
        if not deja:
            a_visiter.extend(nouveaux)

        print(
            f"  {visites:3}. demande={str(id_demande):>9}  servi={str(id_reel):>9}  "
            f"{'SAUTEE ' if deja else 'RETENUE'}  "
            f"file={len(a_visiter):3}  onglets_vus={len(tous):2}  "
            f"+{len(nouveaux) if not deja else 0:2}  {duree:4.1f}s  {chemin}"
        )

    total = time.time() - debut
    print(f"\n  Bilan : {visites} visite(s), {len(retenues)} page(s) retenue(s), "
          f"file restante {len(a_visiter)}, {total:.0f}s")
    return {"visites": visites, "retenues": len(retenues), "reste": len(a_visiter), "duree": total}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    id_site = sys.argv[1].strip()
    cible = sys.argv[2].strip() if len(sys.argv) > 2 else None

    session = SessionNavigateur()
    session.ouvrir()
    print("Connectez-vous dans la fenetre du navigateur. NE LA FERMEZ PAS.")
    if not session.attendre_connexion():
        print("ECHEC : connexion non detectee.", file=sys.stderr)
        session.fermer()
        return 1
    print("\nConnexion detectee.\n")

    try:
        ena = Ena(session)
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

        print(f"Cours : {cours.sigle or ''} {cours.titre}")
        modules = ena.modules(cours)
        print(f"{len(modules)} module(s).")

        if cible == "tous":
            choisis = modules
        elif cible:
            choisis = [m for m in modules if m.id_module == cible]
            if not choisis:
                print(f"ECHEC : aucun module idModule={cible}.", file=sys.stderr)
                return 2
        else:
            choisis = modules[:1]

        bilans = [tracer_un_module(ena, module) for module in choisis]

        print("\n" + "=" * 78)
        print("VERDICT")
        print("=" * 78)
        bloque = [b for b in bilans if b["reste"] > 0]
        if bloque:
            print(f"{len(bloque)} module(s) ont atteint le plafond avec une file NON VIDE.")
            print("=> la file ne se vide pas : boucle, ou explosion du nombre de pages.")
        else:
            total_v = sum(b["visites"] for b in bilans)
            total_r = sum(b["retenues"] for b in bilans)
            total_d = sum(b["duree"] for b in bilans)
            print(f"Tous les modules ont termine : {total_v} visite(s) pour "
                  f"{total_r} page(s) utile(s), en {total_d:.0f}s.")
            if total_v > total_r * 2:
                print("=> pas de boucle, mais beaucoup plus de visites que de pages utiles.")
        return 0
    finally:
        session.fermer()


if __name__ == "__main__":
    raise SystemExit(main())
