"""Ecriture disque : flux, empreinte, et reprise par simple presence du fichier."""

import hashlib
from collections.abc import Iterable
from pathlib import Path

from extracteur.nommage import chemin_long


def deja_present(destination: Path, taille_attendue: int | None) -> bool:
    """Vrai si le fichier est deja la et complet.

    Un fichier portant son nom final est forcement complet : l'ecriture passe
    par un .part renomme seulement a la fin.
    """
    if not destination.exists():
        return False

    taille_reelle = destination.stat().st_size
    if taille_attendue is None:
        return taille_reelle > 0
    return taille_reelle == taille_attendue


def ecrire_flux(destination: Path, morceaux: Iterable[bytes]) -> tuple[int, str]:
    """Ecrit un flux d'octets et retourne (taille, sha256).

    Rien n'est charge en memoire : une capsule video de 800 Mo ne doit pas faire
    gonfler le processus.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_suffix(destination.suffix + ".part")

    empreinte = hashlib.sha256()
    taille = 0

    try:
        with open(chemin_long(partiel), "wb") as sortie:
            for morceau in morceaux:
                sortie.write(morceau)
                empreinte.update(morceau)
                taille += len(morceau)
    except BaseException:
        partiel.unlink(missing_ok=True)
        raise

    # Renommage atomique : c'est ce qui rend la reprise fiable.
    partiel.replace(destination)
    return taille, empreinte.hexdigest()
