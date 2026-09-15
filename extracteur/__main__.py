"""Points d'entree de l'extracteur.

  python -m extracteur                          -> fenetre graphique
  python -m extracteur --lister                 -> enumeration en console
  python -m extracteur --un-seul-cours 181216   -> archivage d'un seul cours, en console
  python -m extracteur --diagnostic             -> etat des lieux du DOM du selecteur de sessions

--lister, --un-seul-cours et --diagnostic sont mutuellement exclusifs : les
combiner est rejete par argparse plutot que de laisser l'un l'emporter en
silence.

--diagnostic ne telecharge rien et n'ecrit rien : il sert uniquement, quand
--lister ne trouve aucune session, a voir sur des faits ce que le DOM reel
contient plutot que de deviner une nouvelle correction.

Aucun de ces modes ne fabrique de Cours a la main : url_plan_de_cours et
url_resultats ne sont renseignes que par l'enumeration (Ena.sites_de_session),
a partir de la page qui liste les cours d'une session. Un Cours construit de
toutes pieces aurait ces champs vides, et l'archiveur retomberait sur le filet
de secours (impression de page) au lieu du PDF officiel de l'Universite.

Codes de sortie (contrat pour un appelant qui ne lirait que le code, pas la
console) :
  0    succes complet : rien a signaler.
  1    echec sans rien ecrire (connexion non detectee, selecteur de sessions
       illisible, erreur inattendue, ou --lister/--un-seul-cours n'a rien
       produit et a consigne des echecs).
  2    --un-seul-cours : aucun cours ne correspond a l'idSite demande.
  3    session expiree en cours de route ; relancer la meme commande, la
       reprise est idempotente.
  4    --un-seul-cours : archivage partiel, au moins un fichier ecrit mais
       aussi des echecs consignes dans le rapport (voir _rapport.html).
  130  interruption au clavier (Ctrl+C).
"""

import argparse
import queue
import sys
import threading
import traceback
from pathlib import Path

from extracteur.archiveur import Archiveur
from extracteur.auth import SessionNavigateur
from extracteur.ena import Ena, SelecteurSessionsIllisible
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


def _connecter(
    sans_fenetre: bool = False, session: SessionNavigateur | None = None
) -> SessionNavigateur:
    """Ouvre le navigateur et attend la connexion manuelle, MFA compris.

    `session` est injectable pour les tests (doublure) ; en usage normal, une
    vraie SessionNavigateur est creee et ouverte ici.

    Responsable de fermer ce qu'elle a ouvert si quoi que ce soit echoue en
    cours de route -- interruption clavier comprise. KeyboardInterrupt n'est
    pas une sous-classe d'Exception, d'ou l'interception large sur
    BaseException. On ferme puis on relance : on nettoie, on n'avale jamais
    l'exception.
    """
    if session is None:
        session = SessionNavigateur(DOSSIER_PROFIL, sans_fenetre=sans_fenetre)
    try:
        session.ouvrir()
        print("Connectez-vous dans la fenetre du navigateur...")
        if not session.attendre_connexion():
            raise ConnexionEchouee("connexion non detectee dans le delai imparti")
        print("Connexion detectee.")
        return session
    except BaseException:
        session.fermer()
        raise


def _chercher_cours(ena, id_site: str) -> "Cours | None":
    """Retrouve, par enumeration, le Cours dont l'identifiant de site correspond.

    Ne fabrique jamais de Cours a la main : url_plan_de_cours et url_resultats
    ne sont renseignes que par cette enumeration (source /portail/cours). Un
    Cours construit de toutes pieces aurait ces champs vides, et l'archiveur
    retomberait sur le filet de secours (impression de page) au lieu du PDF
    officiel de l'Universite.
    """
    # Normalisation : l'utilisateur copie-colle cette valeur depuis --lister
    # ou depuis une URL, un espace ou un retour de ligne parasite ne doit pas
    # faire echouer une comparaison sinon exacte.
    id_site = id_site.strip()
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
    """0 seulement si aucun echec n'a ete consigne : un cours ancien peut
    produire des fichiers ET des echecs partiels, ce n'est pas un succes
    complet. Voir la docstring du module pour le contrat des codes."""
    if not resultat.echecs:
        return 0
    return 4 if resultat.fichiers_ecrits else 1


def _afficher_sessions(ena, imprimer=print) -> None:
    """Enumere sessions et cours sans rien telecharger ni rien ecrire sur
    disque : la sonde la moins risquee pour valider l'enumeration contre la
    vraie plateforme.

    Le nombre de sessions trouvees est annonce en tete, avant la liste
    elle-meme : c'est la seule protection reelle contre un panneau qui
    plafonne durablement sur un compte incomplet (voir la docstring de
    Ena._stabiliser_options). Aucun code ne peut deviner qu'il manque des
    sessions a un palier atteint trop tot ; l'utilisateur, qui connait son
    propre parcours, le peut d'un coup d'oeil.
    """
    sessions = ena.sessions_disponibles()
    if not sessions:
        imprimer("Aucune session trouvee.")
        return

    imprimer(f"{len(sessions)} session(s) detectee(s).")
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


def _lister(session=None, fabrique_ena=Ena) -> int:
    """Enumere sessions et cours, en console, sans navigateur graphique.

    `session` et `fabrique_ena` sont injectables pour les tests (doublure) ;
    en usage normal, une vraie SessionNavigateur et Ena sont utilisees.

    La fermeture du navigateur est garantie par _connecter elle-meme (voir sa
    docstring) : si l'attente de connexion est interrompue au clavier avant
    que `session_ouverte` soit affecte, ce `finally` ne referme rien, mais
    rien ne reste ouvert non plus, puisque _connecter a deja ferme avant de
    relancer.
    """
    session_ouverte = None
    try:
        session_ouverte = _connecter(session=session)
        _afficher_sessions(fabrique_ena(session_ouverte))
        return 0
    except ConnexionEchouee as erreur:
        print(f"ECHEC : {erreur}", file=sys.stderr)
        return 1
    except SelecteurSessionsIllisible as erreur:
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
        if session_ouverte is not None:
            session_ouverte.fermer()


def _diagnostic(session=None, fabrique_ena=Ena, imprimer=print) -> int:
    """Etat des lieux du DOM du selecteur de sessions, en console.

    Ne telecharge rien, n'ecrit rien sur disque : sert uniquement, quand
    --lister ne trouve aucune session, a trancher sur des faits plutot que de
    deviner une nouvelle correction. `session` et `fabrique_ena` sont
    injectables pour les tests, comme _lister.
    """
    session_ouverte = None
    try:
        session_ouverte = _connecter(session=session)
        rapport = fabrique_ena(session_ouverte).diagnostiquer_sessions()

        for selecteur, nombre, echantillon in rapport["candidats"]:
            imprimer(f"{selecteur} : {nombre} element(s)")
            for texte in echantillon:
                imprimer(f"    - {texte!r}")

        imprimer(f"liens portant idSite= dans la page : {rapport['liens_id_site']}")
        return 0
    except ConnexionEchouee as erreur:
        print(f"ECHEC : {erreur}", file=sys.stderr)
        return 1
    except SessionExpiree:
        print("\nSession expiree pendant le diagnostic.", file=sys.stderr)
        return 3
    finally:
        if session_ouverte is not None:
            session_ouverte.fermer()


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
        except SelecteurSessionsIllisible as erreur:
            # Deja un message explicite en soi : jamais de trace brute pour
            # cette exception dediee, contrairement au repli generique
            # ci-dessous.
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

    if isinstance(erreur, SelecteurSessionsIllisible):
        print(f"ECHEC : {erreur}", file=sys.stderr)
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

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


def _construire_analyseur() -> argparse.ArgumentParser:
    analyseur = argparse.ArgumentParser(prog="extracteur")
    # Mutuellement exclusifs : si plusieurs etaient acceptes ensemble, l'un
    # l'emporterait en silence (voir _un_seul_cours plus bas, jamais
    # atteint). L'utilisateur doit voir une erreur explicite plutot qu'un
    # mode different de celui demande.
    groupe_mode = analyseur.add_mutually_exclusive_group()
    groupe_mode.add_argument(
        "--lister", action="store_true", help="enumere sessions et cours, sans rien telecharger"
    )
    groupe_mode.add_argument(
        "--un-seul-cours", dest="id_site", help="idSite a archiver, en console"
    )
    groupe_mode.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "ouvre le selecteur de sessions et affiche un etat des lieux du "
            "DOM, sans rien telecharger ni rien ecrire"
        ),
    )
    analyseur.add_argument(
        "--destination", type=Path, default=Path("Archive monPortail"), help="dossier de sortie"
    )
    return analyseur


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

    analyseur = _construire_analyseur()
    arguments = analyseur.parse_args()

    try:
        if arguments.lister:
            return _lister()

        if arguments.id_site:
            return _un_seul_cours(arguments.id_site, arguments.destination)

        if arguments.diagnostic:
            return _diagnostic()
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
