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


def test_chemin_long_force_usage_sur_toutes_operations(monkeypatch, tmp_path):
    r"""Force l'usage de chemin_long sur toutes les 5 operations disque.

    Simule un Windows sans LongPathsEnabled en monkeypatchant os.* pour lever
    sur des chemins longs sans prefixe \\?\. Le test doit rouger si chemin_long
    est retire d'une quelconque operation.
    """
    import os

    PREFIXE = "\\\\?\\"
    LIMITE = 260

    def check_chemin_long(chemin):
        """Leve si chemin est long et non prefixe."""
        chemin_str = str(chemin)  # Convertir Path en str si necessaire
        if len(chemin_str) > LIMITE and not chemin_str.startswith(PREFIXE):
            raise OSError(
                f"Chemin long sans prefixe : {len(chemin_str)} car, attendu \\\\?\\ "
                f"pour un chemin sans LongPathsEnabled"
            )

    # Capturer les originals
    original_makedirs = os.makedirs
    original_exists = os.path.exists
    original_stat = os.stat
    original_unlink = os.unlink
    original_replace = os.replace

    # Creer les versions verifiantes
    def patched_makedirs(chemin, **kwargs):
        check_chemin_long(chemin)
        return original_makedirs(chemin, **kwargs)

    def patched_exists(chemin):
        check_chemin_long(chemin)
        return original_exists(chemin)

    def patched_stat(chemin, **kwargs):
        check_chemin_long(chemin)
        return original_stat(chemin, **kwargs)

    def patched_unlink(chemin, **kwargs):
        check_chemin_long(chemin)
        return original_unlink(chemin, **kwargs)

    def patched_replace(src, dst, **kwargs):
        check_chemin_long(src)
        check_chemin_long(dst)
        return original_replace(src, dst, **kwargs)

    # Monkeypatcher
    monkeypatch.setattr(os, "makedirs", patched_makedirs)
    monkeypatch.setattr(os.path, "exists", patched_exists)
    monkeypatch.setattr(os, "stat", patched_stat)
    monkeypatch.setattr(os, "unlink", patched_unlink)
    monkeypatch.setattr(os, "replace", patched_replace)

    # Construire un chemin depassant 260 caracteres
    profond = tmp_path
    for i in range(15):
        profond = profond / ("d" * 50)

    destination = profond / "file.bin"

    # ecrire_flux DOIT utiliser chemin_long sur toutes les operations
    taille, empreinte = ecrire_flux(destination, [b"test"])
    assert taille == 4
    assert empreinte == hashlib.sha256(b"test").hexdigest()

    # deja_present DOIT utiliser chemin_long sur exists et stat
    assert deja_present(destination, 4) is True

    # Test du nettoyage en cas d'erreur (unlink DOIT utiliser chemin_long)
    destination2 = profond / "file2.bin"

    def flux_qui_casse():
        yield b"abc"
        raise RuntimeError("coupure")

    with pytest.raises(RuntimeError):
        ecrire_flux(destination2, flux_qui_casse())

    # Verification que rien n'a subsiste (via deja_present qui utilise chemin_long)
    assert not deja_present(destination2, 3)
