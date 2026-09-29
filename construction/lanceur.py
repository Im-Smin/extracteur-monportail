"""Point d'entree de l'executable Windows (Archiveur monPortail.exe).

L'executable est un fichier unique, utilisable quel que soit l'endroit ou
il est range : voir extracteur/navigateur_embarque.py pour le choix de ce
format et la preparation de Chromium, decompresse au premier lancement dans
%LOCALAPPDATA%\\Archiveur monPortail puis reutilise.

PLAYWRIGHT_BROWSERS_PATH doit pointer vers ce dossier avant tout import de
Playwright, sinon celui-ci chercherait le navigateur dans
%LOCALAPPDATA%\\ms-playwright, ou il n'est pas.

Le pilote de Playwright est, lui aussi, pris dans ce dossier : voir
_preparer_navigateur_embarque.

Hors executable (python construction/lanceur.py), rien n'est modifie : le
navigateur installe habituellement par `playwright install` reste utilise.
"""

import os
import sys
from pathlib import Path


class _Annonce:
    """Petite fenetre d'attente, creee seulement si une preparation a
    lieu : les lancements suivants n'en affichent aucune."""

    def __init__(self):
        self.racine = None

    def __call__(self, message: str) -> None:
        import tkinter

        self.racine = tkinter.Tk()
        self.racine.title("Archiveur monPortail")
        tkinter.Label(self.racine, text=message, padx=24, pady=20).pack()
        self.racine.update()

    def fermer(self) -> None:
        if self.racine is not None:
            self.racine.destroy()
            self.racine = None


def _erreur(message: str) -> None:
    try:
        import tkinter
        from tkinter import messagebox

        racine = tkinter.Tk()
        racine.withdraw()
        messagebox.showerror("Archiveur monPortail", message)
        racine.destroy()
    except Exception:
        pass
    raise SystemExit(1)


def _preparer_navigateur_embarque() -> None:
    from extracteur.navigateur_embarque import preparer_navigateur_integre

    locale = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    annonce = _Annonce()
    try:
        dossier = preparer_navigateur_integre(
            Path(sys.executable), Path(locale) / "Archiveur monPortail", annoncer=annonce
        )
    except Exception as erreur:
        annonce.fermer()
        _erreur(
            "Impossible de preparer le navigateur integre dans "
            f"{locale}\\Archiveur monPortail :\n\n{erreur}\n\n"
            "Verifiez l'espace disque disponible (environ 500 Mo), puis relancez."
        )
    annonce.fermer()
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(dossier)

    # Le pilote de Playwright (node.exe et son paquet) voyage avec Chromium
    # dans l'archive integree, et non dans le paquet redeploye a chaque
    # lancement (~18 s de demarrage evitees). Playwright le situe par cette
    # seule fonction, importee par nom dans playwright._impl._transport :
    # elle doit donc etre remplacee AVANT tout import de Playwright.
    import playwright._impl._driver as pilote

    node = dossier / "driver" / "node.exe"
    cli = dossier / "driver" / "package" / "cli.js"
    pilote.compute_driver_executable = lambda: (str(node), str(cli))


if getattr(sys, "frozen", False):
    _preparer_navigateur_embarque()

from extracteur.__main__ import main  # noqa: E402 -- apres la variable, voulu

if __name__ == "__main__":
    raise SystemExit(main())
