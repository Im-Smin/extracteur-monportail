"""Construit l'executable Windows en fichier unique.

A lancer avec le Python d'un environnement dedie a la construction, ou le
projet, PyInstaller et Chromium sont installes :

    python -m venv C:\\chemin\\venv
    C:\\chemin\\venv\\Scripts\\python -m pip install . pyinstaller
    set PLAYWRIGHT_BROWSERS_PATH=0
    C:\\chemin\\venv\\Scripts\\python -m playwright install chromium
    C:\\chemin\\venv\\Scripts\\python construction\\construire.py --sortie C:\\chemin\\sortie

PLAYWRIGHT_BROWSERS_PATH=0 installe Chromium A L'INTERIEUR du paquet
Playwright, d'ou ce script le reprend pour en faire l'archive embarquee
nav-chromium-XXXX.zip, inseree dans l'executable apres sa construction
(voir extracteur/navigateur_embarque.py).

La sortie doit etre hors de tout dossier synchronise (OneDrive...) : c'est
pourquoi --sortie est obligatoire, et jamais le depot.
"""

import argparse
import sys
import zipfile
from pathlib import Path

import PyInstaller.__main__
from PyInstaller.archive.readers import CArchiveReader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extracteur.navigateur_embarque import _entete_archive_integree, integrer_archive  # noqa: E402

ICI = Path(__file__).resolve().parent
RACINE = ICI.parent
NOM = "Archiveur monPortail"


def navigateurs_installes() -> list[Path]:
    """Dossiers a embarquer : Chromium et ses utilitaires, jamais le
    « headless shell », qui ne sert qu'au mode sans fenetre que l'outil
    n'utilise jamais (~270 Mo de moins)."""
    import playwright

    racine = Path(playwright.__file__).parent / "driver" / "package" / ".local-browsers"
    dossiers = [
        d for d in sorted(racine.glob("*"))
        if d.is_dir() and not d.name.startswith("chromium_headless_shell")
    ]
    if not any(d.name.startswith("chromium-") for d in dossiers):
        sys.exit(
            f"Chromium absent de {racine}.\n"
            "Installez-le DANS le paquet Playwright avant de construire :\n"
            "  set PLAYWRIGHT_BROWSERS_PATH=0\n"
            f"  {sys.executable} -m playwright install chromium"
        )
    return dossiers


def archiver_navigateurs(dossiers: list[Path], travail: Path) -> Path:
    """Archive a integrer : Chromium et ses utilitaires, plus le pilote de
    Playwright (driver/ : node.exe et son paquet JavaScript), que le lanceur
    utilise depuis le cache (voir lanceur.py). Le nom porte les deux
    versions : l'un ne va jamais sans l'autre."""
    import playwright
    from importlib.metadata import version as version_de

    chromium = next(d.name for d in dossiers if d.name.startswith("chromium-"))
    destination = travail / f"nav-{chromium}-pw{version_de('playwright')}.zip"
    pilote = Path(playwright.__file__).parent / "driver"
    travail.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for dossier in dossiers:
            for fichier in sorted(dossier.rglob("*")):
                if fichier.is_file():
                    archive.write(fichier, fichier.relative_to(dossier.parent))
        for fichier in sorted(pilote.rglob("*")):
            if fichier.is_file() and ".local-browsers" not in fichier.parts:
                archive.write(fichier, Path("driver") / fichier.relative_to(pilote))
        noms = archive.namelist()
    if "driver/node.exe" not in noms or "driver/package/cli.js" not in noms:
        sys.exit("le pilote de Playwright manque dans l'archive integree.")
    print(f"archive du navigateur : {destination.name} ({destination.stat().st_size / 1e6:.0f} Mo)")
    return destination


def verifier(exe: Path, archive_navigateur: Path) -> None:
    """Refuse un executable qui partirait sans navigateur, ou avec un
    navigateur deploye fichier par fichier : il ne planterait qu'au premier
    lancement, chez l'utilisateur."""
    if not exe.is_file():
        sys.exit(f"executable introuvable : {exe}")
    # Le paquet PyInstaller doit rester lisible apres l'insertion : c'est la
    # meme lecture, depuis la fin du fichier, que celle de son chargeur.
    contenu = list(CArchiveReader(str(exe)).toc)
    if any(".local-browsers" in nom or "chromium_headless_shell" in nom or "playwright\driver" in nom
           or "playwright/driver" in nom for nom in contenu):
        sys.exit("le navigateur ou le pilote a ete embarque dans le paquet redeploye a chaque lancement.")
    with open(exe, "rb") as fichier:
        _, longueur, nom = _entete_archive_integree(fichier)
    if nom != archive_navigateur.stem or longueur != archive_navigateur.stat().st_size:
        sys.exit(f"archive du navigateur mal integree : {nom}, {longueur} octets")
    print(f"verifie : {exe.name} ({exe.stat().st_size / 1e6:.0f} Mo), {nom} integree ({longueur / 1e6:.0f} Mo)")


def main() -> None:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--sortie", type=Path, required=True, help="dossier de sortie, hors OneDrive")
    arguments = analyseur.parse_args()
    sortie = arguments.sortie.resolve()
    if RACINE in sortie.parents or sortie == RACINE:
        sys.exit("--sortie ne doit pas etre dans le depot.")

    archive = archiver_navigateurs(navigateurs_installes(), sortie / "travail")
    PyInstaller.__main__.run([
        str(ICI / "archiveur.spec"),
        "--noconfirm",
        "--clean",
        "--distpath", str(sortie / "pyinstaller"),
        "--workpath", str(sortie / "travail"),
    ])
    exe = sortie / f"{NOM}.exe"
    integrer_archive(sortie / "pyinstaller" / f"{NOM}.exe", archive, exe)
    verifier(exe, archive)


if __name__ == "__main__":
    main()
