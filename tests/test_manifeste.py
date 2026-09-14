from extracteur.manifeste import Manifeste, ecrire_depots, ecrire_notes, ecrire_rapport
from extracteur.modele import Depot, Echec, Note


def test_manifeste_vide_au_depart(tmp_path):
    assert Manifeste(tmp_path).charger() == {}


def test_ajout_puis_relecture(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("2026-1 Hiver/PHI/a.pdf", 120, "abc", "/contenu/a.pdf", "ok")

    relu = Manifeste(tmp_path).charger()
    assert relu["2026-1 Hiver/PHI/a.pdf"]["sha256"] == "abc"
    assert relu["2026-1 Hiver/PHI/a.pdf"]["taille"] == "120"


def test_par_url_retrouve_l_entree(tmp_path):
    # Cle stable pour la reprise : contrairement au chemin, l'url ne change
    # jamais d'une execution a l'autre a cause de la desambiguisation.
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("Documents/notes.pdf", 120, "abc", "/contenu/notes.pdf", "ok")
    manifeste.ajouter("Documents/notes (2).pdf", 45, "def", "/contenu/autre.pdf", "ok")

    assert Manifeste(tmp_path).par_url("/contenu/notes.pdf")["chemin"] == "Documents/notes.pdf"
    assert Manifeste(tmp_path).par_url("/contenu/autre.pdf")["chemin"] == "Documents/notes (2).pdf"
    assert Manifeste(tmp_path).par_url("/contenu/inconnu.pdf") is None


def test_deja_archive(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("a.pdf", 1, "x", "/contenu/a.pdf", "ok")

    suivant = Manifeste(tmp_path)
    assert suivant.deja_archive("a.pdf") is True
    assert suivant.deja_archive("b.pdf") is False


def test_entete_ecrit_une_seule_fois(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("a.pdf", 1, "x", "/u", "ok")
    manifeste.ajouter("b.pdf", 2, "y", "/u", "ok")

    lignes = (tmp_path / "manifeste.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lignes) == 3
    assert lignes[0].startswith("chemin")


def test_ecrire_notes(tmp_path):
    destination = tmp_path / "notes.csv"
    ecrire_notes(
        destination,
        [Note(evaluation="Examen 1", pourcentage="70 %", ponderation="15 %", note="10,5", sur="15")],
    )

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "Examen 1" in contenu
    assert "70 %" in contenu
    assert "10,5" in contenu


def test_ecrire_notes_marque_les_regroupements(tmp_path):
    destination = tmp_path / "notes.csv"
    ecrire_notes(
        destination,
        [Note(evaluation="Examen final", ponderation="39,99 %", note="31,89", sur="39,99", est_regroupement=True)],
    )

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "Oui" in contenu


def test_ecrire_notes_avec_accents(tmp_path):
    destination = tmp_path / "notes.csv"
    ecrire_notes(destination, [Note(evaluation="Résumé de l'été", note="9")])
    assert "Résumé de l'été" in destination.read_text(encoding="utf-8-sig")


def test_notes_consolidees_portent_session_et_cours(tmp_path):
    from extracteur.manifeste import ecrire_notes_consolidees

    destination = tmp_path / "notes-tous-cours.csv"
    ecrire_notes_consolidees(
        destination,
        [
            ("2026-1 Hiver", "PHI-3900 Éthique", Note(evaluation="Examen 1", note="18", sur="20")),
            ("2025-3 Automne", "GIN-3320 Projet", Note(evaluation="Rapport", note="45", sur="50")),
        ],
    )

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "2026-1 Hiver" in contenu
    assert "PHI-3900 Éthique" in contenu
    assert "Rapport" in contenu


def test_notes_consolidees_vides_ecrivent_l_entete(tmp_path):
    from extracteur.manifeste import ecrire_notes_consolidees

    destination = tmp_path / "notes-tous-cours.csv"
    ecrire_notes_consolidees(destination, [])
    assert destination.read_text(encoding="utf-8-sig").startswith("Session")


def test_ecrire_depots(tmp_path):
    destination = tmp_path / "depots.csv"
    ecrire_depots(
        destination,
        [
            Depot(
                nom="Z1-PHI3900-H2026-TP2.docx",
                url="/contenu/sitescours/site181216/depots/tp2.docx?identifiant=abc",
                taille="3,25 Mo",
                depose_par="Buteau, Laurent",
                date_remise="12 avr. 2026 18h43",
            )
        ],
    )

    lignes = destination.read_text(encoding="utf-8-sig").splitlines()
    assert lignes[0] == "Nom du document,Taille,Déposé par,Date de remise"
    assert "Buteau, Laurent" in lignes[1]
    assert "12 avr. 2026 18h43" in lignes[1]


def test_ecrire_depots_vide_ecrit_l_entete(tmp_path):
    destination = tmp_path / "depots.csv"
    ecrire_depots(destination, [])
    assert destination.read_text(encoding="utf-8-sig").startswith("Nom du document")


def test_integration_boite_de_depot_de_ena_jusqu_au_csv(tmp_path):
    """Chaine complete : HTML de boite de depot -> Ena.fichiers_de_depot ->
    manifeste.ecrire_depots. Preuve que "Depose par", la seule trace de qui a
    remis quoi sur un travail d'equipe, survit jusqu'au CSV sur disque."""
    from extracteur.ena import Ena
    from extracteur.modele import Evaluation

    class LocatorFactice:
        def count(self):
            return 1

    class PageFactice:
        def __init__(self, html):
            self._html = html
            self.url = ""

        def goto(self, url, **_kwargs):
            self.url = url

        def content(self):
            return self._html

        def locator(self, _selecteur):
            # Simule une page authentifiee : Ena.fichiers_de_depot verifie
            # desormais que la session n'a pas expire avant d'extraire.
            return LocatorFactice()

        def get_by_text(self, _texte):
            return LocatorFactice()

    class SessionFactice:
        def __init__(self, html):
            self.page = PageFactice(html)

    html = """
    <table>
      <tr><th>Nom du document</th><th>Taille</th><th>Déposé par</th><th>Date de remise</th></tr>
      <tr>
        <td><a href="/contenu/sitescours/040/04000/202601/site181216/depots/tp2.docx?identifiant=abc">Z1-PHI3900-H2026-TP2 - Éthique.docx</a></td>
        <td>3,25 Mo</td>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
      </tr>
    </table>
    """
    ena = Ena(SessionFactice(html))
    evaluation = Evaluation(id_site="181216", id_evaluation="1035434", titre="TP2")

    depots = ena.fichiers_de_depot(evaluation)
    destination = tmp_path / "depots.csv"
    ecrire_depots(destination, depots)

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "Z1-PHI3900-H2026-TP2 - Éthique.docx" in contenu
    assert "Buteau, Laurent" in contenu
    assert "12 avr. 2026 18h43" in contenu


def test_rapport_liste_les_echecs(tmp_path):
    destination = tmp_path / "_rapport.html"
    ecrire_rapport(
        destination,
        [Echec(cours="PHI-3900", element="a.pdf", cause="HTTP 403", url="/contenu/a.pdf")],
        {"fichiers": 12, "cours": 3},
    )

    contenu = destination.read_text(encoding="utf-8")
    assert "PHI-3900" in contenu
    assert "HTTP 403" in contenu
    assert "/contenu/a.pdf" in contenu


def test_rapport_sans_echec_le_dit(tmp_path):
    destination = tmp_path / "_rapport.html"
    ecrire_rapport(destination, [], {"fichiers": 12, "cours": 3})
    assert "Aucun échec" in destination.read_text(encoding="utf-8")


def test_rapport_echappe_le_html():
    # Un titre de document peut contenir des chevrons.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as dossier:
        destination = Path(dossier) / "r.html"
        ecrire_rapport(destination, [Echec("C", "<script>x</script>", "erreur")], {})
        contenu = destination.read_text(encoding="utf-8")
        assert "<script>" not in contenu
        assert "&lt;script&gt;" in contenu


def test_manifeste_entete_ecrit_si_fichier_vide(tmp_path):
    # Si le fichier existe mais est vide, l'en-tête doit être écrit quand même.
    chemin_manifeste = tmp_path / "manifeste.csv"
    chemin_manifeste.parent.mkdir(parents=True, exist_ok=True)
    chemin_manifeste.write_text("", encoding="utf-8-sig")

    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("a.pdf", 1, "x", "/u", "ok")

    lignes = chemin_manifeste.read_text(encoding="utf-8-sig").splitlines()
    assert len(lignes) == 2
    assert lignes[0].startswith("chemin")


def test_rapport_url_javascript_pas_de_lien():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as dossier:
        destination = Path(dossier) / "r.html"
        ecrire_rapport(
            destination,
            [Echec("C", "malveillant.pdf", "erreur", "javascript:alert(1)")],
            {},
        )
        contenu = destination.read_text(encoding="utf-8")
        assert '<a href="javascript:' not in contenu
        assert "javascript:alert(1)" in contenu
