"""Assainissement des noms de fichiers pour Windows.

Windows refuse certains caracteres, reserve certains noms et limite les chemins
a 260 caracteres. Ce module traite ces trois pieges avant toute ecriture disque.
"""

import re
from pathlib import Path

CARACTERES_INTERDITS = r'<>:"/\|?*'

NOMS_RESERVES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

LONGUEUR_MAX_SEGMENT = 80
PREFIXE_CHEMIN_LONG = "\\\\?\\"


def nom_sur(nom: str) -> str:
    """Rend un nom de fichier ou de dossier acceptable par Windows."""
    assaini = "".join("-" if c in CARACTERES_INTERDITS else c for c in nom)

    # Les caracteres de controle passent parfois dans les titres extraits du HTML.
    assaini = re.sub(r"[\x00-\x1f]", "", assaini)

    # Windows supprime silencieusement les points et espaces de fin : on le fait
    # nous-memes pour que le nom sur disque soit celui qu'on croit avoir ecrit.
    assaini = assaini.rstrip(". ")

    if not assaini.strip():
        return "_"

    racine = assaini.split(".")[0]
    if racine.upper() in NOMS_RESERVES:
        assaini = f"_{assaini}"

    return assaini


def tronquer(nom: str, longueur_max: int = LONGUEUR_MAX_SEGMENT) -> str:
    """Tronque un nom en preservant son extension."""
    if longueur_max <= 0:
        raise ValueError(f"longueur_max doit etre > 0, recu {longueur_max}")

    if len(nom) <= longueur_max:
        return nom

    chemin = Path(nom)
    extension = chemin.suffix

    # Une extension plus longue que la limite n'en est pas une.
    if len(extension) >= longueur_max:
        return nom[:longueur_max]

    return chemin.stem[: longueur_max - len(extension)] + extension


def nom_unique(dossier: Path, nom: str) -> str:
    """Suffixe le nom tant qu'un fichier du meme nom existe deja."""
    if not (dossier / nom).exists():
        return nom

    chemin = Path(nom)
    compteur = 2
    while True:
        candidat = f"{chemin.stem} ({compteur}){chemin.suffix}"
        if not (dossier / candidat).exists():
            return candidat
        compteur += 1


def chemin_long(chemin: Path) -> str:
    """Rend le chemin utilisable au-dela de la limite de 260 caracteres.

    Sur Windows, Path.resolve() pose lui-meme le prefixe \\\\?\\ des qu'il le
    juge necessaire (selon la longueur du chemin et l'etat de
    LongPathsEnabled). Verifier la presence du prefixe avant de resoudre ne
    sert donc a rien : c'est apres resolution qu'il faut regarder, sous peine
    de poser un second prefixe sur un chemin qui en porte deja un.
    """
    texte = str(chemin.resolve())
    if texte.startswith(PREFIXE_CHEMIN_LONG):
        texte = texte[len(PREFIXE_CHEMIN_LONG):]
    return PREFIXE_CHEMIN_LONG + texte
