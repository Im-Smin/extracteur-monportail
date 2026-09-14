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
