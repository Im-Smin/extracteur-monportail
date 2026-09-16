"""Empeche la mise en veille pendant un archivage.

Pourquoi : un archivage complet dure de longues minutes, parfois plus d'une
heure. Si la machine s'endort, le reseau tombe et la navigation en cours
expire. Constate en conditions reelles : une seule navigation expiree a
suffi a faire perdre dix-sept cours sur trente-neuf (voir Ena._visiter, qui
repare desormais cette cascade -- mais mieux vaut ne pas la declencher).

Pourquoi PAS `powercfg` : modifier le reglage de veille du systeme est un
changement GLOBAL qu'il faut penser a defaire. Si le programme est tue, ou
plante, ou que la machine redemarre au mauvais moment, le reglage reste
modifie -- exactement le probleme qu'on cherchait a eviter.

SetThreadExecutionState ne modifie aucun reglage : elle declare seulement
« ce fil travaille, ne dors pas tant qu'il tourne ». Windows libere cet etat
tout seul quand le processus se termine, quelle qu'en soit la raison. Rien a
restaurer, rien qui puisse fuir, et aucun droit d'administrateur.

Ce que cela empeche, et ce que cela n'empeche PAS :
  - empeche la mise en veille du systeme et l'extinction de l'ecran ;
  - n'empeche PAS un verrouillage manuel (Win+L) ni un verrouillage impose
    par une politique d'entreprise. Playwright desactivant deja le bridage
    des onglets en arriere-plan (--disable-background-timer-throttling et
    voisins), un ecran verrouille reste toutefois nettement moins genant
    qu'une veille, qui coupe le reseau.
"""

import contextlib
import sys

# Constantes de l'API Windows (winbase.h). ES_CONTINUOUS rend l'etat
# persistant jusqu'a un nouvel appel ; sans lui, la declaration ne vaudrait
# que pour l'instant de l'appel et la machine s'endormirait quand meme.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

ETAT_TRAVAIL_EN_COURS = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
ETAT_NORMAL = ES_CONTINUOUS

MESSAGE_ACTIVE = (
    "Mise en veille suspendue pendant l'archivage (aucun reglage systeme "
    "n'est modifie : Windows retablit tout seul a la fin)."
)
MESSAGE_INDISPONIBLE = (
    "Impossible de suspendre la mise en veille sur ce systeme. Si la machine "
    "s'endort pendant l'archivage, la navigation en cours echouera -- relancer "
    "reprendra la ou l'archivage s'est arrete."
)


def _api_windows():
    """Rend la fonction SetThreadExecutionState, ou None hors de Windows.

    Import de ctypes fait ici, pas en tete de module : sur un systeme non
    Windows, ctypes.windll n'existe pas, et un acces en tete de module ferait
    echouer l'import de tout le programme.
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        return ctypes.windll.kernel32.SetThreadExecutionState
    except (ImportError, AttributeError, OSError):
        return None


@contextlib.contextmanager
def empecher_la_veille(regler=None, imprimer=print):
    """Suspend la mise en veille le temps du bloc, puis rend la main a Windows.

    `regler` est injectable pour les tests : la vraie API n'est resolue que si
    rien n'est fourni. Rendre None (systeme non Windows, ou API indisponible)
    ne fait pas echouer l'archivage -- il tourne simplement sans ce filet, et
    le dit.

    ATTENTION : l'etat declare vaut pour le FIL appelant. Ce gestionnaire doit
    donc envelopper le travail lui-meme, dans le fil qui archive, et non le
    programme vu depuis le fil principal.

    Ne laisse jamais l'etat pose derriere lui : le retablissement est dans un
    finally, et Windows le libere de toute facon a la fin du processus.
    """
    if regler is None:
        regler = _api_windows()

    if regler is None:
        imprimer(MESSAGE_INDISPONIBLE)
        yield False
        return

    try:
        precedent = regler(ETAT_TRAVAIL_EN_COURS)
    except OSError as erreur:
        imprimer(f"{MESSAGE_INDISPONIBLE} ({erreur})")
        yield False
        return

    # La fonction rend l'etat precedent, ou zero si elle a echoue. Un echec
    # ne justifie pas d'interrompre un archivage d'une heure : on le dit et
    # on continue sans le filet.
    if not precedent:
        imprimer(MESSAGE_INDISPONIBLE)
        yield False
        return

    imprimer(MESSAGE_ACTIVE)
    try:
        yield True
    finally:
        # Jamais de garde ici : meme si le retablissement echoue, Windows
        # libere l'etat a la fin du processus. Mais le tenter tout de suite
        # rend la veille des la fin de l'archivage, sans attendre la
        # fermeture de la fenetre.
        with contextlib.suppress(OSError):
            regler(ETAT_NORMAL)
