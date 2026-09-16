"""Verification finale d'une archive face a son manifeste, et mise en ZIP.

Aucun de ces tests ne touche au reseau : tout se joue sur des dossiers
temporaires (tmp_path), avec des manifestes ecrits a la main quand il faut
simuler un fichier CSV abime par un utilisateur.
"""

import csv
import zipfile

from extracteur.manifeste import COLONNES, Manifeste
from extracteur.nommage import chemin_long
from extracteur.verification import creer_zip, verifier


def _ecrire_manifeste_brut(racine, lignes):
    """Ecrit un manifeste.csv a la main, sans passer par Manifeste.ajouter.

    Sert a simuler une colonne "taille" abimee par un utilisateur qui aurait
    ouvert le CSV dans Excel -- un scenario que Manifeste.ajouter ne peut pas
    produire lui-meme.
    """
    with open(racine / "manifeste.csv", "w", encoding="utf-8-sig", newline="") as sortie:
        redacteur = csv.DictWriter(sortie, fieldnames=COLONNES)
        redacteur.writeheader()
        for ligne in lignes:
            redacteur.writerow(ligne)


# --- verifier : confrontation du disque au manifeste ---


def test_verification_sur_archive_coherente(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"abc")
    Manifeste(tmp_path).ajouter("a.pdf", 3, "x", "/contenu/a.pdf")

    rapport = verifier(tmp_path)

    assert rapport["inscrits"] == 1
    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []


def test_verification_detecte_un_fichier_manquant(tmp_path):
    Manifeste(tmp_path).ajouter("absent.pdf", 3, "x", "/contenu/absent.pdf")

    rapport = verifier(tmp_path)

    assert rapport["manquants"] == ["absent.pdf"]
    assert rapport["taille_incorrecte"] == []


def test_verification_detecte_une_taille_incorrecte(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"ab")
    Manifeste(tmp_path).ajouter("a.pdf", 3, "x", "/contenu/a.pdf")

    rapport = verifier(tmp_path)

    assert rapport["taille_incorrecte"] == ["a.pdf"]
    assert rapport["manquants"] == []


def test_verification_taille_illisible_ne_plante_pas(tmp_path):
    # Le manifeste est un CSV que l'utilisateur peut ouvrir et abimer : une
    # colonne "taille" vide ou non numerique ne doit jamais lever, et ne doit
    # pas non plus etre traitee comme une taille incorrecte, faute de pouvoir
    # la controler (meme raisonnement que archiveur._taille_manifeste).
    (tmp_path / "a.pdf").write_bytes(b"abc")
    _ecrire_manifeste_brut(
        tmp_path,
        [
            {
                "chemin": "a.pdf",
                "taille": "",
                "sha256": "x",
                "url": "/contenu/a.pdf",
                "horodatage": "2026-01-01T00:00:00",
                "statut": "ok",
            }
        ],
    )

    rapport = verifier(tmp_path)

    assert rapport["inscrits"] == 1
    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []


def test_verification_taille_non_numerique_ne_plante_pas(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"abc")
    _ecrire_manifeste_brut(
        tmp_path,
        [
            {
                "chemin": "a.pdf",
                "taille": "n/a",
                "sha256": "x",
                "url": "/contenu/a.pdf",
                "horodatage": "2026-01-01T00:00:00",
                "statut": "ok",
            }
        ],
    )

    rapport = verifier(tmp_path)

    assert rapport["taille_incorrecte"] == []


def test_verification_sans_manifeste_ne_leve_pas(tmp_path):
    rapport = verifier(tmp_path)

    assert rapport == {"inscrits": 0, "manquants": [], "taille_incorrecte": []}


def test_verification_sur_archive_vide(tmp_path):
    rapport = verifier(tmp_path)

    assert rapport["inscrits"] == 0
    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []


def test_verification_sur_chemin_long(tmp_path):
    # Un dossier archive de plusieurs mois peut depasser la limite de 260
    # caracteres de Windows : la verification doit s'appuyer sur chemin_long,
    # au meme titre que stockage.ecrire_flux et stockage.deja_present.
    import os

    profond = tmp_path
    for _ in range(15):
        profond = profond / ("d" * 50)
    os.makedirs(chemin_long(profond), exist_ok=True)
    chemin_fichier = profond / "notes.pdf"
    with open(chemin_long(chemin_fichier), "wb") as sortie:
        sortie.write(b"contenu")

    relatif = str(chemin_fichier.relative_to(tmp_path)).replace("\\", "/")
    Manifeste(tmp_path).ajouter(relatif, 7, "x", "/contenu/notes.pdf")

    rapport = verifier(tmp_path)

    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []


# --- creer_zip : mise en archive compressee ---


def test_creation_du_zip(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"abc")
    destination = tmp_path.parent / "archive.zip"

    creer_zip(tmp_path, destination)

    assert destination.exists()
    with zipfile.ZipFile(destination) as archive:
        assert "a.pdf" in archive.namelist()


def test_creation_du_zip_preserve_l_arborescence_relative(tmp_path):
    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents"
    dossier.mkdir(parents=True)
    (dossier / "notes.pdf").write_bytes(b"abc")
    destination = tmp_path.parent / "archive.zip"

    creer_zip(tmp_path, destination)

    with zipfile.ZipFile(destination) as archive:
        assert "2026-1 Hiver/ABC-1000 Éthique/Documents/notes.pdf" in archive.namelist()


def test_creation_du_zip_ne_s_inclut_pas_lui_meme(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"abc")
    destination = tmp_path / "archive.zip"

    creer_zip(tmp_path, destination)

    with zipfile.ZipFile(destination) as archive:
        noms = archive.namelist()
        assert "a.pdf" in noms
        assert "archive.zip" not in noms
        assert len(noms) == 1


def test_creation_du_zip_sur_archive_vide(tmp_path):
    destination = tmp_path.parent / "vide.zip"

    creer_zip(tmp_path, destination)

    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == []


def test_creation_du_zip_sur_chemin_long(tmp_path):
    import os

    profond = tmp_path
    for _ in range(15):
        profond = profond / ("d" * 50)
    os.makedirs(chemin_long(profond), exist_ok=True)
    with open(chemin_long(profond / "notes.pdf"), "wb") as sortie:
        sortie.write(b"contenu")

    destination = tmp_path.parent / "archive-longue.zip"
    creer_zip(tmp_path, destination)

    relatif_attendu = str((profond / "notes.pdf").relative_to(tmp_path)).replace("\\", "/")
    with zipfile.ZipFile(destination) as archive:
        assert relatif_attendu in archive.namelist()
