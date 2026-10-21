"""Entry point of the terminal executable (Archiveur-monPortail-terminal.exe)."""

import os
import sys
from pathlib import Path


def _prepare_embedded_browser() -> None:
    from extracteur.navigateur_embarque import preparer_navigateur_integre

    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    try:
        folder = preparer_navigateur_integre(Path(sys.executable), Path(local) / "Archiveur monPortail", annoncer=print)
    except Exception as error:  # noqa: BLE001
        print(f"Impossible de préparer le navigateur intégré dans {local}\\Archiveur monPortail :\n{error}\n"
              "Vérifie l'espace disque disponible (environ 500 Mo), puis relance.")
        input("\nAppuie sur Entrée pour fermer.")
        raise SystemExit(1)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(folder)

    # Same driver redirection as the window launcher: it must happen before Playwright is imported.
    import playwright._impl._driver as driver

    node = folder / "driver" / "node.exe"
    cli = folder / "driver" / "package" / "cli.js"
    driver.compute_driver_executable = lambda: (str(node), str(cli))


frozen = getattr(sys, "frozen", False)
if frozen:
    _prepare_embedded_browser()

from monportail.__main__ import main  # noqa: E402

if __name__ == "__main__":
    code = main()
    if frozen:
        input("\nAppuie sur Entrée pour fermer.")
    raise SystemExit(code)
