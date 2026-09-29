"""Chromium livre avec l'executable Windows, decompresse une seule fois.

L'executable est un fichier unique : il se deploie a chaque lancement dans un
dossier temporaire court, ce qui lui permet de fonctionner quel que soit
l'endroit ou l'utilisateur l'a range. Un format « dossier » a ete essaye et
ecarte : place trop profondement, Windows refusait d'en charger les
bibliotheques (limite de 260 caracteres, « DLL load failed ... The filename
or extension is too long »), avant meme que le code de l'outil ne demarre.

Chromium et le pilote de Playwright (~530 Mo decompresses) n'est pas redeploye a chaque lancement : il
est livre en une seule archive, decompressee au PREMIER lancement dans un
cache au chemin court et fixe (%LOCALAPPDATA%\\Archiveur monPortail), puis
reutilise. Le nom de l'archive porte la version de Chromium : une mise a jour
de l'Archiveur installe la sienne et retire l'ancienne.
"""

import io
import shutil
import struct
import zipfile
from pathlib import Path

# Ecrite en dernier : un dossier sans elle est une decompression interrompue
# (fermeture, panne), jamais un navigateur utilisable.
MARQUE_PRET = ".pret"
PREFIXE = "nav-"

# --- Archive du navigateur integree a l'executable ---
#
# Rangee parmi les donnees de PyInstaller, l'archive (~200 Mo) etait
# redeployee dans le dossier temporaire A CHAQUE lancement, puis analysee par
# l'antivirus : 17 s de demarrage mesurees, a chaque fois. Elle est donc
# inseree dans l'executable, juste avant le paquet PyInstaller, la ou son
# chargeur ne regarde pas : il situe son paquet en partant de la fin du
# fichier (debut = fin du « cookie » - longueur du paquet, voir
# PyInstaller.archive.readers.CArchiveReader), et ignore ce qui precede.
# Elle n'est lue qu'au premier lancement.
MAGIQUE_PYINSTALLER = b"MEI\014\013\012\013\016"
FORMAT_COOKIE = "!8sIIII64s"
MAGIQUE_NAVIGATEUR = b"ARCHNAV1"
FORMAT_ENTETE = "<8sQQ64s"  # magique, debut, longueur, nom de l'archive
TAILLE_BLOC = 1024 * 1024


def _debut_paquet_pyinstaller(fichier) -> int:
    fichier.seek(0, 2)
    taille = fichier.tell()
    fin_lue = max(0, taille - TAILLE_BLOC)
    fichier.seek(fin_lue)
    position = fichier.read().rfind(MAGIQUE_PYINSTALLER)
    if position == -1:
        raise ValueError("paquet PyInstaller introuvable dans l'executable")
    fichier.seek(fin_lue + position)
    cookie = fichier.read(struct.calcsize(FORMAT_COOKIE))
    _, longueur, *_ = struct.unpack(FORMAT_COOKIE, cookie)
    return fin_lue + position + struct.calcsize(FORMAT_COOKIE) - longueur


def _copier(source, destination, longueur: int) -> None:
    while longueur > 0:
        bloc = source.read(min(TAILLE_BLOC, longueur))
        if not bloc:
            raise ValueError("executable tronque")
        destination.write(bloc)
        longueur -= len(bloc)


def integrer_archive(executable: Path, archive: Path, destination: Path) -> None:
    """Ecrit `destination` : l'executable PyInstaller `executable`, avec
    `archive` inseree juste avant son paquet (a la construction)."""
    nom = Path(archive).stem.encode("utf-8")
    with open(executable, "rb") as exe, open(archive, "rb") as nav, open(destination, "wb") as sortie:
        debut_paquet = _debut_paquet_pyinstaller(exe)
        exe.seek(0)
        _copier(exe, sortie, debut_paquet)
        debut_archive = sortie.tell()
        shutil.copyfileobj(nav, sortie, TAILLE_BLOC)
        longueur = sortie.tell() - debut_archive
        sortie.write(struct.pack(FORMAT_ENTETE, MAGIQUE_NAVIGATEUR, debut_archive, longueur, nom))
        shutil.copyfileobj(exe, sortie, TAILLE_BLOC)


def _entete_archive_integree(fichier) -> tuple[int, int, str]:
    taille_entete = struct.calcsize(FORMAT_ENTETE)
    fichier.seek(_debut_paquet_pyinstaller(fichier) - taille_entete)
    magique, debut, longueur, nom = struct.unpack(FORMAT_ENTETE, fichier.read(taille_entete))
    if magique != MAGIQUE_NAVIGATEUR:
        raise ValueError("aucune archive du navigateur integree a l'executable")
    return debut, longueur, nom.rstrip(b"\0").decode("utf-8")


class _Tranche(io.RawIOBase):
    """Vue en lecture seule sur une portion d'un fichier : l'archive integree
    est lue en place dans l'executable. La recopier d'abord dans un fichier
    temporaire coutait ~60 s au premier lancement (240 Mo ecrits, puis
    analyses par l'antivirus), avant meme que le message d'attente ne
    s'affiche."""

    def __init__(self, fichier, debut: int, longueur: int):
        self._fichier = fichier
        self._debut = debut
        self._longueur = longueur
        self._position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._position

    def seek(self, decalage, origine=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: self._longueur}[origine]
        self._position = max(0, base + decalage)
        return self._position

    def readinto(self, tampon):
        a_lire = min(len(tampon), self._longueur - self._position)
        if a_lire <= 0:
            return 0
        self._fichier.seek(self._debut + self._position)
        donnees = self._fichier.read(a_lire)
        tampon[: len(donnees)] = donnees
        self._position += len(donnees)
        return len(donnees)


def preparer_navigateur_integre(executable: Path, cache: Path, annoncer=print) -> Path:
    """Comme preparer_navigateur, pour l'archive integree a `executable`,
    lue en place : rien n'est lu dans l'executable quand le cache est deja
    pret, et rien n'est recopie quand il ne l'est pas."""
    with open(executable, "rb") as exe:
        debut, longueur, nom = _entete_archive_integree(exe)
        return preparer_navigateur(_Tranche(exe, debut, longueur), cache, annoncer=annoncer, nom=nom)


def preparer_navigateur(archive, cache: Path, annoncer=print, nom: str | None = None) -> Path:
    """Rend le dossier a donner a Playwright (PLAYWRIGHT_BROWSERS_PATH), en
    decompressant `archive` (chemin, ou fichier ouvert avec `nom`) dans
    `cache` si ce n'est pas deja fait.

    `annoncer` recoit un message avant une decompression -- jamais quand le
    cache est deja pret : l'utilisateur ne voit l'attente qu'une fois.
    """
    nom = nom or Path(archive).stem
    cache = Path(cache)
    dossier = cache / nom
    if (dossier / MARQUE_PRET).exists():
        return dossier

    annoncer(
        "Premiere utilisation : preparation du navigateur integre "
        "(une seule fois, environ une minute)..."
    )
    cache.mkdir(parents=True, exist_ok=True)
    partiel = cache / f"{nom}.partiel"
    shutil.rmtree(partiel, ignore_errors=True)
    with zipfile.ZipFile(archive) as source:
        racine = partiel.resolve()
        for element in source.namelist():
            cible = (partiel / element).resolve()
            if racine != cible and racine not in cible.parents:
                raise ValueError(f"chemin hors du cache dans l'archive du navigateur : {element}")
        source.extractall(partiel)
    (partiel / MARQUE_PRET).write_text("ok", encoding="utf-8")

    shutil.rmtree(dossier, ignore_errors=True)
    partiel.replace(dossier)

    for ancien in cache.glob(f"{PREFIXE}*"):
        if ancien != dossier and ancien.is_dir():
            shutil.rmtree(ancien, ignore_errors=True)
    return dossier
