import struct
import zipfile

import pytest

from extracteur.navigateur_embarque import (
    FORMAT_COOKIE,
    MAGIQUE_PYINSTALLER,
    MARQUE_PRET,
    _debut_paquet_pyinstaller,
    integrer_archive,
    preparer_navigateur,
    preparer_navigateur_integre,
)


def _archive(tmp_path, nom="nav-chromium-1234.zip", contenu=None):
    contenu = contenu or {"chromium-1234/chrome-win64/chrome.exe": b"MZ", "ffmpeg-1011/ffmpeg.exe": b"MZ"}
    chemin = tmp_path / nom
    with zipfile.ZipFile(chemin, "w") as archive:
        for nom_fichier, donnees in contenu.items():
            archive.writestr(nom_fichier, donnees)
    return chemin


def test_premier_lancement_decompresse_le_navigateur_dans_le_cache(tmp_path):
    archive = _archive(tmp_path)
    cache = tmp_path / "cache"
    annonces = []

    dossier = preparer_navigateur(archive, cache, annoncer=annonces.append)

    assert (dossier / "chromium-1234" / "chrome-win64" / "chrome.exe").read_bytes() == b"MZ"
    assert (dossier / MARQUE_PRET).exists()
    # Premier lancement : l'utilisateur est prevenu de l'attente.
    assert len(annonces) == 1


def test_lancements_suivants_reutilisent_le_cache_sans_rien_decompresser(tmp_path):
    archive = _archive(tmp_path)
    cache = tmp_path / "cache"
    premier = preparer_navigateur(archive, cache, annoncer=lambda _m: None)
    annonces = []

    second = preparer_navigateur(archive, cache, annoncer=annonces.append)

    assert second == premier
    assert annonces == []


def test_une_decompression_interrompue_est_refaite(tmp_path):
    # Premier lancement coupe en plein travail (fermeture, panne) : sans la
    # marque de fin, le dossier n'est jamais considere comme pret.
    archive = _archive(tmp_path)
    cache = tmp_path / "cache"
    dossier = preparer_navigateur(archive, cache, annoncer=lambda _m: None)
    (dossier / MARQUE_PRET).unlink()
    (dossier / "chromium-1234" / "chrome-win64" / "chrome.exe").unlink()

    preparer_navigateur(archive, cache, annoncer=lambda _m: None)

    assert (dossier / "chromium-1234" / "chrome-win64" / "chrome.exe").exists()
    assert (dossier / MARQUE_PRET).exists()


def test_une_nouvelle_version_remplace_l_ancienne(tmp_path):
    # Une mise a jour de l'Archiveur embarque un autre Chromium : l'ancien
    # (~430 Mo) ne doit pas s'accumuler dans le cache.
    cache = tmp_path / "cache"
    ancien = preparer_navigateur(_archive(tmp_path, "nav-chromium-1000.zip"), cache, annoncer=lambda _m: None)

    nouveau = preparer_navigateur(_archive(tmp_path, "nav-chromium-1234.zip"), cache, annoncer=lambda _m: None)

    assert nouveau.exists()
    assert not ancien.exists()


def test_refuse_une_archive_qui_sortirait_du_cache(tmp_path):
    archive = _archive(tmp_path, contenu={"../evasion.txt": b"x"})

    with pytest.raises(ValueError):
        preparer_navigateur(archive, tmp_path / "cache", annoncer=lambda _m: None)

    assert not (tmp_path / "evasion.txt").exists()


# --- Archive integree a l'executable, juste avant le paquet PyInstaller ---


def _faux_executable(tmp_path):
    """Chargeur, puis paquet PyInstaller termine par son cookie : le debut
    du paquet se deduit de la fin du fichier, comme dans le vrai chargeur."""
    paquet = b"CONTENU-DU-PAQUET" * 10
    cookie_taille = struct.calcsize(FORMAT_COOKIE)
    cookie = struct.pack(FORMAT_COOKIE, MAGIQUE_PYINSTALLER, len(paquet) + cookie_taille, 0, 0, 312, b"python312.dll")
    chemin = tmp_path / "base.exe"
    chemin.write_bytes(b"MZ-CHARGEUR" * 50 + paquet + cookie)
    return chemin, paquet + cookie


def test_l_archive_integree_laisse_le_paquet_pyinstaller_intact(tmp_path):
    base, fin_attendue = _faux_executable(tmp_path)
    exe = tmp_path / "Archiveur.exe"

    integrer_archive(base, _archive(tmp_path), exe)

    donnees = exe.read_bytes()
    assert donnees.endswith(fin_attendue)
    # Le chargeur retrouve son paquet exactement au meme endroit relatif.
    with open(exe, "rb") as f:
        debut = _debut_paquet_pyinstaller(f)
    assert donnees[debut:] == fin_attendue


def test_le_navigateur_integre_est_decompresse_une_seule_fois(tmp_path):
    base, _ = _faux_executable(tmp_path)
    exe = tmp_path / "Archiveur.exe"
    integrer_archive(base, _archive(tmp_path), exe)
    cache = tmp_path / "cache"
    annonces = []

    dossier = preparer_navigateur_integre(exe, cache, annoncer=annonces.append)
    preparer_navigateur_integre(exe, cache, annoncer=annonces.append)

    assert dossier.name == "nav-chromium-1234"
    assert (dossier / "chromium-1234" / "chrome-win64" / "chrome.exe").read_bytes() == b"MZ"
    assert len(annonces) == 1
    # L'archive temporaire copiee depuis l'executable ne reste pas.
    assert not list(cache.glob("*.zip"))


def test_un_executable_sans_archive_integree_est_signale(tmp_path):
    base, _ = _faux_executable(tmp_path)

    with pytest.raises(ValueError):
        preparer_navigateur_integre(base, tmp_path / "cache", annoncer=lambda _m: None)
