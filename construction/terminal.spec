# PyInstaller recipe for the terminal executable. Run through construire.py --interface terminal.

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def without_browsers(entry):
    paths = (str(entry[0]).replace("\\", "/"), str(entry[1]).replace("\\", "/"))
    return not any("playwright/driver" in p or p.startswith("driver") for p in paths)


a = Analysis(
    ["terminal_launcher.py"],
    pathex=[".."],
    datas=[d for d in collect_data_files("playwright") if without_browsers(d)],
    hiddenimports=collect_submodules("rich")
    + collect_submodules("prompt_toolkit", filter=lambda name: not name.startswith("prompt_toolkit.contrib")),
    excludes=["pytest"],
)
a.datas = [d for d in a.datas if without_browsers(d)]
a.binaries = [b for b in a.binaries if without_browsers(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Archiveur-monPortail-terminal",
    console=True,
    upx=False,
)
