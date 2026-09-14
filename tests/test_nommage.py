from pathlib import Path

import pytest

from extracteur.nommage import chemin_long, nom_sur, nom_unique, tronquer


@pytest.mark.parametrize(
    "entree, attendu",
    [
        ('rapport<final>.pdf', 'rapport-final-.pdf'),
        ('note: importante.txt', 'note- importante.txt'),
        ('a/b\\c.txt', 'a-b-c.txt'),
        ('qui?.doc', 'qui-.doc'),
        ('100% * 2.xlsx', '100% - 2.xlsx'),
    ],
)
def test_caracteres_interdits_remplaces(entree, attendu):
    assert nom_sur(entree) == attendu


@pytest.mark.parametrize("reserve", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_noms_reserves_prefixes(reserve):
    assert nom_sur(f"{reserve}.txt") == f"_{reserve}.txt"
    assert nom_sur(reserve) == f"_{reserve}"


def test_nom_reserve_insensible_a_la_casse():
    assert nom_sur("con.TXT") == "_con.TXT"


def test_points_et_espaces_de_fin_retires():
    assert nom_sur("dossier. ") == "dossier"
    assert nom_sur("fichier.txt.") == "fichier.txt"


def test_accents_conserves():
    assert nom_sur("Résumé été 2026.pdf") == "Résumé été 2026.pdf"


def test_nom_vide_remplace():
    assert nom_sur("") == "_"
    assert nom_sur("   ") == "_"


def test_troncature_preserve_extension():
    long_nom = "a" * 200 + ".pptx"
    resultat = tronquer(long_nom, 80)
    assert len(resultat) == 80
    assert resultat.endswith(".pptx")


def test_troncature_laisse_les_noms_courts_intacts():
    assert tronquer("court.pdf", 80) == "court.pdf"


def test_troncature_extension_demesuree():
    # Une "extension" de plus de 80 caractères n'en est pas une : on tronque brutalement.
    resultat = tronquer("fichier." + "z" * 100, 80)
    assert len(resultat) == 80


def test_nom_unique_suffixe_les_collisions(tmp_path):
    (tmp_path / "note.pdf").write_text("x")
    assert nom_unique(tmp_path, "note.pdf") == "note (2).pdf"

    (tmp_path / "note (2).pdf").write_text("x")
    assert nom_unique(tmp_path, "note.pdf") == "note (3).pdf"


def test_nom_unique_laisse_passer_si_libre(tmp_path):
    assert nom_unique(tmp_path, "libre.pdf") == "libre.pdf"


def test_chemin_long_prefixe(tmp_path):
    resultat = chemin_long(tmp_path / "a.txt")
    assert resultat.startswith("\\\\?\\")
    assert resultat.endswith("a.txt")


def test_chemin_long_ne_double_pas_le_prefixe():
    deja = Path("\\\\?\\C:\\temp\\a.txt")
    assert chemin_long(deja).count("\\\\?\\") == 1
