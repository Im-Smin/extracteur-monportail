# Recette PyInstaller de l'executable Windows. Ne pas lancer directement :
# passer par construire.py, qui prepare l'archive du navigateur et verifie
# le resultat.
#
# Fichier unique (onefile) : il se deploie a chaque lancement dans un dossier
# temporaire au chemin court, et fonctionne donc quel que soit l'endroit ou
# l'utilisateur le range (voir extracteur/navigateur_embarque.py). Ni
# Chromium ni le pilote de Playwright (node.exe, 89 Mo) n'en font partie :
# redeployes a chaque lancement, ils coutaient ~18 s de demarrage. construire.py
# les insere ensuite dans l'executable, hors de ce paquet.

from PyInstaller.utils.hooks import collect_data_files


def hors_navigateurs(entree):
    chemins = (str(entree[0]).replace("\\", "/"), str(entree[1]).replace("\\", "/"))
    return not any("playwright/driver" in c or c.startswith("driver") for c in chemins)


a = Analysis(
    ["lanceur.py"],
    pathex=[".."],
    datas=[d for d in collect_data_files("playwright") if hors_navigateurs(d)],
    hiddenimports=[],
    excludes=["pytest"],
)
# Les .exe et .dll du navigateur sont ranges parmi les binaires, pas les
# donnees : les deux listes doivent etre filtrees (verifie par construire.py).
a.datas = [d for d in a.datas if hors_navigateurs(d)]
a.binaries = [b for b in a.binaries if hors_navigateurs(b)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Archiveur monPortail",
    console=False,
    upx=False,
)
