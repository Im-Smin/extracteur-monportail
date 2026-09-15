"""Confrontation du disque au manifeste, puis mise en archive ZIP.

Derniere etape avant de declarer une archive terminee : verifier() recompte
les fichiers reellement presents face a ce que le manifeste dit avoir ecrit,
et creer_zip() rassemble le tout dans un fichier unique.
"""

import os
import zipfile
from pathlib import Path

from extracteur.manifeste import Manifeste
from extracteur.nommage import PREFIXE_CHEMIN_LONG, chemin_long


def _taille_attendue(ligne: dict) -> int | None:
    """Parse la colonne taille d'une entree de manifeste, ou None si illisible.

    Meme precaution que archiveur._taille_manifeste : le manifeste est
    toujours ecrit par Manifeste.ajouter en usage normal, mais reste un
    fichier CSV ouvrable et modifiable a la main. Une colonne vide ou non
    numerique ne doit jamais faire planter la verification -- elle rend
    seulement cette taille incontrolable, jamais une anomalie en soi.
    """
    try:
        return int(ligne["taille"])
    except (KeyError, TypeError, ValueError):
        return None


def verifier(racine: Path) -> dict:
    """Recompte les fichiers reels face au manifeste avant de declarer
    l'archive terminee.

    Rend un dictionnaire avec le nombre d'entrees inscrites au manifeste, la
    liste des chemins relatifs absents du disque, et celle dont la taille sur
    disque ne correspond pas a celle consignee. Une taille illisible n'entre
    ni dans l'une ni dans l'autre : elle est simplement hors de portee du
    controle, pas une anomalie constatee.
    """
    racine = Path(racine)
    entrees = Manifeste(racine).charger()

    manquants: list[str] = []
    taille_incorrecte: list[str] = []

    for relatif, ligne in entrees.items():
        chemin_str = chemin_long(racine / relatif)
        if not os.path.exists(chemin_str):
            manquants.append(relatif)
            continue

        taille_attendue = _taille_attendue(ligne)
        if taille_attendue is not None and os.stat(chemin_str).st_size != taille_attendue:
            taille_incorrecte.append(relatif)

    return {
        "inscrits": len(entrees),
        "manquants": manquants,
        "taille_incorrecte": taille_incorrecte,
    }


def _sans_prefixe_long(chemin: str) -> str:
    if chemin.startswith(PREFIXE_CHEMIN_LONG):
        return chemin[len(PREFIXE_CHEMIN_LONG) :]
    return chemin


def _normaliser(chemin) -> str:
    """Forme comparable d'un chemin, prefixe \\\\?\\ ou non, absolu ou non.

    Sert uniquement a reconnaitre si le fichier ZIP en cours d'ecriture se
    trouve lui-meme dans l'arborescence qu'on est en train de compresser.
    Purement textuel (os.path.abspath ne touche jamais le disque) : reste
    valide sur un chemin qui n'existe pas encore, comme la destination du
    ZIP au moment ou on la compare.
    """
    return os.path.normcase(os.path.abspath(_sans_prefixe_long(str(chemin))))


def creer_zip(racine: Path, destination: Path) -> Path:
    """Compresse toute l'archive en un seul fichier ZIP, arborescence
    relative preservee.

    Trois precautions, chacune couteuse a l'usage si on l'oublie :
    - chemin_long est applique a chaque operation disque (ouverture du ZIP,
      parcours, lecture des fichiers sources), au meme titre que dans
      stockage.ecrire_flux : un dossier archive de plusieurs mois depasse
      facilement la limite de 260 caracteres de Windows.
    - zipfile.ZipFile.write() lit chaque fichier source par blocs, jamais en
      entier : une archive de plusieurs gigaoctets ne fait jamais gonfler le
      processus.
    - si `destination` se trouve a l'interieur de `racine`, elle est exclue
      de son propre contenu. Sans cette garde, le ZIP s'ajouterait a
      lui-meme au fil de son ecriture et grossirait indefiniment.
    """
    racine = Path(racine)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    destination_normalisee = _normaliser(destination)
    racine_longue = chemin_long(racine)

    with zipfile.ZipFile(chemin_long(destination), "w", zipfile.ZIP_DEFLATED) as archive:
        for dossier_courant, sous_dossiers, fichiers in os.walk(racine_longue):
            sous_dossiers.sort()
            for nom in sorted(fichiers):
                chemin_absolu = os.path.join(dossier_courant, nom)
                if _normaliser(chemin_absolu) == destination_normalisee:
                    continue
                relatif = os.path.relpath(chemin_absolu, racine_longue).replace("\\", "/")
                archive.write(chemin_absolu, relatif)

    return destination
