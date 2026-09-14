"""Politique de reessai et distinction des causes d'echec.

La regle qui compte : un jeton expire ne doit jamais consommer les cours
restants en erreurs. Il remonte en SessionExpiree pour que l'archiveur mette la
file en pause et demande une reconnexion.
"""

import time
from pathlib import Path

from extracteur.stockage import ecrire_flux

PAUSES = (2, 8, 30)
STATUTS_SANS_REESSAI = (403, 404)
ERREURS_RESEAU = (ConnectionError, TimeoutError, OSError)


class ErreurPermanente(Exception):
    """Contenu inaccessible ou disparu : on consigne et on avance."""


class SessionExpiree(Exception):
    """La session d'authentification est morte : il faut se reconnecter."""


def telecharger(transport, url, destination: Path, renouveler=None, dormir=time.sleep):
    """Telecharge une ressource vers destination, avec reessais differencies."""
    jeton_renouvele = False

    for tentative in range(3):
        try:
            reponse = transport(url)
        except ERREURS_RESEAU as erreur:
            if tentative == 2:
                raise ErreurPermanente(f"reseau : {erreur}") from erreur
            dormir(PAUSES[tentative])
            continue

        if reponse.statut == 200:
            return ecrire_flux(Path(destination), reponse.morceaux())

        if reponse.statut == 401:
            # Une seule chance : on rafraichit le jeton et on rejoue.
            if renouveler is None or jeton_renouvele:
                raise SessionExpiree(url)
            renouveler()
            jeton_renouvele = True
            continue

        if reponse.statut in STATUTS_SANS_REESSAI:
            raise ErreurPermanente(f"HTTP {reponse.statut}")

        if tentative == 2:
            raise ErreurPermanente(f"HTTP {reponse.statut}")
        dormir(PAUSES[tentative])

    raise ErreurPermanente("tentatives epuisees")
