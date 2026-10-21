"""Startup banner."""

from rich.align import Align
from rich.console import Console, Group
from rich.text import Text

WIDE = ['███╗   ███╗ ██████╗ ███╗   ██╗██████╗  ██████╗ ██████╗ ████████╗ █████╗ ██╗██╗     ', '████╗ ████║██╔═══██╗████╗  ██║██╔══██╗██╔═══██╗██╔══██╗╚══██╔══╝██╔══██╗██║██║     ', '██╔████╔██║██║   ██║██╔██╗ ██║██████╔╝██║   ██║██████╔╝   ██║   ███████║██║██║     ', '██║╚██╔╝██║██║   ██║██║╚██╗██║██╔═══╝ ██║   ██║██╔══██╗   ██║   ██╔══██║██║██║     ', '██║ ╚═╝ ██║╚██████╔╝██║ ╚████║██║     ╚██████╔╝██║  ██║   ██║   ██║  ██║██║███████╗', '╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝      ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝╚══════╝']

SUBTITLE_WIDE = ['██╗   ██╗██╗      █████╗ ██╗   ██╗ █████╗ ██╗     ', '██║   ██║██║     ██╔══██╗██║   ██║██╔══██╗██║     ', '██║   ██║██║     ███████║██║   ██║███████║██║     ', '██║   ██║██║     ██╔══██║╚██╗ ██╔╝██╔══██║██║     ', '╚██████╔╝███████╗██║  ██║ ╚████╔╝ ██║  ██║███████╗', ' ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝  ╚═╝╚══════╝']

NARROW = ['                ___         _        _ _   _   _ _                  _ ', ' _ __  ___ _ _ | _ \\___ _ _| |_ __ _(_) | | | | | |   __ ___ ____ _| |', "| '  \\/ _ \\ ' \\|  _/ _ \\ '_|  _/ _` | | | | |_| | |__/ _` \\ V / _` | |", '|_|_|_\\___/_||_|_| \\___/_|  \\__\\__,_|_|_|  \\___/|____\\__,_|\\_/\\__,_|_|']

GRADIENT = ["#e4002b", "#e8262a", "#ec4c28", "#f07326", "#f49924", "#f8bf21", "#fcd51f"]


def _paint(lines: list[str]) -> list[Text]:
    count = len(lines)
    return [Text(line, style=f"bold {GRADIENT[min(i * len(GRADIENT) // count, len(GRADIENT) - 1)]}") for i, line in enumerate(lines)]


def show(console: Console) -> None:
    if console.width >= len(WIDE[0]) + 2:
        lines = _paint(WIDE) + [Text("")] + _paint(SUBTITLE_WIDE)
    elif console.width >= len(NARROW[0]) + 2:
        lines = _paint(NARROW)
    else:
        lines = [Text("MONPORTAIL ULAVAL", style="bold #e4002b")]
    tagline = Text("archiveur des anciens sites de cours · avant la fermeture du 1er novembre 2026", style="dim italic")
    console.print()
    console.print(Align.center(Group(*[Align.center(line) for line in lines], Text(""), Align.center(tagline))))
    console.print()
