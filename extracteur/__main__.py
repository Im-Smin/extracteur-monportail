"""Points d'entree de l'extracteur.

  python -m extracteur                          -> fenetre graphique
  python -m extracteur --lister                 -> enumeration en console
  python -m extracteur --un-seul-cours 100001   -> archivage d'un seul cours, en console
  python -m extracteur --session "Automne 2022" -> archivage de tous les cours d'une session
  python -m extracteur --tout                   -> archivage de toutes les sessions, de la plus
                                                    ancienne a la plus recente
  python -m extracteur --diagnostic             -> etat des lieux du DOM du selecteur de sessions
  python -m extracteur --verifier               -> confronte une archive existante a son manifeste,
                                                    sans rien telecharger ni ouvrir de navigateur
  python -m extracteur --zip                    -> compresse une archive existante en un fichier .zip,
                                                    sans rien telecharger

--lister, --un-seul-cours, --session, --tout, --diagnostic, --verifier et
--zip sont mutuellement exclusifs : les combiner est rejete par argparse
plutot que de laisser l'un l'emporter en silence.

Chaque lancement de --lister, --diagnostic, --un-seul-cours, --session ou
--tout ouvre le navigateur sur un profil neuf et demande une authentification
complete, MFA compris : aucun profil de navigateur n'est conserve d'une
execution a l'autre. C'est un choix de conception deliberement retenu apres
qu'un profil persistant se soit corrompu a deux reprises en conditions
reelles (voir la docstring d'extracteur.auth et docs/api-monportail.md,
section "Pieges d'exploitation"), pas une regression : le cout d'une
reconnexion par lancement est juge acceptable puisque --tout traite les 33
cours du projet en une seule execution.

--verifier et --zip ne se connectent jamais a monPortail : ils ne touchent
qu'au disque, sur le dossier --destination d'une archive deja produite par un
des modes ci-dessus. Penses pour rester utilisables bien apres la fermeture
de la plateforme, le 1er novembre 2026, quand aucun autre mode ne le sera
plus.

--un-seul-cours, --session et --tout terminent tous les trois par le meme
controle : verification.verifier() confronte le manifeste au disque, et toute
anomalie (fichier manquant, taille incorrecte) est versee dans le rapport
d'echecs final au meme titre qu'un echec de telechargement -- avec le meme
effet sur le code de sortie (voir _code_de_sortie).

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
  0    succes complet : rien a signaler. Pour --verifier, aucune anomalie
       detectee (aucun fichier manquant, aucune taille incorrecte). Pour
       --zip, le fichier compresse a ete ecrit.
  1    echec sans rien ecrire (connexion non detectee, selecteur de sessions
       illisible, erreur inattendue, --session dont le libelle ne correspond
       a aucune session, ou --lister/--un-seul-cours/--session/--tout n'a
       rien produit et a consigne des echecs). Pour --verifier : dossier
       d'archive introuvable, ou au moins un fichier manquant ou de taille
       incorrecte. Pour --zip : dossier d'archive introuvable, ou echec
       d'ecriture du fichier compresse.
  2    --un-seul-cours : aucun cours ne correspond a l'idSite demande.
  3    session expiree en cours de route ; relancer la meme commande. Si
       l'expiration survient pendant un telechargement, la reprise est
       idempotente (--session et --tout reprennent aussi la ou ils se sont
       arretes, sans retelecharger ce qui est deja sur disque). Si elle
       survient plus tot -- pendant l'enumeration des sessions et cours,
       avant qu'aucun fichier n'ait ete ecrit -- rien n'est a "reprendre" :
       relancer recommence entierement l'enumeration a zero. Le message
       console et _rapport.html distinguent explicitement les deux cas.
  4    --un-seul-cours, --session ou --tout : archivage partiel, au moins un
       fichier ecrit mais aussi des echecs consignes dans le rapport (voir
       _rapport.html) -- une anomalie relevee par la verification finale y
       compte au meme titre qu'un echec de telechargement.
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
from extracteur.auth import (
    HOTE_SITESCOURS,
    PREFIXE_TITRE_FENETRE_PILOTEE,
    TITRE_FENETRE_PILOTEE,
    SessionNavigateur,
)
from extracteur.ena import Ena, SelecteurSessionsIllisible
from extracteur.manifeste import ecrire_rapport
from extracteur.modele import Echec, Resultat
from extracteur.telechargement import SessionExpiree
from extracteur.verification import creer_zip, verifier

NOM_RAPPORT = "_rapport.html"

# Affiche avant l'ouverture du navigateur, sur chacun des modes qui s'y
# connectent : sans cette explication, l'utilisateur croirait a une
# regression la premiere fois qu'il devrait retaper son mot de passe d'un
# lancement a l'autre, alors que c'est un choix de conception assume (voir
# la docstring du module et celle d'extracteur.auth.SessionNavigateur).
MESSAGE_INVITATION_CONNEXION = (
    "Connectez-vous dans la fenetre du navigateur...\n"
    f'Reconnaissez-la a son onglet, intitule "{TITRE_FENETRE_PILOTEE}" puis '
    f'prefixe "{PREFIXE_TITRE_FENETRE_PILOTEE.strip()}" une fois sur la page '
    "de connexion : cette fenetre-la, et seulement elle, tourne sur un profil "
    "cree vierge a ce lancement -- sans barre de favoris ni avatar de compte "
    "personnel. Se connecter dans une autre fenetre ou un autre navigateur ne "
    "sert a rien : le programme ne peut lire que les cookies de celle-ci.\n"
    "(authentification demandee a chaque lancement : aucun profil de "
    "navigateur n'est conserve d'une execution a l'autre -- c'est voulu, "
    "pas un bug ; voir docs/api-monportail.md.)"
)


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


def _message_echec_connexion(session) -> str:
    """Message d'echec explicite : dit sur quel domaine la page se trouvait
    au dernier sondage d'attendre_connexion, et ce que cela suggere, plutot
    que le seul constat d'un delai ecoule (qui n'aide en rien a agir).

    `dernier_domaine_observe` peut etre absent (doublure de test qui ne le
    pose pas) : le message se rabat alors sur le constat generique.
    """
    domaine = getattr(session, "dernier_domaine_observe", None)
    if domaine is None:
        return "connexion non detectee dans le delai imparti (aucune page chargee)."
    if domaine == HOTE_SITESCOURS:
        return (
            f"connexion non detectee dans le delai imparti (page restee sur {domaine} "
            "sans afficher son contenu : page qui n'a pas fini de charger, ou acces refuse)."
        )
    return (
        f"connexion non detectee dans le delai imparti (page restee sur {domaine} : "
        "la connexion n'a jamais abouti)."
    )


def _connecter(
    sans_fenetre: bool = False,
    session: SessionNavigateur | None = None,
) -> SessionNavigateur:
    """Ouvre le navigateur et attend la connexion manuelle, MFA compris.

    `session` est injectable pour les tests (doublure) ; en usage normal, une
    vraie SessionNavigateur est creee et ouverte ici, sur un profil de
    navigateur neuf (voir SessionNavigateur.ouvrir) : chaque appel redemande
    donc une authentification complete.

    Responsable de fermer ce qu'elle a ouvert si quoi que ce soit echoue en
    cours de route -- interruption clavier comprise. KeyboardInterrupt n'est
    pas une sous-classe d'Exception, d'ou l'interception large sur
    BaseException. On ferme puis on relance : on nettoie, on n'avale jamais
    l'exception.
    """
    if session is None:
        session = SessionNavigateur(sans_fenetre=sans_fenetre)
    try:
        session.ouvrir()
        print(MESSAGE_INVITATION_CONNEXION)
        if not session.attendre_connexion():
            raise ConnexionEchouee(_message_echec_connexion(session))
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


def _ecrire_rapport_final(
    destination: Path, resultat: Resultat, interruption: str | None = None
) -> Path | None:
    """Le compte rendu final : doit exister meme apres une session expiree ou
    une interruption, pour dire ce qu'il reste a recuperer a la main.

    `interruption`, quand fourni, est le message en clair qui fait basculer
    le rapport dans son troisieme etat (voir ecrire_rapport) : ni succes
    complet, ni simple echec partiel, mais un arret en cours de route.

    Si l'ecriture du rapport echoue (fichier verrouille, notamment), affiche
    un message clair en stderr et retourne None sans elever d'exception :
    l'archivage lui-meme a reussi, seul le compte rendu fait defaut."""
    chemin = destination / NOM_RAPPORT
    try:
        ecrire_rapport(
            chemin,
            resultat.echecs,
            {"fichiers ecrits": resultat.fichiers_ecrits, "fichiers sautes": resultat.fichiers_sautes},
            interruption=interruption,
        )
        return chemin
    except OSError as erreur:
        print(
            f"ATTENTION : le rapport n'a pas pu etre ecrit : {erreur}\n"
            f"  Cause probable : le fichier {NOM_RAPPORT} est ouvert ailleurs.\n"
            f"  Rapport aurait ete ecrit a : {chemin}",
            file=sys.stderr,
        )
        return None


def _code_de_sortie(resultat: Resultat) -> int:
    """0 seulement si aucun echec n'a ete consigne ET qu'aucun cours n'a ete
    laisse de cote : un cours ancien peut produire des fichiers ET des
    echecs partiels, ce n'est pas un succes complet -- et un archivage
    interrompu par l'utilisateur (Archiveur.archiver(annulation=...)) sans
    le moindre echec de telechargement n'en est pas un non plus. Voir la
    docstring du module pour le contrat des codes.

    `cours_non_tentes` n'est normalement jamais different de zero ici : une
    SessionExpiree, seule autre source de cours non tentes, est interceptee
    plus haut et rend 3 avant d'atteindre cette fonction. Le cas couvert ici
    est l'annulation cooperative, qui ne leve pas d'exception (voir la
    docstring de Archiveur.archiver).
    """
    if not resultat.echecs and not resultat.cours_non_tentes:
        return 0
    return 4 if resultat.fichiers_ecrits else 1


def _fusionner_verification(resultat: Resultat, controle: dict) -> None:
    """Verse les anomalies de la verification finale dans le resultat.

    Elles apparaissent ainsi dans _rapport.html au meme titre qu'un echec de
    telechargement -- c'est le document qui dit ce qu'il reste a recuperer a
    la main -- et pesent de la meme facon sur le code de sortie
    (_code_de_sortie ne distingue pas leur origine).
    """
    for relatif in controle["manquants"]:
        resultat.echecs.append(
            Echec(cours="(verification)", element=relatif, cause="fichier manquant sur disque")
        )
    for relatif in controle["taille_incorrecte"]:
        resultat.echecs.append(
            Echec(
                cours="(verification)",
                element=relatif,
                cause="taille sur disque differente du manifeste",
            )
        )


def _message_interruption_annulation(resultat: Resultat) -> "str | None":
    """Message du troisieme etat de ecrire_rapport quand l'arret vient d'une
    annulation cooperative (Archiveur.archiver(annulation=...)), pas d'une
    SessionExpiree -- celle-ci est deja interceptee plus haut et jamais
    tentee ici (voir la docstring de _code_de_sortie).

    None quand cours_non_tentes vaut zero : c'est ce qui garde le chemin
    nominal, deja couvert par tous les tests existants, strictement inchange.
    """
    if not resultat.cours_non_tentes:
        return None
    return (
        f"L'archivage a ete interrompu par l'utilisateur : {resultat.cours_non_tentes} "
        "cours n'ont jamais ete tentes. Relancer reprend la ou l'archivage s'est arrete, "
        "sans retelecharger ce qui est deja sur disque."
    )


def _afficher_sessions(ena, imprimer=print) -> "list[str]":
    """Enumere sessions et cours sans rien telecharger ni rien ecrire sur
    disque : la sonde la moins risquee pour valider l'enumeration contre la
    vraie plateforme.

    Le nombre de sessions trouvees est annonce en tete, avant la liste
    elle-meme : c'est la seule protection reelle contre un panneau qui
    plafonne durablement sur un compte incomplet (voir la docstring de
    Ena._stabiliser_options). Aucun code ne peut deviner qu'il manque des
    sessions a un palier atteint trop tot ; l'utilisateur, qui connait son
    propre parcours, le peut d'un coup d'oeil.

    Isolation par session, symetrique a celle d'Archiveur.archiver() par
    cours : une session dont le selecteur ne confirme jamais la selection
    (SelecteurSessionsIndisponible, entre autres) est signalee puis sautee,
    sans emporter les sessions suivantes. Reproduit le defaut constate en
    conditions reelles ou une session future et vide (« Hiver 2027 ») a fait
    perdre l'inventaire complet des douze sessions. Rend la liste des
    libelles de session en echec, vide si aucune -- SessionExpiree n'est
    jamais absorbee ici : une session d'authentification morte ne se repare
    pas en passant a la session suivante.
    """
    sessions = ena.sessions_disponibles()
    if not sessions:
        imprimer("Aucune session trouvee.")
        return []

    imprimer(f"{len(sessions)} session(s) detectee(s).")
    sessions_en_echec: "list[str]" = []
    for session in sessions:
        imprimer(f"\n=== {session.libelle} ({session.code}) ===")
        try:
            cours_de_la_session = ena.sites_de_session(session)
        except SessionExpiree:
            raise
        except Exception as erreur:  # isolation stricte par session
            sessions_en_echec.append(session.libelle)
            imprimer(f"  ECHEC : session sautee - {erreur}")
            continue
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

    return sessions_en_echec


def _lister(session=None, fabrique_ena=Ena) -> int:
    """Enumere sessions et cours, en console, sans navigateur graphique.

    `session` et `fabrique_ena` sont injectables pour les tests (doublure) ;
    en usage normal, une vraie SessionNavigateur et Ena sont utilisees.

    La fermeture du navigateur est garantie par _connecter elle-meme (voir sa
    docstring) : si l'attente de connexion est interrompue au clavier avant
    que `session_ouverte` soit affecte, ce `finally` ne referme rien, mais
    rien ne reste ouvert non plus, puisque _connecter a deja ferme avant de
    relancer.

    Rend 1 (inventaire partiel) si _afficher_sessions a du sauter au moins
    une session : --lister n'ecrit rien sur disque, mais un inventaire
    incomplet ne doit pas se faire passer pour un succes complet.
    """
    session_ouverte = None
    try:
        session_ouverte = _connecter(session=session)
        sessions_en_echec = _afficher_sessions(fabrique_ena(session_ouverte))
        if sessions_en_echec:
            print(
                f"\nECHEC : {len(sessions_en_echec)} session(s) n'ont pas pu etre lues : "
                f"{', '.join(sessions_en_echec)}.",
                file=sys.stderr,
            )
            return 1
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
        if rapport.get("echec_stabilisation_options"):
            imprimer(
                "echec de stabilisation des options de session : "
                f"{rapport['echec_stabilisation_options']}"
            )
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


def _un_seul_cours(
    id_site: str,
    destination: Path,
    session=None,
    fabrique_ena=Ena,
    imprimer=print,
    imprimer_erreur=None,
    annulation=None,
    sur_fin=None,
) -> int:
    """Archive un seul cours, en console ou depuis l'interface graphique.

    `session` et `fabrique_ena` sont injectables pour les tests ; en usage
    normal, une vraie SessionNavigateur et Ena sont utilisees, sur un profil
    de navigateur neuf (voir SessionNavigateur.ouvrir).

    `imprimer` et `imprimer_erreur` recoivent respectivement ce qui irait sur
    stdout et sur stderr en console : c'est le coeur commun evoque dans la
    tache 12, appele tel quel par la console (valeurs par defaut) et par
    l'interface graphique (callables qui alimentent sa file d'affichage au
    lieu du vrai flux standard). `imprimer_erreur` vaut, par defaut, un print
    sur stderr -- non exprimable directement en valeur par defaut d'argument
    puisqu'elle doit fermer sur sys.stderr au moment de l'appel, pas a la
    definition du module.

    `annulation`, un threading.Event optionnel, est transmis a
    l'attente de connexion et a l'archiveur : le poser interrompt proprement
    le travail a la prochaine frontiere verifiee (sondage de connexion,
    frontiere de cours), sans jamais toucher aux objets Playwright depuis un
    autre fil que celui-ci.

    `sur_fin`, quand fourni, est appele une seule fois avec
    (resultat, controle, chemin_rapport) au moment ou l'archivage se termine
    normalement (succes complet ou partiel) -- jamais sur les autres issues
    (connexion non detectee, cours introuvable, session expiree, erreur
    inattendue), deja entierement racontees par imprimer_erreur. Sert
    l'interface graphique, qui a besoin des compteurs exacts pour son etat
    final, sans avoir a les re-analyser depuis le texte affiche.

    La connexion et l'archivage tournent dans le meme fil : Playwright (API
    synchrone) exige que tous ses appels viennent du fil qui l'a demarre. Le
    fil appelant se contente de vider la file d'evenements pendant que ce
    fil de travail avance, pour afficher la progression au fil de l'eau au
    lieu de rester muet jusqu'a la fin.
    """
    if session is None:
        session = SessionNavigateur()
    if imprimer_erreur is None:
        imprimer_erreur = lambda texte: print(texte, file=sys.stderr)  # noqa: E731

    evenements: "queue.Queue" = queue.Queue()
    contexte: dict = {}

    def travailler() -> None:
        try:
            session.ouvrir()
            imprimer(MESSAGE_INVITATION_CONNEXION)
            if not session.attendre_connexion(annulation=annulation):
                contexte["connexion_echouee"] = _message_echec_connexion(session)
                return
            imprimer("Connexion detectee.")

            ena = fabrique_ena(session)
            archiveur = Archiveur(ena, session.transport, destination, evenements)
            # Stocke la reference AVANT _chercher_cours, pas seulement avant
            # archiver() : _chercher_cours enumere lui-meme sessions et
            # cours (ena.sessions_disponibles / ena.sites_de_session), donc
            # le tout premier appel reseau de cette fonction peut deja lever
            # SessionExpiree, avant qu'aucun cours n'ait ete localise. Sans
            # l'archiveur des ce point, aucun rapport ne serait ecrit du
            # tout -- le pire mode de defaillance du projet.
            contexte["archiveur"] = archiveur

            cours = _chercher_cours(ena, id_site)
            if cours is None:
                contexte["erreur"] = CoursIntrouvable(id_site)
                return

            # Marque le passage a la phase d'archivage : distingue, pour le
            # message de reprise, une expiration pendant la recherche du
            # cours (rien de tente, l'enumeration recommencera a zero) d'une
            # expiration pendant l'archivage lui-meme (reprise idempotente).
            contexte["cours_localise"] = True
            contexte["resultat"] = archiveur.archiver([cours], annulation=annulation)
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
            _drainer(evenements, imprimer=imprimer)
            fil.join(timeout=0.2)
    except KeyboardInterrupt:
        # On attend la fin du fil plutot que de couper court : c'est le
        # finally de travailler() qui ferme le navigateur, et un processus
        # Playwright orphelin est une nuisance concrete sur Windows.
        imprimer_erreur("\nInterruption demandee : fermeture du navigateur en cours, patientez...")
        fil.join()
        _drainer(evenements, imprimer=imprimer)
        archiveur = contexte.get("archiveur")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 130

    _drainer(evenements, imprimer=imprimer)

    if contexte.get("connexion_echouee"):
        imprimer_erreur(f"ECHEC : {contexte['connexion_echouee']}")
        return 1

    erreur = contexte.get("erreur")
    archiveur = contexte.get("archiveur")

    if isinstance(erreur, CoursIntrouvable):
        imprimer_erreur(f"ECHEC : aucun cours ne correspond a idSite={id_site}.")
        return 2

    if isinstance(erreur, SelecteurSessionsIllisible):
        imprimer_erreur(f"ECHEC : {erreur}")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

    if isinstance(erreur, SessionExpiree):
        if contexte.get("cours_localise"):
            # L'archivage avait deja commence : la reprise par manifeste ne
            # retelecharge jamais un fichier deja ecrit sur disque.
            imprimer_erreur(
                "\nSession expiree pendant l'archivage. Reconnectez-vous puis "
                "relancez la meme commande : la reprise est idempotente, elle "
                "continue la ou elle s'est arretee."
            )
            message_rapport = (
                f"La session a expire pendant l'archivage du cours idSite={id_site} : "
                "son contenu n'a pas pu etre completement recupere."
            )
        else:
            # Rien n'a encore ete ecrit : le cours vise n'a meme pas ete
            # localise. Relancer ne "reprend" rien, ca recommence a zero.
            imprimer_erreur(
                "\nSession expiree avant meme de localiser le cours vise. "
                "Aucun fichier n'a encore ete traite : reconnectez-vous puis "
                "relancez la meme commande, l'enumeration recommencera "
                "entierement depuis le debut."
            )
            message_rapport = (
                f"La session a expire avant meme que le cours vise (idSite={id_site}) "
                "n'ait pu etre localise dans l'enumeration des sessions. Aucun fichier "
                "n'a ete telecharge."
            )
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat, interruption=message_rapport)
        return 3

    if erreur is not None:
        imprimer_erreur(f"ECHEC inattendu : {erreur}")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

    resultat = contexte["resultat"]
    controle = verifier(destination)
    _fusionner_verification(resultat, controle)
    chemin_rapport = _ecrire_rapport_final(
        destination, resultat, interruption=_message_interruption_annulation(resultat)
    )
    imprimer(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    if chemin_rapport:
        imprimer(f"Echecs : {len(resultat.echecs)}  -> {chemin_rapport}")
    else:
        imprimer(f"Echecs : {len(resultat.echecs)}  (rapport non ecrit)")
    imprimer(
        f"Verification : {controle['inscrits']} inscrits, "
        f"{len(controle['manquants'])} manquants, "
        f"{len(controle['taille_incorrecte'])} de taille incorrecte"
    )
    if sur_fin is not None:
        sur_fin(resultat, controle, chemin_rapport)
    return _code_de_sortie(resultat)


def _archiver_plusieurs_sessions(
    resoudre_sessions,
    destination: Path,
    session=None,
    fabrique_ena=Ena,
    imprimer=print,
    imprimer_erreur=None,
    annulation=None,
    sur_fin=None,
) -> int:
    """Archive les cours d'une ou plusieurs sessions, en console.

    Reutilise Archiveur telle quelle, sans rien lui ajouter : `resoudre_sessions(ena)`
    determine seulement LA LISTE des Session a couvrir (une seule pour
    --session, toutes pour --tout) ; l'enumeration de leurs cours puis
    l'archivage passent par les memes mecanismes que _un_seul_cours, sur la
    liste complete de tous les cours retenus, remise a Archiveur.archiver()
    en un seul appel.

    Un seul appel, jamais un par session : Archiveur.archiver() reconstruit
    desormais notes-tous-cours.csv a partir des notes.csv de chaque cours
    deja ecrits sur disque (voir manifeste.consolider_notes), donc un appel
    par session ne l'ecraserait plus a tort. Le regroupement en un seul
    appel reste neanmoins necessaire : resultat.cours_non_tentes et
    l'evenement "fin" doivent porter sur l'ensemble des sessions visees, pas
    sur une seule a la fois. Le cout est qu'aucun telechargement ne
    commence avant que toutes les sessions visees aient ete enumerees ; si
    une session expire pendant cette enumeration, rien n'a encore ete ecrit,
    ET le rapport le dit explicitement (etat "travail interrompu" de
    ecrire_rapport) -- jamais un rapport qui pretendrait, a tort, que tout a
    ete recupere simplement parce qu'aucun echec n'est consigne. Une fois
    l'archivage entame, en revanche, une session qui expire au milieu laisse
    un rapport qui couvre tout ce qui a deja ete tente, et signale de la
    meme facon ce qui ne l'a jamais ete, exactement comme pour
    --un-seul-cours.

    L'enumeration des cours d'une session (ena.sites_de_session) est isolee
    session par session, symetriquement a l'isolation par cours
    d'Archiveur.archiver() : une session dont la selection echoue
    (SelecteurSessionsIndisponible, entre autres) est consignee comme un
    echec portant son libelle et sautee, l'enumeration continuant avec les
    suivantes. SessionExpiree fait exception a cette isolation : elle
    interrompt tout l'enchainement, comme avant, une session
    d'authentification morte ne se reparant jamais en passant a la suivante.

    `session` et `fabrique_ena` sont injectables pour les tests, comme
    _un_seul_cours. `imprimer`, `imprimer_erreur`, `annulation` et `sur_fin`
    ont exactement le meme role et le meme contrat que dans _un_seul_cours --
    voir sa docstring -- et sont ce qui permet a l'interface graphique de
    reutiliser ce meme coeur pour --session et --tout, sans en dupliquer la
    logique.
    """
    if session is None:
        session = SessionNavigateur()
    if imprimer_erreur is None:
        imprimer_erreur = lambda texte: print(texte, file=sys.stderr)  # noqa: E731

    evenements: "queue.Queue" = queue.Queue()
    contexte: dict = {}

    def travailler() -> None:
        try:
            session.ouvrir()
            imprimer(MESSAGE_INVITATION_CONNEXION)
            if not session.attendre_connexion(annulation=annulation):
                contexte["connexion_echouee"] = _message_echec_connexion(session)
                return
            imprimer("Connexion detectee.")

            ena = fabrique_ena(session)
            archiveur = Archiveur(ena, session.transport, destination, evenements)
            # Stocke la reference AVANT resoudre_sessions : c'est deja le
            # tout premier appel reseau (ena.sessions_disponibles, via
            # resoudre_sessions), et il peut a lui seul lever SessionExpiree,
            # avant qu'aucune session ne soit connue. Sans l'archiveur des ce
            # point, aucun rapport ne serait ecrit du tout.
            contexte["archiveur"] = archiveur

            # sessions_disponibles() ne rend jamais de liste vide : elle leve
            # SelecteurSessionsIllisible des que le panneau n'offre aucune
            # option valide (voir sa docstring dans ena.py). resoudre_sessions
            # ne peut donc jamais rendre [] ici non plus : --session renvoie
            # soit un candidat unique soit leve SessionIntrouvable, et --tout
            # trie cette meme liste garantie non vide. Un garde sur une liste
            # vide serait un chemin mort, jamais atteint.
            sessions_a_traiter = resoudre_sessions(ena)
            contexte["sessions_prevues"] = len(sessions_a_traiter)

            total_sessions = len(sessions_a_traiter)
            tous_les_cours = []
            sessions_en_echec: "list[str]" = []
            contexte["sessions_en_echec"] = sessions_en_echec
            for indice, session_cible in enumerate(sessions_a_traiter, start=1):
                if annulation is not None and annulation.is_set():
                    # Arret demande pendant l'enumeration, donc avant que le
                    # moindre fichier ait ete ecrit -- l'archivage ne commence
                    # qu'une fois TOUTES les sessions visees enumerees. Sans ce
                    # point d'arret, l'interface graphique laisserait son
                    # utilisateur attendre l'enumeration des douze sessions
                    # apres qu'il a clique sur Arreter.
                    #
                    # On sort sans appeler archiver() : sur une liste de cours
                    # vide il rendrait un Resultat sans echec ni cours non
                    # tente, que _code_de_sortie traduirait en 0 -- un succes
                    # complet annonce sur une archive inexistante.
                    contexte["interrompu_a_l_enumeration"] = (indice - 1, total_sessions)
                    return
                evenements.put(
                    ("session", (indice, total_sessions, session_cible.libelle, "enumeration en cours..."))
                )
                # Isolation par session, symetrique a celle d'Archiveur.archiver()
                # par cours : une session dont le selecteur ne confirme jamais
                # la selection (SelecteurSessionsIndisponible, entre autres) est
                # consignee en echec et sautee, sans emporter les sessions
                # suivantes. Reproduit le defaut constate en conditions reelles
                # ou une session future et vide (« Hiver 2027 ») a fait perdre
                # l'inventaire complet des douze sessions -- le reste de
                # l'archivage doit survivre a une seule session illisible.
                # SessionExpiree n'est jamais absorbee ici : une session
                # d'authentification morte ne se repare pas en passant a la
                # session suivante, elle doit interrompre tout l'enchainement.
                try:
                    cours_de_la_session = ena.sites_de_session(session_cible)
                except SessionExpiree:
                    raise
                except Exception as erreur:  # isolation stricte par session
                    contexte["sessions_enumerees"] = indice
                    sessions_en_echec.append(session_cible.libelle)
                    archiveur.resultat.echecs.append(
                        Echec(
                            cours=session_cible.libelle,
                            element="(session entiere)",
                            cause=str(erreur),
                        )
                    )
                    evenements.put(
                        (
                            "session",
                            (indice, total_sessions, session_cible.libelle, f"ECHEC - {erreur}"),
                        )
                    )
                    continue
                contexte["sessions_enumerees"] = indice
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
                contexte["cours_enumeres"] = len(tous_les_cours)

            evenements.put(("plan", [c.session.libelle for c in tous_les_cours]))
            # Marque le passage a la phase d'archivage : distingue, pour le
            # message de reprise, une expiration pendant l'enumeration (rien
            # de tente, tout recommencera a zero) d'une expiration pendant
            # l'archivage lui-meme (reprise idempotente par manifeste).
            contexte["archivage_commence"] = True
            contexte["resultat"] = archiveur.archiver(tous_les_cours, annulation=annulation)
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
            _drainer_progression(evenements, etat_progression, imprimer=imprimer)
            fil.join(timeout=0.2)
    except KeyboardInterrupt:
        # Meme raisonnement que _un_seul_cours : on attend la fin du fil,
        # dont le finally ferme le navigateur, plutot que de couper court et
        # laisser un processus Playwright orphelin sur Windows.
        imprimer_erreur("\nInterruption demandee : fermeture du navigateur en cours, patientez...")
        fil.join()
        _drainer_progression(evenements, etat_progression, imprimer=imprimer)
        archiveur = contexte.get("archiveur")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 130

    _drainer_progression(evenements, etat_progression, imprimer=imprimer)

    if contexte.get("connexion_echouee"):
        imprimer_erreur(f"ECHEC : {contexte['connexion_echouee']}")
        return 1

    erreur = contexte.get("erreur")
    archiveur = contexte.get("archiveur")

    interrompu = contexte.get("interrompu_a_l_enumeration")
    if interrompu is not None:
        enumerees, prevues = interrompu
        message_rapport = (
            f"L'archivage a ete interrompu par l'utilisateur pendant l'enumeration : "
            f"{enumerees} session(s) sur {prevues} avaient ete parcourues, et aucun "
            "fichier n'avait encore ete telecharge. Relancer recommence l'enumeration "
            "depuis le debut, sans retelecharger ce qui serait deja sur disque."
        )
        imprimer_erreur(f"\n{message_rapport}")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat, interruption=message_rapport)
        return 1

    if isinstance(erreur, SessionIntrouvable):
        imprimer_erreur(f"ECHEC : {erreur}")
        imprimer_erreur("Sessions disponibles :")
        for session_disponible in erreur.sessions_disponibles:
            imprimer_erreur(f"  - {session_disponible.libelle}")
        return 1

    if isinstance(erreur, SelecteurSessionsIllisible):
        imprimer_erreur(f"ECHEC : {erreur}")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

    if isinstance(erreur, SessionExpiree):
        sessions_prevues = contexte.get("sessions_prevues")
        if contexte.get("archivage_commence"):
            # Toutes les sessions visees ont ete enumerees, et archiver() a
            # deja commence a ecrire sur disque : la reprise par manifeste
            # ne retelecharge jamais ce qui est deja la.
            imprimer_erreur(
                "\nSession expiree pendant l'archivage. Reconnectez-vous puis "
                "relancez la meme commande : la reprise est idempotente, elle "
                "continue la ou elle s'est arretee."
            )
            cours_non_tentes = archiveur.resultat.cours_non_tentes if archiveur else 0
            cours_prevus = contexte.get("cours_enumeres", 0)
            message_rapport = (
                f"L'archivage a ete interrompu : {cours_non_tentes} cours sur {cours_prevus} "
                f"(repartis sur les {sessions_prevues} session(s) visee(s), toutes deja "
                "enumerees) n'ont jamais ete tentes."
            )
        elif sessions_prevues is None:
            # La resolution meme des sessions visees a echoue (le tout
            # premier appel reseau) : on ne sait pas combien de sessions
            # sont concernees, encore moins combien de cours.
            imprimer_erreur(
                "\nSession expiree avant meme de determiner les sessions visees. "
                "Aucun fichier n'a encore ete traite : reconnectez-vous puis "
                "relancez la meme commande, l'enumeration recommencera "
                "entierement depuis le debut."
            )
            message_rapport = (
                "La session a expire avant meme de savoir combien de sessions sont "
                "visees par cette commande. Aucune session ni aucun cours n'a ete tente."
            )
        else:
            # Une partie des sessions visees a ete enumeree, mais
            # l'archivage proprement dit n'a jamais commence : il ne
            # demarre qu'une fois TOUTES les sessions enumerees (voir la
            # docstring de cette fonction). Rien n'a donc ete ecrit, pour
            # aucune des sessions visees, enumeree ou non.
            sessions_enumerees = contexte.get("sessions_enumerees", 0)
            cours_enumeres = contexte.get("cours_enumeres", 0)
            imprimer_erreur(
                "\nSession expiree pendant l'enumeration des sessions. Aucun "
                "fichier n'a encore ete traite : reconnectez-vous puis relancez "
                "la meme commande, l'enumeration recommencera entierement "
                "depuis le debut."
            )
            message_rapport = (
                f"L'enumeration a ete interrompue : {sessions_enumerees} session(s) sur "
                f"{sessions_prevues} avaient ete entierement enumerees ({cours_enumeres} "
                "cours recenses), mais l'archivage n'avait commence pour aucune d'entre "
                "elles (il ne demarre qu'une fois toutes les sessions visees enumerees). "
                f"Les {sessions_prevues - sessions_enumerees} session(s) restante(s) n'ont "
                "meme pas pu etre enumerees ; leur nombre de cours est inconnu. Aucun des "
                f"{sessions_prevues} session(s) visee(s) n'a donc ete archive."
            )
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat, interruption=message_rapport)
        return 3

    if erreur is not None:
        imprimer_erreur(f"ECHEC inattendu : {erreur}")
        if archiveur is not None:
            _ecrire_rapport_final(destination, archiveur.resultat)
        return 1

    resultat = contexte["resultat"]
    controle = verifier(destination)
    _fusionner_verification(resultat, controle)
    chemin_rapport = _ecrire_rapport_final(
        destination, resultat, interruption=_message_interruption_annulation(resultat)
    )
    imprimer(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    if chemin_rapport:
        imprimer(f"Echecs : {len(resultat.echecs)}  -> {chemin_rapport}")
    else:
        imprimer(f"Echecs : {len(resultat.echecs)}  (rapport non ecrit)")
    imprimer(
        f"Verification : {controle['inscrits']} inscrits, "
        f"{len(controle['manquants'])} manquants, "
        f"{len(controle['taille_incorrecte'])} de taille incorrecte"
    )
    if sur_fin is not None:
        sur_fin(resultat, controle, chemin_rapport)
    return _code_de_sortie(resultat)


def _session(
    libelle: str,
    destination: Path,
    session=None,
    fabrique_ena=Ena,
    imprimer=print,
    imprimer_erreur=None,
    annulation=None,
    sur_fin=None,
) -> int:
    """Archive tous les cours d'une session, en console ou depuis l'interface
    graphique.

    Le libelle demande est compare a ceux du selecteur avec une tolerance
    raisonnable sur la casse, les espaces de tete et de fin, et les accents
    (voir _normaliser_libelle_session) : il sera tape a la main. Si aucune
    session ne correspond, resoudre() leve SessionIntrouvable avec la liste
    reelle des sessions disponibles, affichee par _archiver_plusieurs_sessions.

    `imprimer`, `imprimer_erreur`, `annulation` et `sur_fin` sont simplement
    transmis a _archiver_plusieurs_sessions -- voir sa docstring et celle de
    _un_seul_cours pour leur contrat complet.
    """

    def resoudre(ena):
        disponibles = ena.sessions_disponibles()
        cible = _normaliser_libelle_session(libelle)
        for candidate in disponibles:
            if _normaliser_libelle_session(candidate.libelle) == cible:
                return [candidate]
        raise SessionIntrouvable(libelle, disponibles)

    return _archiver_plusieurs_sessions(
        resoudre,
        destination,
        session,
        fabrique_ena,
        imprimer=imprimer,
        imprimer_erreur=imprimer_erreur,
        annulation=annulation,
        sur_fin=sur_fin,
    )


def _tout(
    destination: Path,
    session=None,
    fabrique_ena=Ena,
    imprimer=print,
    imprimer_erreur=None,
    annulation=None,
    sur_fin=None,
) -> int:
    """Archive toutes les sessions, de la plus ancienne a la plus recente, en
    console ou depuis l'interface graphique.

    Session.code est de la forme AAAASS (annee puis mois de debut) : trier
    dessus rend directement l'ordre chronologique, sans avoir a interpreter
    le libelle affiche.

    `imprimer`, `imprimer_erreur`, `annulation` et `sur_fin` sont simplement
    transmis a _archiver_plusieurs_sessions -- voir sa docstring et celle de
    _un_seul_cours pour leur contrat complet.
    """

    def resoudre(ena):
        return sorted(ena.sessions_disponibles(), key=lambda s: s.code)

    return _archiver_plusieurs_sessions(
        resoudre,
        destination,
        session,
        fabrique_ena,
        imprimer=imprimer,
        imprimer_erreur=imprimer_erreur,
        annulation=annulation,
        sur_fin=sur_fin,
    )


def _verifier_mode(destination: Path, imprimer=print) -> int:
    """Confronte une archive deja sur disque a son manifeste, en console.

    Ne se connecte jamais a monPortail et n'ouvre aucun navigateur : pense
    pour rester utilisable des mois apres l'archivage, y compris apres la
    fermeture de la plateforme le 1er novembre 2026, quand plus aucun autre
    mode ne le sera.
    """
    if not destination.is_dir():
        print(f"ECHEC : dossier introuvable : {destination}", file=sys.stderr)
        return 1

    controle = verifier(destination)
    imprimer(
        f"{controle['inscrits']} inscrits, {len(controle['manquants'])} manquants, "
        f"{len(controle['taille_incorrecte'])} de taille incorrecte"
    )
    for relatif in controle["manquants"]:
        imprimer(f"  manquant : {relatif}")
    for relatif in controle["taille_incorrecte"]:
        imprimer(f"  taille incorrecte : {relatif}")

    if controle["manquants"] or controle["taille_incorrecte"]:
        return 1
    return 0


def _zip_mode(destination: Path, imprimer=print) -> int:
    """Compresse une archive deja sur disque en un fichier .zip, en console.

    Ne telecharge rien et ne se connecte jamais a monPortail. Le fichier
    compresse est ecrit a cote du dossier archive, jamais dedans, pour ne
    jamais avoir a s'exclure de son propre contenu.
    """
    if not destination.is_dir():
        print(f"ECHEC : dossier introuvable : {destination}", file=sys.stderr)
        return 1

    cible = destination.parent / f"{destination.name}.zip"
    try:
        creer_zip(destination, cible)
    except OSError as erreur:
        print(f"ECHEC : {erreur}", file=sys.stderr)
        return 1

    imprimer(f"Archive compressee : {cible}")
    return 0


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
    groupe_mode.add_argument(
        "--verifier",
        action="store_true",
        help=(
            "confronte une archive existante (--destination) a son manifeste, "
            "sans rien telecharger ni ouvrir de navigateur"
        ),
    )
    groupe_mode.add_argument(
        "--zip",
        dest="zip_",
        action="store_true",
        help="compresse une archive existante (--destination) en fichier .zip, sans rien telecharger",
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

        if arguments.verifier:
            return _verifier_mode(arguments.destination)

        if arguments.zip_:
            return _zip_mode(arguments.destination)
    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.", file=sys.stderr)
        return 130

    try:
        from extracteur.ui import lancer
    except ImportError as erreur:
        # Tkinter fait partie de la bibliotheque standard mais reste un paquet
        # separe sur plusieurs distributions Linux (python3-tk). Sans lui, la
        # fenetre est indisponible, mais tous les modes console restent
        # parfaitement utilisables : on le dit plutot que de deverser une
        # trace d'appels.
        print(
            f"Interface graphique indisponible ({erreur}).\n"
            "Les modes console restent utilisables : --tout, "
            '--session "Automne 2022", --un-seul-cours <idSite>, --lister.',
            file=sys.stderr,
        )
        return 1

    lancer(arguments.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
