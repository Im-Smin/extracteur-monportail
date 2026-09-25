import pytest
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


def test_verification_ignore_les_entrees_non_ok(tmp_path):
    # CRITIQUE : une ressource ignoree (video, lien externe, traceur inconnu)
    # n'a jamais ete ecrite sur disque. Sans ce filtre, verifier() la
    # compterait comme un fichier manquant, et l'archive serait annoncee
    # incomplete alors qu'elle est parfaitement saine.
    Manifeste(tmp_path).ajouter(
        "Documents/Module 1/capsule.mp4", "", "", "/contenu/capsule.mp4", "video"
    )

    rapport = verifier(tmp_path)

    assert rapport["manquants"] == []
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
# --- compression : rapidite, progression, arret, ecriture atomique ---


def _archive_de_test(racine):
    (racine / "cours").mkdir(parents=True)
    (racine / "cours" / "notes.txt").write_text("texte " * 2000, encoding="utf-8")
    (racine / "cours" / "diapos.pdf").write_bytes(b"%PDF-1.4 " + bytes(range(256)) * 50)
    (racine / "cours" / "labo.zip").write_bytes(b"PK" + bytes(range(256)) * 50)
    return racine


def test_les_formats_deja_compresses_sont_stockes_tels_quels(tmp_path):
    # Mesure reelle sur 7,6 Go : recompresser PDF, PPTX, ZIP... tourne a
    # 17 Mo/s pour 9 % de gain, contre 258 Mo/s en stockage simple. C'etait
    # la cause des ~30 minutes de compression prises pour un plantage.
    import zipfile

    racine = _archive_de_test(tmp_path / "Archive")
    cible = creer_zip(racine, tmp_path / "Archive.zip")

    with zipfile.ZipFile(cible) as archive:
        modes = {info.filename: info.compress_type for info in archive.infolist()}
    assert modes["cours/diapos.pdf"] == zipfile.ZIP_STORED
    assert modes["cours/labo.zip"] == zipfile.ZIP_STORED
    # Le texte, lui, se compresse tres bien : on continue de le compresser.
    assert modes["cours/notes.txt"] == zipfile.ZIP_DEFLATED


def test_le_zip_produit_est_integre_et_complet(tmp_path):
    import zipfile

    racine = _archive_de_test(tmp_path / "Archive")
    cible = creer_zip(racine, tmp_path / "Archive.zip")

    with zipfile.ZipFile(cible) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == [
            "cours/diapos.pdf", "cours/labo.zip", "cours/notes.txt",
        ]
        assert archive.read("cours/notes.txt") == (racine / "cours" / "notes.txt").read_bytes()


def test_la_progression_est_rapportee_jusqu_au_total(tmp_path, monkeypatch):
    # Sans signe de vie, une compression de plusieurs minutes ressemble a un
    # plantage : c'est exactement ce que l'utilisateur a vu.
    monkeypatch.setattr("extracteur.verification.INTERVALLE_PROGRESSION_FICHIERS", 1)
    racine = _archive_de_test(tmp_path / "Archive")
    etapes: list = []

    creer_zip(racine, tmp_path / "Archive.zip", progression=lambda *a: etapes.append(a))

    assert etapes, "aucune progression rapportee"
    fait, total, octets_faits, octets_total = etapes[-1]
    assert fait == total == 3
    assert octets_faits == octets_total > 0


def test_l_arret_supprime_le_fichier_partiel(tmp_path):
    # Le bouton Arreter doit fonctionner pendant la compression, et ne rien
    # laisser d'incomplet sur le disque.
    import threading

    from extracteur.verification import CompressionInterrompue

    racine = _archive_de_test(tmp_path / "Archive")
    annulation = threading.Event()
    annulation.set()
    cible = tmp_path / "Archive.zip"

    with pytest.raises(CompressionInterrompue):
        creer_zip(racine, cible, annulation=annulation)

    assert not cible.exists()
    assert not (tmp_path / "Archive.zip.part").exists()


def test_une_compression_qui_echoue_ne_laisse_pas_de_zip_d_apparence_complete(tmp_path, monkeypatch):
    # Incident reel : un arret force de l'ancienne version a laisse un ZIP
    # tronque portant le nom definitif, qui avait l'air complet. L'ecriture
    # passe desormais par un .part renomme seulement a la fin.
    import zipfile

    racine = _archive_de_test(tmp_path / "Archive")
    cible = tmp_path / "Archive.zip"
    ecrire_original = zipfile.ZipFile.write
    appels = {"n": 0}

    def ecrire_puis_echouer(self, *args, **kwargs):
        appels["n"] += 1
        if appels["n"] == 2:
            raise OSError("disque plein")
        return ecrire_original(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", ecrire_puis_echouer)

    with pytest.raises(OSError):
        creer_zip(racine, cible)

    assert not cible.exists()
    assert not (tmp_path / "Archive.zip.part").exists()


def test_un_ancien_zip_dans_le_dossier_n_est_pas_avale_par_le_nouveau(tmp_path):
    # Le repli ecrit le ZIP DANS le dossier quand a cote est interdit (racine
    # d'un disque). Une execution suivante qui, elle, ecrit a cote ne doit pas
    # emballer ce vieux ZIP de plusieurs gigaoctets dans le nouveau.
    import zipfile

    racine = _archive_de_test(tmp_path / "Archive")
    (racine / "Archive.zip").write_bytes(b"PK ancien zip de plusieurs Go")

    cible = creer_zip(racine, tmp_path / "Archive.zip")

    with zipfile.ZipFile(cible) as archive:
        assert "Archive.zip" not in archive.namelist()


def test_le_nom_definitif_n_apparait_qu_une_fois_la_compression_finie(tmp_path, monkeypatch):
    # Un processus tue net (fenetre fermee de force, machine eteinte) ne passe
    # par aucun nettoyage. La seule protection est de n'ecrire sous le nom
    # definitif qu'a la toute fin : tant que ce nom n'existe pas, personne ne
    # peut prendre un ZIP tronque pour une archive complete.
    monkeypatch.setattr("extracteur.verification.INTERVALLE_PROGRESSION_FICHIERS", 1)
    racine = _archive_de_test(tmp_path / "Archive")
    cible = tmp_path / "Archive.zip"
    vu_en_cours: list = []

    def observer(fait, total, *_octets):
        if fait < total:
            vu_en_cours.append(cible.exists())

    creer_zip(racine, cible, progression=observer)

    assert vu_en_cours, "aucune etape intermediaire observee"
    assert not any(vu_en_cours), "le ZIP portait deja son nom definitif en cours d'ecriture"
    assert cible.exists()

