import argparse
import os
import sys

from monportail.app import App
from monportail.ui import console


def main() -> int:
    if os.name == "nt":
        os.system("")  # enables ANSI colours in legacy Windows consoles
    parser = argparse.ArgumentParser(prog="python -m monportail", description="Archive tes anciens sites de cours monPortail.")
    parser.add_argument("--destination", help="dossier de l'archive (sinon demandé)")
    parser.add_argument("--paralleles", type=int, default=2, help="vidéos téléchargées en même temps (défaut : 2)")
    parser.add_argument("--fenetre-visible", action="store_true", help="garder la fenêtre du navigateur à l'écran")
    args = parser.parse_args()
    try:
        return App(args).run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Arrêté.[/]")
        return 130


if __name__ == "__main__":
    sys.exit(main())
