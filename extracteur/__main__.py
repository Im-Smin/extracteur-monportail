"""Points d'entree de l'extracteur.

  python -m extracteur                          -> fenetre graphique
  python -m extracteur --lister                 -> enumeration en console
  python -m extracteur --un-seul-cours 181216   -> archivage d'un seul cours, en console
  python -m extracteur --session "Automne 2022" -> archivage de tous les cours d'une session
  python -m extracteur --tout                   -> archivage de toutes les sessions, de la plus
                                                    ancienne a la plus recente
  python -m extracteur --diagnostic             -> etat des lieux du DOM du selecteur de sessions

--lister, --un-seul-cours, --session, --tout et --diagnostic sont mutuellement
exclusifs : les combiner est rejete par argparse plutot que de laisser l'un
l'emporter en silence.

--session et --tout reutilisent l'archiveur existant sans rien lui ajouter :
ils se contentent de determiner la liste des Session a couvrir (une seule
pour --session, toutes pour --tout, triees de la plus ancienne a la plus
recente), d'enumerer leurs cours, puis de remettre la liste complete a
Archiveur.archiver() en un seul appel -- celui-ci sait deja isoler les echecs
cours par cours, et sa reprise par manifeste ne retelecharge jamais un
fichier deja present sur disque. --session accepte le libelle avec une
tolerance raisonnable sur la casse, les espaces de tete et de fin, et les
accents, puisqu'il sera tape a la main ; si aucune session ne correspond, les
libelles disponibles sont affiches plutot que de deviner.

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
       illisible, erreur inattendue, --session dont le libelle ne correspond
       a aucune session, ou --lister/--un-seul-cours/--session/--tout n'a
       rien produit et a consigne des echecs).
  2    --un-seul-cours : aucun cours ne correspond a l'idSite demande.
  3    session expiree en cours de route ; relancer la meme commande, la
       reprise est idempotente (--session et --tout reprennent aussi la ou
       ils se sont arretes, sans retelecharger ce qui est deja sur disque).
  4    --un-seul-cours, --session ou --tout : archivage partiel, au moins un
       fichier ecrit mais aussi des echecs consignes dans le rapport (voir
       _rapport.html).
  130  interruption au clavier (Ctrl+C).
"""

import argparse
import queue
import sys
import threading
import traceback
import unicodedata
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


class SessionIntrouvable(Exception):
    """Aucune session offerte par le selecteur ne correspond au libelle
    demande par --session.

    Porte les sessions reellement disponibles : le message d'erreur doit les
    afficher plutot que de laisser l'utilisateur deviner l'orthographe
    exacte attendue.
    """

    def __init__(self, libelle_demande: str, sessions_disponibles: "list"):
        super().__init__(f"aucune session ne correspond a '{libelle_demande}'")
        self.libelle_demande = libelle_demande
        self.sessions_disponibles = sessions_disponibles


def _normaliser_libelle_session(libelle: str) -> str:
    """Neutralise casse, espaces de tete/fin et accents avant de comparer un
    libelle de session tape a la main a celui affiche par le selecteur.

    Decompose les caracteres accentues (NFKD) puis retire les marques
    combinantes : "Été" et "ete" se comparent ainsi egaux, tout comme "
    Automne 2022 " et "AUTOMNE 2022".
    """
    sans_accents = unicodedata.normalize("NFKD", libelle.strip())
    sans_accents = "".join(
        caractere for caractere in sans_accents if not unicodedata.combining(caractere)
    )
    return sans_accents.casefold()


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


def _drainer_progression(evenements: "queue.Queue", etat: dict, imprimer=print) -> None:
    """Vide la file comme _drainer, mais annote chaque evenement 'cours' de
    son rang global (toutes sessions confondues) sur le total, et de la
    session dont il fait partie.

    Reservee a --session et --tout : sur des dizaines de cours repartis sur
    plusieurs sessions, un compteur qui ne bouge qu'a la fin est
    indiscernable d'un programme bloque, et savoir seulement qu'un cours
    avance sans savoir combien il en reste ni dans quelle session ne dit pas
    grand-chose sur un enchainement de plusieurs heures.

    La session de chaque cours vient d'un 'plan' pose en un seul evenement,
    PAS des evenements 'session' (qui ne font qu'annoncer la progression de
    l'enumeration, laquelle se termine entierement avant que l'archivage --
    et donc les evenements 'cours' -- ne commence : un simple pointeur mis a
    jour par ces evenements 'session' figerait sur la derniere session
    enumeree pour tous les cours). `etat` porte ce plan, dans l'ordre exact
    ou Archiveur.archiver() traite les cours, d'un appel de drainage a
    l'autre.
    """
    while not evenements.empty():
        type_evenement, donnee = evenements.get()
        if type_evenement == "plan":
            etat["plan"] = donnee
        elif type_evenement == "session":
            indice, total_sessions, libelle, detail = donnee
            imprimer(f"\n=== Session {indice}/{total_sessions} : {libelle} - {detail} ===")
        elif type_evenement == "cours":
            plan = etat.get("plan", [])
            rang = etat.get("rang", 0)
            libelle_session = plan[rang] if rang < len(plan) else "?"
            etat["rang"] = rang + 1
            imprimer(f"  [{libelle_session}] ({etat['rang']}/{len(plan)}) {donnee}")
        else:
            imprimer(f"  [{type_evenement}] {donnee}")


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


def _archiver_plusieurs_sessions(
    resoudre_sessions, destination: Path, session=None, fabrique_ena=Ena
) -> int:
    """Archive les cours d'une ou plusieurs sessions, en console.

    Reutilise Archiveur telle quelle, sans rien lui ajouter : `resoudre_sessions(ena)`
    determine seulement LA LISTE des Session a couvrir (une seule pour
    --session, toutes pour --tout) ; l'enumeration de leurs cours puis
    l'archivage passent par les memes mecanismes que _un_seul_cours, sur la
    liste complete de tous les cours retenus, remise a Archiveur.archiver()
    en un seul appel.

    Un seul appel, jamais un par session : Archiveur.archiver() ecrit
    notes-tous-cours.csv a partir d'une liste locale a l'appel en cours ;
    l'appeler une fois par session ecraserait ce fichier a chaque session au
    lieu de couvrir l'ensemble. Le cout est qu'aucun telechargement ne
    commence avant que toutes les sessions visees aient ete enumerees ; si
    une session expire pendant cette enumeration, rien n'a encore ete
    ecrit -- ce qui reste un rapport honnete, jamais un rapport tronque.
    Une fois l'archivage entame, en revanche, une session qui expire au
    milieu laisse un rapport qui couvre tout ce qui a deja ete tente,
    exactement comme pour --un-seul-cours.

    `session` et `fabrique_ena` sont injectables pour les tests, comme
    _un_seul_cours.
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
            sessions_a_traiter = resoudre_sessions(ena)
            if not sessions_a_traiter:
                contexte["aucune_session"] = True
                return

            archiveur = Archiveur(ena, session.transport, destination, evenements)
            # Stocke la reference AVANT toute enumeration ou tout archivage :
            # si une session expire en cours de route (enumeration comprise),
            # le rapport final doit exister quand meme, meme s'il ne couvre
            # que ce qui a deja ete tente jusque-la.
            contexte["archiveur"] = archiveur

            total_sessions = len(sessions_a_traiter)
            tous_les_cours = []
            for indice, session_cible in enumerate(sessions_a_traiter, start=1):
                evenements.put(
                    ("session", (indice, total_sessions, session_cible.libelle, "enumeration en cours..."))
                )
                cours_de_la_session = ena.sites_de_session(session_cible)
                evenements.put(
                    (
                        "session",
                        (
                            indice,
                            total_sessions,
                            session_cible.libelle,
                            f"{len(cours_de_la_session)} cours",
                        ),
                    )
                )
                tous_les_cours.extend(cours_de_la_session)

            evenements.put(("plan", [c.session.libelle for c in tous_les_cours]))
            contexte["resultat"] = archiveur.archiver(tous_les_cours)
        except SessionIntrouvable as erreur:
            contexte["erreur"] = erreur
        except SessionExpiree as erreur:
            contexte["erreur"] = erreur
        except SelecteurSessionsIllisible as erreur:
            contexte["erreur"] = erreur
        # Isolation stricte, comme _un_seul_cours : une exception dans ce fil
        # ne doit jamais mourir en silence sans que l'appelant le sache.
        except Exception as erreur:
            traceback.print_exc()
            contexte["erreur"] = erreur
        finally:
            session.fermer()

    fil = threading.Thread(target=travailler, daemon=False)
    fil.start()
    etat_progression: dict = {}
    try:
        while fil.is_alive():
            _drainer_progression(evenements, etat_progression)
            fil.join(timeout=0.2)
    except KeyboardInterrupt:
        # Meme raisonnement que _un_seul_cours : on attend la fin du fil,
        # dont le finally ferme le navigateur, plutot que de couper court et
        # laisser un processus Playwright orphelin sur Windows.
        print(
            "\nInterruption demandee : fermeture du navigateur en cours, patientez...",
            file=sys.stderr,
        )
        fil.join()
        _drainer_progression(evenements, etat_progression)
        archiveur = contexte.get("archiveur")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 130

    _drainer_progression(evenements, etat_progression)

    if contexte.get("connexion_echouee"):
        print("ECHEC : connexion non detectee.", file=sys.stderr)
        return 1

    erreur = contexte.get("erreur")
    archiveur = contexte.get("archiveur")

    if isinstance(erreur, SessionIntrouvable):
        print(f"ECHEC : {erreur}", file=sys.stderr)
        print("Sessions disponibles :", file=sys.stderr)
        for session_disponible in erreur.sessions_disponibles:
            print(f"  - {session_disponible.libelle}", file=sys.stderr)
        return 1

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

    if contexte.get("aucune_session"):
        print("ECHEC : aucune session trouvee.", file=sys.stderr)
        return 1

    resultat = contexte["resultat"]
    chemin_rapport = _ecrire_rapport_final(destination, resultat)
    print(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    print(f"Echecs : {len(resultat.echecs)}  -> {chemin_rapport}")
    return _code_de_sortie(resultat)


def _session(libelle: str, destination: Path, session=None, fabrique_ena=Ena) -> int:
    """Archive tous les cours d'une session, en console.

    Le libelle demande est compare a ceux du selecteur avec une tolerance
    raisonnable sur la casse, les espaces de tete et de fin, et les accents
    (voir _normaliser_libelle_session) : il sera tape a la main. Si aucune
    session ne correspond, resoudre() leve SessionIntrouvable avec la liste
    reelle des sessions disponibles, affichee par _archiver_plusieurs_sessions.
    """

    def resoudre(ena):
        disponibles = ena.sessions_disponibles()
        cible = _normaliser_libelle_session(libelle)
        for candidate in disponibles:
            if _normaliser_libelle_session(candidate.libelle) == cible:
                return [candidate]
        raise SessionIntrouvable(libelle, disponibles)

    return _archiver_plusieurs_sessions(resoudre, destination, session, fabrique_ena)


def _tout(destination: Path, session=None, fabrique_ena=Ena) -> int:
    """Archive toutes les sessions, de la plus ancienne a la plus recente.

    Session.code est de la forme AAAASS (annee puis mois de debut) : trier
    dessus rend directement l'ordre chronologique, sans avoir a interpreter
    le libelle affiche.
    """

    def resoudre(ena):
        return sorted(ena.sessions_disponibles(), key=lambda s: s.code)

    return _archiver_plusieurs_sessions(resoudre, destination, session, fabrique_ena)


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
        "--session",
        dest="session_cible",
        help="libelle de la session a archiver en entier, ex. \"Automne 2022\"",
    )
    groupe_mode.add_argument(
        "--tout",
        action="store_true",
        help="archive toutes les sessions, de la plus ancienne a la plus recente",
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

        if arguments.session_cible:
            return _session(arguments.session_cible, arguments.destination)

        if arguments.tout:
            return _tout(arguments.destination)

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
