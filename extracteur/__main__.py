"""Points d'entree de l'extracteur.

  python -m extracteur                          -> fenetre graphique
  python -m extracteur --lister                 -> enumeration en console
  python -m extracteur --un-seul-cours 181216   -> archivage d'un seul cours, en console

Aucun de ces modes ne fabrique de Cours a la main : url_plan_de_cours et
url_resultats ne sont renseignes que par l'enumeration (Ena.sites_de_session),
a partir de la page qui liste les cours d'une session. Un Cours construit de
toutes pieces aurait ces champs vides, et l'archiveur retomberait sur le filet
de secours (impression de page) au lieu du PDF officiel de l'Universite.
"""

import argparse
import queue
import sys
import threading
import traceback
from pathlib import Path

from extracteur.archiveur import Archiveur
from extracteur.auth import SessionNavigateur
from extracteur.ena import Ena
from extracteur.manifeste import ecrire_rapport
from extracteur.modele import Resultat
from extracteur.telechargement import SessionExpiree

DOSSIER_PROFIL = Path(".session")
NOM_RAPPORT = "_rapport.html"


class CoursIntrouvable(Exception):
    """Aucun cours ne correspond a l'identifiant de site demande."""

    def __init__(self, id_site: str):
        super().__init__(f"aucun cours ne correspond a idSite={id_site}")
        self.id_site = id_site


class ConnexionEchouee(Exception):
    """La connexion n'a pas ete detectee dans le delai imparti."""


def _connecter(sans_fenetre: bool = False) -> SessionNavigateur:
    session = SessionNavigateur(DOSSIER_PROFIL, sans_fenetre=sans_fenetre)
    session.ouvrir()
    print("Connectez-vous dans la fenetre du navigateur...")
    if not session.attendre_connexion():
        session.fermer()
        raise ConnexionEchouee("connexion non detectee dans le delai imparti")
    print("Connexion detectee.")
    return session


def _chercher_cours(ena, id_site: str) -> "Cours | None":
    """Retrouve, par enumeration, le Cours dont l'identifiant de site correspond.

    Ne fabrique jamais de Cours a la main : url_plan_de_cours et url_resultats
    ne sont renseignes que par cette enumeration (source /portail/cours). Un
    Cours construit de toutes pieces aurait ces champs vides, et l'archiveur
    retomberait sur le filet de secours (impression de page) au lieu du PDF
    officiel de l'Universite.
    """
    for session in ena.sessions_disponibles():
        for cours in ena.sites_de_session(session):
            if cours.id_site == id_site:
                return cours
    return None


def _drainer(evenements: "queue.Queue", imprimer=print) -> None:
    """Vide la file d'evenements deja postes, sans bloquer.

    Appelee pendant le travail (pas seulement a la fin) : un archivage de
    plusieurs dizaines de cours doit afficher sa progression au fil de l'eau,
    pas rester muet jusqu'a la fin.
    """
    while not evenements.empty():
        type_evenement, texte = evenements.get()
        imprimer(f"  [{type_evenement}] {texte}")


def _ecrire_rapport_final(destination: Path, resultat: Resultat) -> Path:
    """Le compte rendu final : doit exister meme apres une session expiree ou
    une interruption, pour dire ce qu'il reste a recuperer a la main."""
    chemin = destination / NOM_RAPPORT
    ecrire_rapport(
        chemin,
        resultat.echecs,
        {"fichiers ecrits": resultat.fichiers_ecrits, "fichiers sautes": resultat.fichiers_sautes},
    )
    return chemin


def _code_de_sortie(resultat: Resultat) -> int:
    return 0 if resultat.fichiers_ecrits or not resultat.echecs else 1


def _afficher_sessions(ena, imprimer=print) -> None:
    """Enumere sessions et cours sans rien telecharger ni rien ecrire sur
    disque : la sonde la moins risquee pour valider l'enumeration contre la
    vraie plateforme."""
    sessions = ena.sessions_disponibles()
    if not sessions:
        imprimer("Aucune session trouvee.")
        return

    for session in sessions:
        imprimer(f"\n=== {session.libelle} ({session.code}) ===")
        cours_de_la_session = ena.sites_de_session(session)
        if not cours_de_la_session:
            imprimer("  (aucun cours)")
            continue
        for cours in cours_de_la_session:
            sigle = cours.sigle or "(sans sigle)"
            plan = "oui" if cours.url_plan_de_cours else "non"
            resultats = "oui" if cours.url_resultats else "non"
            imprimer(
                f"  [{cours.id_site}] {sigle} - {cours.titre}"
                f"  (plan de cours officiel : {plan}, sommaire de resultats : {resultats})"
            )


def _lister() -> int:
    session = None
    try:
        session = _connecter()
        _afficher_sessions(Ena(session))
        return 0
    except ConnexionEchouee as erreur:
        print(f"ECHEC : {erreur}", file=sys.stderr)
        return 1
    except SessionExpiree:
        print(
            "\nSession expiree pendant l'enumeration. Reconnectez-vous puis "
            "relancez --lister.",
            file=sys.stderr,
        )
        return 3
    finally:
        # Encadre par try/finally : meme si l'utilisateur ferme la fenetre ou
        # interrompt au clavier, le navigateur doit se fermer. Un processus
        # Playwright orphelin est une nuisance concrete sur Windows.
        if session is not None:
            session.fermer()


def _un_seul_cours(id_site: str, destination: Path, session=None, fabrique_ena=Ena) -> int:
    """Archive un seul cours, en console, sans interface graphique.

    `session` et `fabrique_ena` sont injectables pour les tests ; en usage
    normal, une vraie SessionNavigateur et Ena sont utilisees.

    La connexion et l'archivage tournent dans le meme fil : Playwright (API
    synchrone) exige que tous ses appels viennent du fil qui l'a demarre. Le
    fil principal se contente de vider la file d'evenements pendant que ce
    fil de travail avance, pour afficher la progression au fil de l'eau au
    lieu de rester muet jusqu'a la fin.
    """
    if session is None:
        session = SessionNavigateur(DOSSIER_PROFIL)

    evenements: "queue.Queue" = queue.Queue()
    contexte: dict = {}

    def travailler() -> None:
        try:
            session.ouvrir()
            print("Connectez-vous dans la fenetre du navigateur...")
            if not session.attendre_connexion():
                contexte["connexion_echouee"] = True
                return
            print("Connexion detectee.")

            ena = fabrique_ena(session)
            cours = _chercher_cours(ena, id_site)
            if cours is None:
                contexte["erreur"] = CoursIntrouvable(id_site)
                return

            archiveur = Archiveur(ena, session.transport, destination, evenements)
            # Stocke la reference AVANT d'archiver : si SessionExpiree est
            # levee pendant archiver(), le resultat partiel (echecs deja
            # consignes) doit rester accessible pour le rapport final.
            contexte["archiveur"] = archiveur
            contexte["resultat"] = archiveur.archiver([cours])
        except SessionExpiree as erreur:
            contexte["erreur"] = erreur
        # Isolation stricte : une exception dans ce fil ne doit jamais mourir
        # en silence sans que l'appelant le sache. Elle est tracee ici, puis
        # traduite en code de sortie une fois le fil rejoint.
        except Exception as erreur:
            traceback.print_exc()
            contexte["erreur"] = erreur
        finally:
            session.fermer()

    fil = threading.Thread(target=travailler, daemon=False)
    fil.start()
    try:
        while fil.is_alive():
            _drainer(evenements)
            fil.join(timeout=0.2)
    except KeyboardInterrupt:
        # On attend la fin du fil plutot que de couper court : c'est le
        # finally de travailler() qui ferme le navigateur, et un processus
        # Playwright orphelin est une nuisance concrete sur Windows.
        print(
            "\nInterruption demandee : fermeture du navigateur en cours, patientez...",
            file=sys.stderr,
        )
        fil.join()
        _drainer(evenements)
        archiveur = contexte.get("archiveur")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 130

    _drainer(evenements)

    if contexte.get("connexion_echouee"):
        print("ECHEC : connexion non detectee.", file=sys.stderr)
        return 1

    erreur = contexte.get("erreur")
    archiveur = contexte.get("archiveur")

    if isinstance(erreur, CoursIntrouvable):
        print(f"ECHEC : aucun cours ne correspond a idSite={id_site}.", file=sys.stderr)
        return 2

    if isinstance(erreur, SessionExpiree):
        print(
            "\nSession expiree pendant l'archivage. Reconnectez-vous puis "
            "relancez la meme commande : la reprise est idempotente, elle "
            "continue la ou elle s'est arretee.",
            file=sys.stderr,
        )
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 3

    if erreur is not None:
        print(f"ECHEC inattendu : {erreur}", file=sys.stderr)
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

    resultat = contexte["resultat"]
    chemin_rapport = _ecrire_rapport_final(destination, resultat)
    print(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    print(f"Echecs : {len(resultat.echecs)}  -> {chemin_rapport}")
    return _code_de_sortie(resultat)


def main() -> int:
    try:
        # Garde-fou d'affichage : une console Windows dont l'encodage n'est pas
        # UTF-8 ne doit jamais planter sur un titre de cours accentue. Les
        # caracteres non representables sont remplaces plutot que de lever
        # UnicodeEncodeError.
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        # Flux deja ferme ou remplace (ex. redirection en tests) : tant pis,
        # ce n'est qu'un garde-fou d'affichage.
        pass

    analyseur = argparse.ArgumentParser(prog="extracteur")
    analyseur.add_argument(
        "--lister", action="store_true", help="enumere sessions et cours, sans rien telecharger"
    )
    analyseur.add_argument("--un-seul-cours", dest="id_site", help="idSite a archiver, en console")
    analyseur.add_argument(
        "--destination", type=Path, default=Path("Archive monPortail"), help="dossier de sortie"
    )
    arguments = analyseur.parse_args()

    try:
        if arguments.lister:
            return _lister()

        if arguments.id_site:
            return _un_seul_cours(arguments.id_site, arguments.destination)
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        return 130

    try:
        from extracteur.ui import lancer
    except ModuleNotFoundError as erreur:
        if erreur.name != "extracteur.ui":
            raise
        print(
            "Interface graphique non disponible : extracteur/ui.py n'existe pas "
            "encore. Utilisez --lister ou --un-seul-cours <idSite> en attendant.",
            file=sys.stderr,
        )
        return 1

    lancer(arguments.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
