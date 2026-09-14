import hashlib

import pytest

from extracteur.stockage import deja_present, ecrire_flux


def test_ecrire_flux_retourne_taille_et_empreinte(tmp_path):
    destination = tmp_path / "a.bin"
    taille, empreinte = ecrire_flux(destination, [b"abc", b"def"])

    assert destination.read_bytes() == b"abcdef"
    assert taille == 6
    assert empreinte == hashlib.sha256(b"abcdef").hexdigest()


def test_aucun_fichier_part_ne_survit(tmp_path):
    destination = tmp_path / "a.bin"
    ecrire_flux(destination, [b"abc"])
    assert list(tmp_path.glob("*.part")) == []


def test_le_part_est_nettoye_si_le_flux_echoue(tmp_path):
    destination = tmp_path / "a.bin"

    def flux_qui_casse():
        yield b"abc"
        raise ConnectionError("coupure")

    with pytest.raises(ConnectionError):
        ecrire_flux(destination, flux_qui_casse())

    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_deja_present_faux_si_absent(tmp_path):
    assert deja_present(tmp_path / "absent.pdf", 10) is False


def test_deja_present_vrai_si_taille_correspond(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"0123456789")
    assert deja_present(fichier, 10) is True


def test_deja_present_faux_si_taille_differente(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"012")
    assert deja_present(fichier, 10) is False


def test_deja_present_vrai_si_taille_inconnue(tmp_path):
    # Le serveur ne donne pas toujours Content-Length : un fichier non vide suffit.
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"012")
    assert deja_present(fichier, None) is True


def test_deja_present_faux_si_fichier_vide(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"")
    assert deja_present(fichier, None) is False


def test_les_dossiers_parents_sont_crees(tmp_path):
    destination = tmp_path / "x" / "y" / "a.bin"
    ecrire_flux(destination, [b"abc"])
    assert destination.exists()


def test_chemin_long_plus_de_260_caracteres(tmp_path):
    import os
    from extracteur.nommage import chemin_long

    # Construire un chemin depassant 260 caracteres (sans prefixe \\?\)
    profond = tmp_path
    for i in range(15):
        profond = profond / ("d" * 50)

    destination = profond / "file.bin"

    # ecrire_flux doit fonctionner sur chemin long
    taille, empreinte = ecrire_flux(destination, [b"x"])

    assert taille == 1
    assert empreinte == hashlib.sha256(b"x").hexdigest()

    # deja_present doit reconnaitre le fichier cree
    assert deja_present(destination, 1) is True

    # Pas de .part subsiste
    parts = [n for n in os.listdir(chemin_long(profond)) if n.endswith(".part")]
    assert parts == []

    # Test avec erreur
    destination2 = profond / "file2.bin"

    def flux_qui_casse():
        yield b"x"
        raise ValueError("coupure")

    with pytest.raises(ValueError):
        ecrire_flux(destination2, flux_qui_casse())

    assert not deja_present(destination2, 1)
    parts2 = [n for n in os.listdir(chemin_long(profond)) if n.endswith(".part")]
    assert parts2 == []
