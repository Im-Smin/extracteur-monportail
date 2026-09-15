import csv
import queue
from pathlib import Path

import pytest
from playwright.sync_api import Error as ErreurPlaywright

from extracteur.archiveur import Archiveur
from extracteur.manifeste import COLONNES
from extracteur.modele import Cours, Depot, Evaluation, Fichier, Module, Note, Session
from extracteur.telechargement import ErreurPermanente, SessionExpiree

SESSION = Session(code="202601", libelle="Hiver 2026")
COURS = Cours(id_site="181216", sigle="PHI-3900", titre="Éthique", session=SESSION)


class EnaFactice:
    def __init__(
        self,
        fichiers=None,
        depots=None,
        notes=None,
        erreur=None,
        fichiers_description=None,
        fichiers_resultats_evaluation=None,
        erreur_capture_pdf_pour=(),
    ):
        self._fichiers = fichiers or []
        self._depots = depots if depots is not None else []
        self._notes = notes or []
        self._erreur = erreur
        self._fichiers_description = fichiers_description or []
        self._fichiers_resultats_evaluation = fichiers_resultats_evaluation or []
        self._erreur_capture_pdf_pour = erreur_capture_pdf_pour
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

    def fichiers_de_description(self, evaluation):
        return self._fichiers_description

    def fichiers_de_resultats_evaluation(self, evaluation):
        return self._fichiers_resultats_evaluation

    def resultats(self, cours):
        return self._notes

    def parcourir_menu(self, cours, action):
        return 0

    def capturer_pdf(self, chemin, destination):
        self.pdf_captures.append(destination)
        if destination.name in self._erreur_capture_pdf_pour:
            raise ErreurPlaywright(f"impression impossible : {destination.name}")
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


def test_collision_reelle_stable_a_travers_les_relances(tmp_path):
    # Le defaut de la ronde 1 : le manifeste etait interroge par le chemin
    # APRES desambiguisation, mais celui-ci designe toujours l'entree du
    # PREMIER fichier en collision. La comparaison d'url du second echouait
    # donc a chaque relance, et nom_unique (qui ne consulte que le disque)
    # ajoutait un nouveau suffixe a chaque execution. Ici, quatre appels
    # successifs a archiver() doivent laisser exactement deux fichiers.
    premier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    second = Fichier(nom="notes.pdf", url="/contenu/sitescours/y/notes.pdf?identifiant=b")
    ena = EnaFactice([premier, second])

    for _ in range(4):
        Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes (2).pdf", "notes.pdf"]


def test_taille_illisible_dans_le_manifeste_ne_fait_pas_planter_la_reprise(tmp_path):
    # Un manifeste.csv est un fichier que l'utilisateur peut ouvrir et editer
    # a la main : une colonne "taille" vide ou non numerique ne doit jamais
    # lever de ValueError, mais etre traitee comme une taille inconnue.
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Module 1"
    dossier.mkdir(parents=True)
    (dossier / "notes.pdf").write_bytes(b"contenu")

    chemin_relatif = "2026-1 Hiver/PHI-3900 Éthique/Documents/Module 1/notes.pdf"
    with open(tmp_path / "manifeste.csv", "w", encoding="utf-8-sig", newline="") as sortie:
        redacteur = csv.DictWriter(sortie, fieldnames=COLONNES)
        redacteur.writeheader()
        redacteur.writerow(
            {
                "chemin": chemin_relatif,
                "taille": "",
                "sha256": "abc",
                "url": fichier.url,
                "horodatage": "2026-01-01T00:00:00",
                "statut": "ok",
            }
        )

    archiveur = Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue())
    resultat = archiveur.archiver([COURS])

    # Taille inconnue : deja_present verifie seulement que le fichier existe
    # et n'est pas vide, pas de nouveau telechargement.
    assert resultat.fichiers_sautes == 1
    assert resultat.fichiers_ecrits == 0


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


class PageFacticeMenu:
    """Page factice pour _capturer_page_courante : pdf() echoue pour la
    premiere section, reussit pour les suivantes."""

    def __init__(self):
        self.appels = 0

    def pdf(self, path, **_kwargs):
        self.appels += 1
        if self.appels == 1:
            raise ErreurPlaywright("impression impossible")
        Path(path).write_bytes(b"%PDF-1.4 factice")


class SessionFacticeMenu:
    def __init__(self, page):
        self.page = page


class EnaMenuAvecEchecPartiel(EnaFactice):
    """Site sans modules ni evaluations, repli par le menu : la premiere
    section n'a rien a telecharger et son impression echoue, la seconde
    porte un vrai fichier recuperable."""

    def __init__(self, page):
        super().__init__()
        self.session = SessionFacticeMenu(page)

    def modules(self, cours):
        return []

    def evaluations(self, cours):
        return []

    def parcourir_menu(self, cours, action):
        action("Section un", "<html></html>")
        action(
            "Section deux",
            '<a href="/contenu/sitescours/x/second.pdf?identifiant=b">second.pdf</a>',
        )
        return 2


def test_echec_capture_menu_isole_la_section_fautive(tmp_path):
    # Le defaut de la ronde 1 : une exception non interceptee dans
    # _capturer_page_courante traversait le traitement de TOUTES les sections
    # du menu, transformant tout en un seul Echec generique sur "(cours
    # entier)" et perdant en silence le contenu recuperable des sections
    # suivantes. Ici, la premiere section doit echouer isolement, nommee, et
    # la seconde doit etre traitee normalement.
    page = PageFacticeMenu()
    ena = EnaMenuAvecEchecPartiel(page)
    archiveur = Archiveur(ena, transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    assert len(resultat.echecs) == 1
    assert resultat.echecs[0].cours == COURS.dossier()
    assert resultat.echecs[0].element == "Section un.pdf"

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Documents" / "Section deux"
    assert (dossier / "second.pdf").exists()


def test_session_expiree_depuis_navigation_ne_devient_pas_un_echec_ordinaire(tmp_path):
    # Sans garde-fou dans ena.py, une session expiree en cours de navigation
    # ne remonte que via SessionExpiree ; l'isolation par cours de archiver()
    # ne doit surtout pas l'avaler comme une erreur de cours ordinaire.
    class EnaSessionExpireeSurModules(EnaFactice):
        def modules(self, cours):
            raise SessionExpiree("page de connexion")

    evenements = queue.Queue()
    archiveur = Archiveur(EnaSessionExpireeSurModules(), transport_ok, tmp_path, evenements)

    with pytest.raises(SessionExpiree):
        archiveur.archiver([COURS])

    assert archiveur.resultat.echecs == []
    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "pause" in types
    assert "echec" not in types


def test_session_expiree_pendant_la_boucle_des_modules_ne_devient_pas_un_echec_ordinaire(tmp_path):
    # Meme garde-fou que pour modules() : une session expiree pendant la
    # boucle des modules (fichiers_du_module ou capturer_pdf) doit remonter
    # intacte, jamais avalee par l'isolation par cours.
    class EnaSessionExpireeSurFichiersDuModule(EnaFactice):
        def fichiers_du_module(self, module):
            raise SessionExpiree("page de connexion")

    evenements = queue.Queue()
    archiveur = Archiveur(
        EnaSessionExpireeSurFichiersDuModule(), transport_ok, tmp_path, evenements
    )

    with pytest.raises(SessionExpiree):
        archiveur.archiver([COURS])

    assert archiveur.resultat.echecs == []
    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "pause" in types
    assert "echec" not in types


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


def test_pages_de_la_section_evaluations_capturees(tmp_path):
    # La liste des evaluations et le sommaire des resultats sont des pages a
    # part entiere de la section, pas seulement les fichiers deposes et le
    # tableau des notes : elles doivent etre capturees comme les pages de
    # module, dans Pages/.
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique"
    assert (base / "Pages" / "evaluations.pdf").exists()
    assert (base / "Pages" / "sommaire-des-resultats.pdf").exists()


def test_description_et_resultats_d_evaluation_captures_dans_mes_depots(tmp_path):
    # La description (consignes du travail) et l'onglet Resultats (retroaction
    # possible) de chaque evaluation sont ranges dans le meme dossier que ses
    # depots deja existants, sans creer de nouvelle arborescence.
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Mes dépôts" / "TP1"
    assert (dossier / "description.pdf").exists()
    assert (dossier / "resultats.pdf").exists()


def test_fichiers_joints_description_recuperes(tmp_path):
    # Une description d'evaluation peut porter l'enonce d'un travail en piece
    # jointe : sans cette recuperation, ces consignes disparaitraient sans
    # laisser de trace a la fermeture de la plateforme.
    enonce = Fichier(nom="enonce.pdf", url="/contenu/sitescours/x/enonce.pdf?identifiant=a")
    ena = EnaFactice(fichiers_description=[enonce])
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Mes dépôts" / "TP1"
    assert (dossier / "enonce.pdf").exists()
    assert resultat.fichiers_ecrits >= 1


def test_fichiers_joints_resultats_evaluation_recuperes(tmp_path):
    # L'onglet Resultats d'une evaluation peut porter une retroaction en
    # piece jointe, au meme titre qu'un enonce en description.
    retroaction = Fichier(
        nom="retroaction.pdf", url="/contenu/sitescours/x/retroaction.pdf?identifiant=a"
    )
    ena = EnaFactice(fichiers_resultats_evaluation=[retroaction])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Mes dépôts" / "TP1"
    assert (dossier / "retroaction.pdf").exists()


def test_capture_de_page_evaluation_en_echec_isolee(tmp_path):
    # Une capture ratee (ici la description) ne doit couter que ce fichier,
    # jamais l'evaluation ni le cours : l'onglet Resultats de la meme
    # evaluation doit quand meme etre capture.
    ena = EnaFactice(erreur_capture_pdf_pour=("description.pdf",))
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert any(e.element == "description.pdf" for e in resultat.echecs)
    dossier = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique" / "Mes dépôts" / "TP1"
    assert not (dossier / "description.pdf").exists()
    assert (dossier / "resultats.pdf").exists()


def test_cours_sans_evaluation_ne_produit_ni_dossier_ni_erreur(tmp_path):
    class EnaSansEvaluations(EnaFactice):
        def evaluations(self, cours):
            return []

    resultat = Archiveur(
        EnaSansEvaluations(), transport_ok, tmp_path, queue.Queue()
    ).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "PHI-3900 Éthique"
    assert not (base / "Mes dépôts").exists()
    assert not (base / "Pages" / "evaluations.pdf").exists()
    assert not (base / "Pages" / "sommaire-des-resultats.pdf").exists()
    assert resultat.echecs == []


def test_arborescence_des_depots_existants_inchangee(tmp_path):
    # Ajouter la capture des pages de description et de resultats ne doit pas
    # deplacer les depots deja indexes par le manifeste : une reorganisation
    # ferait retelecharger toute l'archive existante de l'utilisateur.
    depot = Depot(nom="travail.docx", url="/contenu/sitescours/x/travail.docx?identifiant=a")
    ena = EnaFactice(depots=[depot])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    attendu = (
        tmp_path
        / "2026-1 Hiver"
        / "PHI-3900 Éthique"
        / "Mes dépôts"
        / "TP1"
        / "travail.docx"
    )
    assert attendu.exists()
