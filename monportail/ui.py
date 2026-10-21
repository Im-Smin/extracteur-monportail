"""Console, prompts and naming helpers shared by the terminal interface."""

import shutil
from pathlib import Path

import questionary
from rich.console import Console

from extracteur.archiveur import _segment

console = Console(highlight=False)

DEFAULT_FOLDER = Path.home() / "Documents" / "Archive monPortail"
LAST_LEGACY_SESSION = "202605"

STYLE = questionary.Style([
    ("qmark", "fg:#e5a50a bold"),
    ("pointer", "fg:#e5a50a bold"),
    ("highlighted", "fg:#e5a50a bold"),
    ("selected", "fg:#3fb950"),
])


def ask(question):
    # Playwright already owns the main thread's event loop, so prompts run in their own thread.
    answer = question.application.run(in_thread=True)
    if answer is None:
        raise KeyboardInterrupt
    return answer


def pick_folder(title: str, default: Path) -> Path:
    choice = ask(questionary.select(
        title,
        choices=[
            questionary.Choice("Ouvrir une fenêtre pour choisir le dossier", "dialog"),
            questionary.Choice(f"Utiliser {default}", "default"),
            questionary.Choice("Taper le chemin", "type"),
        ],
        style=STYLE,
    ))
    if choice == "default":
        return default
    if choice == "dialog":
        try:
            import tkinter
            from tkinter import filedialog

            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title=title, initialdir=str(default.parent if default.parent.exists() else Path.home()))
            root.destroy()
            if path:
                return Path(path)
            console.print("[yellow]Aucun dossier choisi.[/]")
        except Exception as error:  # noqa: BLE001
            console.print(f"[yellow]Fenêtre indisponible ({error}).[/]")
    typed = ask(questionary.path("Chemin du dossier :", default=str(default), only_directories=True, style=STYLE))
    return Path(typed.strip().strip('"'))


def safe_name(text: str) -> str:
    """Same folder naming as the original archiver, so videos land next to documents."""
    return _segment(" ".join((text or "").split()) or "_")


def free_space(folder: Path) -> int | None:
    path = folder
    while not path.exists() and path.parent != path:
        path = path.parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def human_size(size: int | None) -> str:
    if not size:
        return "?"
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if value < 1024 or unit == "To":
            return f"{value:.0f} {unit}" if unit in ("o", "Ko") else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return f"{size} o"
