"""Politique de reessai et distinction des causes d'echec.

La regle qui compte : un jeton expire ne doit jamais consommer les cours
restants en erreurs. Il remonte en SessionExpiree pour que l'archiveur mette la
file en pause et demande une reconnexion.

Un 401 ne consomme pas le budget des trois tentatives reseau. Il declenche un
renouvellement et un rejeu immediatement. Seul le second 401 leve SessionExpiree.
"""

import time
from pathlib import Path

from extracteur.stockage import ecrire_flux

PAUSES = (2, 8)
STATUTS_SANS_REESSAI = (403, 404)
ERREURS_RESEAU = (ConnectionError, TimeoutError, OSError)


class ErreurPermanente(Exception):
    """Contenu inaccessible ou disparu : on consigne et on avance."""


class SessionExpiree(Exception):
    """La session d'authentification est morte : il faut se reconnecter."""


def telecharger(transport, url, destination: Path, renouveler=None, dormir=time.sleep):
    """Telecharge une ressource vers destination, avec reessais differencies."""
    tentatives_reseau = 0
    jeton_renouvele = False

    while True:
        try:
            reponse = transport(url)
        except ERREURS_RESEAU as erreur:
            if tentatives_reseau >= 2:
                raise ErreurPermanente(f"reseau : {erreur}") from erreur
            dormir(PAUSES[tentatives_reseau])
            tentatives_reseau += 1
            continue

        if reponse.statut == 200:
            return ecrire_flux(Path(destination), reponse.morceaux())

        if reponse.statut == 401:
            # Le jeton expire ne consomme pas le budget : un seul renouvellement.
            if renouveler is None or jeton_renouvele:
                raise SessionExpiree(url)
            renouveler()
            jeton_renouvele = True
            continue

        if reponse.statut in STATUTS_SANS_REESSAI:
            raise ErreurPermanente(f"HTTP {reponse.statut}")

        # Erreur serveur 5xx ou autre : compte dans le budget
        if tentatives_reseau >= 2:
            raise ErreurPermanente(f"HTTP {reponse.statut}")
        dormir(PAUSES[tentatives_reseau])
        tentatives_reseau += 1
