import queue

import pytest

from extracteur.archiveur import Archiveur
from extracteur.modele import Cours, Depot, Evaluation, Fichier, Module, Note, Session
from extracteur.telechargement import ErreurPermanente, SessionExpiree

SESSION = Session(code="202601", libelle="Hiver 2026")
COURS = Cours(id_site="181216", sigle="PHI-3900", titre="Éthique", session=SESSION)


class EnaFactice:
    def __init__(self, fichiers=None, depots=None, notes=None, erreur=None):
        self._fichiers = fichiers or []
        self._depots = depots if depots is not None else []
        self._notes = notes or []
        self._erreur = erreur
        self.pdf_captures = []
        self.plans_captures = []

    def modules(self, cours):
        if self._erreur:
            raise self._erreur
        return [Module(id_site=cours.id_site, id_module="1", titre="Module 1")]

    def fichiers_du_module(self, module):
        return self._fichiers

    def evaluations(self, cours):
        return [Evaluation(id_site=cours.id_site, id_evaluation="9", titre="TP1")]

    def fichiers_de_depot(self, evaluation):
        return self._depots

    def resultats(self, cours):
        return self._notes

    def parcourir_menu(self, cours, action):
        return 0

    def capturer_pdf(self, chemin, destination):
        self.pdf_captures.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 factice")

    def capturer_plan_de_cours(self, cours, destination):
        self.plans_captures.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 plan")
        return True


def transport_ok(url):
    class R:
        statut = 200

        def morceaux(self):
            yield b"contenu"

    return R()


def transport_403(url):
    class R:
        statut = 403

        def morceaux(self):
            yield b""

    return R()


def test_arborescence_session_cours(tmp_path):
    archiveur = Archiveur(EnaFactice(), transport_ok, tmp_path, queue.Queue())
    chemin = archiveur.chemin_du_cours(COURS)
    assert chemin == tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique"


def test_fichier_telecharge_et_inscrit_au_manifeste(tmp_path):
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    archiveur = Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    attendu = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1" / "notes.pdf"
    assert attendu.exists()
    assert resultat.fichiers_ecrits == 1
    assert (tmp_path / "manifeste.csv").exists()


def test_relancer_saute_ce_qui_est_deja_la(tmp_path):
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    second = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert second.fichiers_ecrits == 0
    assert second.fichiers_sautes == 1


def test_echec_permanent_consigne_et_continue(tmp_path):
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    archiveur = Archiveur(EnaFactice([fichier]), transport_403, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    assert resultat.fichiers_ecrits == 0
    assert len(resultat.echecs) == 1
    assert "403" in resultat.echecs[0].cause


def test_un_cours_qui_casse_n_emporte_pas_les_autres(tmp_path):
    autre = Cours(id_site="2", sigle="GIN-3320", titre="Projet", session=SESSION)
    ena = EnaFactice(erreur=RuntimeError("site illisible"))
    archiveur = Archiveur(ena, transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS, autre])

    assert len(resultat.echecs) == 2
    assert all("site illisible" in e.cause for e in resultat.echecs)


def test_session_expiree_interrompt_et_signale(tmp_path):
    def transport_401(url):
        raise SessionExpiree(url)

    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    evenements = queue.Queue()
    archiveur = Archiveur(EnaFactice([fichier]), transport_401, tmp_path, evenements)

    with pytest.raises(SessionExpiree):
        archiveur.archiver([COURS])

    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "pause" in types


def test_notes_exportees(tmp_path):
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    notes = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "notes.csv"
    assert notes.exists()
    assert "Examen 1" in notes.read_text(encoding="utf-8-sig")


def test_pages_capturees_en_pdf(tmp_path):
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    assert any("Pages" in str(p) for p in ena.pdf_captures)


def test_evenements_emis(tmp_path):
    evenements = queue.Queue()
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, evenements).archiver([COURS])

    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "cours" in types
    assert "fichier" in types
    assert "fin" in types


def test_noms_de_fichiers_assainis(tmp_path):
    fichier = Fichier(nom='ra:pport<1>.pdf', url="/contenu/sitescours/x/a.pdf?identifiant=a")
    Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1"
    assert (dossier / "ra-pport-1-.pdf").exists()


def test_relancer_ne_cree_jamais_de_doublon_numerote(tmp_path):
    # Le piege : assainir puis desambiguiser le nom AVANT de verifier le
    # manifeste ferait retelecharger tout le contenu sous « (2) » a chaque
    # relance, et l'archive doublerait de taille a chaque coupure reseau.
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    for _ in range(3):
        Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes.pdf"]


def test_collision_reelle_entre_deux_urls_differentes(tmp_path):
    # Deux ressources distinctes portant le meme nom doivent coexister.
    premier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    second = Fichier(nom="notes.pdf", url="/contenu/sitescours/y/notes.pdf?identifiant=b")
    archiveur = Archiveur(EnaFactice([premier, second]), transport_ok, tmp_path, queue.Queue())

    archiveur.archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes (2).pdf", "notes.pdf"]


def test_plan_de_cours_repli_capture_quand_url_absente(tmp_path):
    # COURS ne porte pas d'url_plan_de_cours : le filet de secours (impression
    # de page) doit etre sollicite.
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    plan = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Plan de cours" / "plan-de-cours.pdf"
    assert plan.exists()
    assert ena.plans_captures


def test_plan_de_cours_telecharge_depuis_url_officielle(tmp_path):
    # Quand la page /portail/cours porte l'url du PDF officiel, on la
    # telecharge comme un fichier normal ; le filet de secours ne doit pas
    # etre sollicite.
    cours = Cours(
        id_site="181216",
        sigle="PHI-3900",
        titre="Éthique",
        session=SESSION,
        url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=a",
    )
    ena = EnaFactice()
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([cours])

    plan = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Plan de cours" / "plan-de-cours.pdf"
    assert plan.exists()
    assert ena.plans_captures == []
    assert resultat.fichiers_ecrits >= 1


def test_depot_telecharge_avec_metadonnees_et_csv(tmp_path):
    # Le CSV doit conserver "Depose par" : sur un travail d'equipe, ce n'est
    # pas toujours l'utilisateur qui a remis le document.
    depot = Depot(
        nom="travail.docx",
        url="/contenu/sitescours/x/travail.docx?identifiant=a",
        taille="12 Ko",
        depose_par="Coequipier Untel",
        date_remise="2026-03-01",
    )
    ena = EnaFactice(depots=[depot])
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Mes dépôts" / "TP1"
    assert (dossier / "travail.docx").exists()

    contenu = (dossier / "depots.csv").read_text(encoding="utf-8-sig")
    assert "Coequipier Untel" in contenu
    assert "12 Ko" in contenu
    assert resultat.fichiers_ecrits >= 1


def test_site_sans_modules_ni_evaluations_passe_par_le_menu(tmp_path):
    # Le site de formation EDI n'a ni modules ni evaluations : sans repli par le
    # menu, son dossier serait vide.
    class EnaSansSchema(EnaFactice):
        def modules(self, cours):
            return []

        def evaluations(self, cours):
            return []

        def parcourir_menu(self, cours, action):
            action(
                "Six biais",
                '<a href="/contenu/sitescours/x/biais.pdf?identifiant=a">biais.pdf</a>',
            )
            return 1

    cours = Cours(
        id_site="149047",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    Archiveur(EnaSansSchema(), transport_ok, tmp_path, queue.Queue()).archiver([cours])

    attendu = (
        tmp_path / "2022-3 Automne" / "Nos biais inconscients" / "Documents" / "Six biais" / "biais.pdf"
    )
    assert attendu.exists()


def test_repli_par_le_menu_non_declenche_si_des_modules_existent(tmp_path):
    class EnaAvecSentinelle(EnaFactice):
        def __init__(self):
            super().__init__()
            self.menu_parcouru = False

        def parcourir_menu(self, cours, action):
            self.menu_parcouru = True
            return 0

    ena = EnaAvecSentinelle()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    assert ena.menu_parcouru is False


def test_notes_consolidees_a_la_racine(tmp_path):
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    consolide = tmp_path / "notes-tous-cours.csv"
    contenu = consolide.read_text(encoding="utf-8-sig")
    assert "2026-1 Hiver" in contenu
    assert "PHI-3900 Éthique" in contenu
    assert "Examen 1" in contenu
