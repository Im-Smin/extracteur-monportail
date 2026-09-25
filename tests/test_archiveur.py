import csv
import queue
from pathlib import Path

import pytest
from playwright.sync_api import Error as ErreurPlaywright

from extracteur.archiveur import Archiveur
from extracteur.manifeste import COLONNES
from extracteur.modele import Cours, Depot, Evaluation, Fichier, Module, Note, PageDeModule, Session
from extracteur.telechargement import ErreurPermanente, SessionExpiree

SESSION = Session(code="202601", libelle="Hiver 2026")
COURS = Cours(id_site="100001", sigle="ABC-1000", titre="Éthique", session=SESSION)


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
        ressources_ignorees=None,
    ):
        self._fichiers = fichiers or []
        self._depots = depots if depots is not None else []
        self._notes = notes or []
        self._erreur = erreur
        self._fichiers_description = fichiers_description or []
        self._fichiers_resultats_evaluation = fichiers_resultats_evaluation or []
        self._erreur_capture_pdf_pour = erreur_capture_pdf_pour
        self._ressources_ignorees = ressources_ignorees or []
        self.pdf_captures = []
        self.plans_captures = []

    def modules(self, cours):
        if self._erreur:
            raise self._erreur
        return [Module(id_site=cours.id_site, id_module="1", titre="Module 1")]

    def fichiers_du_module(self, module, id_page=None):
        return self._fichiers

    def pages_du_module(self, module):
        # Module monte sans barre d'onglets par defaut : conserve le
        # comportement de tous les tests existants, ecrits avant l'ajout des
        # onglets (une seule page racine, sans chemin).
        return [PageDeModule(id_page=None, chemin=())]

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
        self._ecrire_pdf(destination)

    def capturer_pdf_page_courante(self, destination):
        # Meme trace et memes echecs simules que capturer_pdf : pour tous les
        # tests qui ne portent PAS sur le rechargement, les deux voies sont
        # interchangeables. Voir EnaQuiDistingueLesCaptures pour celui qui les
        # distingue.
        self.pdf_captures.append(destination)
        self._ecrire_pdf(destination)

    def ressources_ignorees_page_courante(self):
        return self._ressources_ignorees

    def _ecrire_pdf(self, destination):
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
    assert chemin == tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"


def test_fichier_telecharge_et_inscrit_au_manifeste(tmp_path):
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    archiveur = Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    attendu = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1" / "notes.pdf"
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
    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
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


def test_annulation_interrompt_a_la_frontiere_du_cours_suivant(tmp_path):
    # Le bouton Interrompre de l'interface graphique pose l'Event AVANT le
    # premier cours : rien ne doit etre tente, mais la synthese finale et
    # l'evenement "fin" doivent tout de meme sortir, sans exception levee --
    # a la difference de SessionExpiree.
    import threading

    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
    evenements = queue.Queue()
    archiveur = Archiveur(EnaFactice(), transport_ok, tmp_path, evenements)
    annulation = threading.Event()
    annulation.set()

    resultat = archiveur.archiver([COURS, autre], annulation=annulation)

    assert resultat.cours_non_tentes == 2
    assert resultat.fichiers_ecrits == 0
    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "pause" in types
    assert "fin" in types
    assert "cours" not in types


def test_annulation_posee_pendant_le_premier_cours_epargne_les_suivants(tmp_path):
    import threading

    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
    evenements = queue.Queue()
    archiveur = Archiveur(EnaFactice(), transport_ok, tmp_path, evenements)
    annulation = threading.Event()

    resultat = archiveur.archiver([COURS, autre], annulation=annulation)

    # Sans annulation posee, les deux cours sont bien tentes -- contre-epreuve
    # que le parametre par defaut (None) ne change rien au comportement
    # existant.
    assert resultat.cours_non_tentes == 0


def test_notes_exportees(tmp_path):
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    notes = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "notes.csv"
    assert notes.exists()
    assert "Examen 1" in notes.read_text(encoding="utf-8-sig")


def test_pages_capturees_en_pdf(tmp_path):
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    assert any("Pages" in str(p) for p in ena.pdf_captures)


class EnaAvecPages(EnaFactice):
    """Module dont pages_du_module rend plusieurs pages (onglets simples ou
    imbriques -- voir docs/api-monportail.md, etape 3) : fichiers_du_module
    est cable sur id_page pour rendre le bon contenu par page, comme le
    ferait ena.py en session reelle. `erreur_id_page`, quand fourni, fait
    echouer fichiers_du_module (navigation Playwright) pour cette seule page
    -- reproduit une page illisible sans affecter les autres."""

    def __init__(self, pages, fichiers_par_id_page, erreur_id_page=None):
        super().__init__()
        self._pages = pages
        self._fichiers_par_id_page = fichiers_par_id_page
        self._erreur_id_page = erreur_id_page

    def pages_du_module(self, module):
        return self._pages

    def fichiers_du_module(self, module, id_page=None):
        if id_page is not None and id_page == self._erreur_id_page:
            raise ErreurPlaywright(f"page illisible : idPage={id_page}")
        return self._fichiers_par_id_page.get(id_page, [])


def test_module_sans_onglets_garde_l_arborescence_actuelle(tmp_path):
    # Un module sans barre d'onglets est un montage legitime : comportement
    # inchange, aucun sous-dossier d'onglet ne doit apparaitre.
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert (base / "Documents" / "Module 1" / "a.pdf").exists()
    assert (base / "Pages" / "Module 1.pdf").exists()
    assert not (base / "Documents" / "Module 1" / "Général").exists()


def test_module_avec_un_seul_niveau_d_onglets_range_fichiers_et_pdf_par_onglet(tmp_path):
    fichier_general = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    fichier_contenu = Fichier(nom="b.pdf", url="/contenu/sitescours/x/b.pdf?identifiant=b")
    pages = [
        PageDeModule(id_page="1", chemin=("Général",)),
        PageDeModule(id_page="2", chemin=("Contenu du module",)),
    ]
    ena = EnaAvecPages(
        pages=pages,
        fichiers_par_id_page={"1": [fichier_general], "2": [fichier_contenu]},
    )

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert (base / "Documents" / "Module 1" / "Général" / "a.pdf").exists()
    assert (base / "Documents" / "Module 1" / "Contenu du module" / "b.pdf").exists()
    assert (base / "Pages" / "Module 1 - Général.pdf").exists()
    assert (base / "Pages" / "Module 1 - Contenu du module.pdf").exists()
    assert resultat.fichiers_ecrits == 2


def test_module_avec_onglets_imbriques_range_fichiers_et_pdf_sur_deux_niveaux(tmp_path):
    # MNO-5000 (idSite=100004, idModule=1310231) : deux niveaux d'onglets
    # empiles, avec deux feuilles distinctes ("Vues orthogonales", "Coupes")
    # partageant le meme onglet de niveau 1 ("Théorie et dessin à la main"),
    # et un onglet de niveau 1 sans enfant ("AutoCAD").
    fichier_vues = Fichier(nom="vues.pdf", url="/contenu/sitescours/x/vues.pdf?identifiant=a")
    fichier_coupes = Fichier(nom="coupes.pdf", url="/contenu/sitescours/x/coupes.pdf?identifiant=b")
    fichier_autocad = Fichier(nom="dwg.pdf", url="/contenu/sitescours/x/dwg.pdf?identifiant=c")
    pages = [
        PageDeModule(id_page="3550445", chemin=("Théorie et dessin à la main", "Vues orthogonales")),
        PageDeModule(id_page="3550448", chemin=("Théorie et dessin à la main", "Coupes")),
        PageDeModule(id_page="3550451", chemin=("AutoCAD",)),
    ]
    ena = EnaAvecPages(
        pages=pages,
        fichiers_par_id_page={
            "3550445": [fichier_vues],
            "3550448": [fichier_coupes],
            "3550451": [fichier_autocad],
        },
    )

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert (base / "Théorie et dessin à la main" / "Vues orthogonales" / "vues.pdf").exists()
    assert (base / "Théorie et dessin à la main" / "Coupes" / "coupes.pdf").exists()
    assert (base / "AutoCAD" / "dwg.pdf").exists()

    pages_pdf = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Pages"
    assert (pages_pdf / "Module 1 - Théorie et dessin à la main - Vues orthogonales.pdf").exists()
    assert (pages_pdf / "Module 1 - Théorie et dessin à la main - Coupes.pdf").exists()
    assert (pages_pdf / "Module 1 - AutoCAD.pdf").exists()
    assert resultat.fichiers_ecrits == 3


def test_page_de_module_en_echec_est_isolee_des_autres_pages_du_module(tmp_path):
    # Une page illisible ne doit couter qu'elle, ni les autres pages du meme
    # module, ni le module, ni le cours.
    fichier_contenu = Fichier(nom="b.pdf", url="/contenu/sitescours/x/b.pdf?identifiant=b")
    pages = [
        PageDeModule(id_page="1", chemin=("Général",)),
        PageDeModule(id_page="2", chemin=("Contenu du module",)),
    ]
    ena = EnaAvecPages(
        pages=pages,
        fichiers_par_id_page={"2": [fichier_contenu]},
        erreur_id_page="1",
    )

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert any(e.element == "Module 1 - Général" for e in resultat.echecs)
    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert (base / "Documents" / "Module 1" / "Contenu du module" / "b.pdf").exists()
    assert not (base / "Documents" / "Module 1" / "Général").exists()


def test_session_expiree_pendant_une_page_de_module_ne_devient_pas_echec_ordinaire(tmp_path):
    # Meme garde-fou que pour la boucle des modules : une session expiree
    # pendant la lecture d'une page de module doit remonter intacte, jamais
    # avalee par l'isolation par page ni par cours.
    class EnaSessionExpireeSurPage(EnaAvecPages):
        def fichiers_du_module(self, module, id_page=None):
            raise SessionExpiree("page de connexion")

    pages = [PageDeModule(id_page="1", chemin=("Général",))]
    ena = EnaSessionExpireeSurPage(pages=pages, fichiers_par_id_page={})
    evenements = queue.Queue()
    archiveur = Archiveur(ena, transport_ok, tmp_path, evenements)

    with pytest.raises(SessionExpiree):
        archiveur.archiver([COURS])

    assert archiveur.resultat.echecs == []


def test_trop_de_pages_dans_un_module_est_isole(tmp_path):
    # Une structure d'onglets pathologique ou cyclique (TropDePagesDansUnModule,
    # voir ena.py) ne doit couter que ce module, jamais le reste du cours.
    from extracteur.ena import TropDePagesDansUnModule

    class EnaAvecTropDePages(EnaFactice):
        def pages_du_module(self, module):
            raise TropDePagesDansUnModule("module 1 : plus de 60 pages retenues")

    resultat = Archiveur(
        EnaAvecTropDePages(), transport_ok, tmp_path, queue.Queue()
    ).archiver([COURS])

    assert any(e.element == "Module 1" for e in resultat.echecs)
    # Le reste du cours (plan de cours, evaluations...) reste archive.
    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert (base / "Plan de cours" / "plan-de-cours.pdf").exists()


# --- Consignation des ressources ignorees (videos, liens externes, traceurs
# inconnus) : jamais telechargees, mais tracees au manifeste avec statut
# egal a leur genre, chemin, taille et sha256 vides. ---


def test_video_consignee_au_manifeste_sans_telechargement(tmp_path):
    from extracteur.manifeste import Manifeste

    video = Fichier(
        nom="capsule.mp4",
        url="/contenu/sitescours/x/capsule.mp4?identifiant=a",
        genre="video",
    )
    ena = EnaFactice(ressources_ignorees=[video])

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert not (dossier / "capsule.mp4").exists()
    assert resultat.fichiers_ecrits == 0

    entrees = Manifeste(tmp_path).charger()
    relatif = str((dossier / "capsule.mp4").relative_to(tmp_path)).replace("\\", "/")
    assert entrees[relatif]["statut"] == "video"
    assert entrees[relatif]["url"] == video.url
    assert entrees[relatif]["taille"] == ""
    assert entrees[relatif]["sha256"] == ""


def test_deux_videos_dans_un_module_produisent_deux_lignes_de_manifeste(tmp_path):
    # Ce que l'utilisateur verifiera : un module portant deux capsules video
    # produit deux lignes de manifeste, statut=video, avec leur url reelle.
    # Sans evaluation ici, pour isoler le seul point d'accrochage du module :
    # EnaFactice rend les memes ressources ignorees a chaque page consultee.
    class EnaModuleAvecDeuxVideos(EnaFactice):
        def evaluations(self, cours):
            return []

    premiere = Fichier(nom="c1.mp4", url="/contenu/sitescours/x/c1.mp4", genre="video")
    seconde = Fichier(nom="c2.mp4", url="/contenu/sitescours/x/c2.mp4", genre="video")
    ena = EnaModuleAvecDeuxVideos(ressources_ignorees=[premiere, seconde])

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    from extracteur.manifeste import Manifeste

    entrees = Manifeste(tmp_path).charger()
    lignes_video = [ligne for ligne in entrees.values() if ligne["statut"] == "video"]
    assert len(lignes_video) == 2
    assert {ligne["url"] for ligne in lignes_video} == {premiere.url, seconde.url}


def test_lien_externe_et_traceur_inconnu_consignes_avec_leur_genre(tmp_path):
    from extracteur.manifeste import Manifeste

    externe = Fichier(nom="Doc de l'ordre", url="https://www.oiq.qc.ca/doc.pdf", genre="externe")
    inconnu = Fichier(nom="sondage.html", url="/contenu/sitescours/x/sondage.html", genre="traceur-inconnu")
    ena = EnaFactice(ressources_ignorees=[externe, inconnu])

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    entrees = Manifeste(tmp_path).charger()
    statuts = {ligne["statut"] for ligne in entrees.values()}
    assert "externe" in statuts
    assert "traceur-inconnu" in statuts


def test_verifier_ne_signale_aucune_anomalie_sur_des_videos_ignorees(tmp_path):
    # Preuve integrale demandee : --verifier sur une archive ne comportant
    # que des ressources ignorees doit rendre zero anomalie.
    from extracteur.verification import verifier

    video = Fichier(nom="capsule.mp4", url="/contenu/sitescours/x/capsule.mp4", genre="video")
    ena = EnaFactice(ressources_ignorees=[video])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    rapport = verifier(tmp_path)

    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []


def test_ressource_ignoree_illisible_au_manifeste_n_emporte_pas_le_module(tmp_path):
    # Isolation : une ressource ignoree qui ne peut pas etre consignee (ici,
    # l'ecriture du manifeste echoue) ne doit jamais couter le module ni le
    # cours -- meme principe que le reste de l'archivage.
    class ManifesteQuiRefuseLesIgnorees:
        def __init__(self, appels):
            self._appels = appels

        def par_url(self, url):
            return None

        def ajouter(self, chemin_relatif, taille, sha256, url, statut="ok"):
            self._appels.append(statut)
            if statut != "ok":
                raise OSError("manifeste verrouille")

    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    video = Fichier(nom="capsule.mp4", url="/contenu/sitescours/x/capsule.mp4", genre="video")
    ena = EnaFactice([fichier], ressources_ignorees=[video])
    archiveur = Archiveur(ena, transport_ok, tmp_path, queue.Queue())
    appels = []
    archiveur.manifeste = ManifesteQuiRefuseLesIgnorees(appels)

    resultat = archiveur.archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert (dossier / "a.pdf").exists()
    assert resultat.fichiers_ecrits == 1
    assert any(e.element == "capsule.mp4" for e in resultat.echecs)


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

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert (dossier / "ra-pport-1-.pdf").exists()


def test_relancer_ne_cree_jamais_de_doublon_numerote(tmp_path):
    # Le piege : assainir puis desambiguiser le nom AVANT de verifier le
    # manifeste ferait retelecharger tout le contenu sous « (2) » a chaque
    # relance, et l'archive doublerait de taille a chaque coupure reseau.
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    for _ in range(3):
        Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes.pdf"]


def test_collision_reelle_entre_deux_urls_differentes(tmp_path):
    # Deux ressources distinctes portant le meme nom doivent coexister.
    premier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    second = Fichier(nom="notes.pdf", url="/contenu/sitescours/y/notes.pdf?identifiant=b")
    archiveur = Archiveur(EnaFactice([premier, second]), transport_ok, tmp_path, queue.Queue())

    archiveur.archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
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

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes (2).pdf", "notes.pdf"]


def test_taille_illisible_dans_le_manifeste_ne_fait_pas_planter_la_reprise(tmp_path):
    # Un manifeste.csv est un fichier que l'utilisateur peut ouvrir et editer
    # a la main : une colonne "taille" vide ou non numerique ne doit jamais
    # lever de ValueError, mais etre traitee comme une taille inconnue.
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    dossier.mkdir(parents=True)
    (dossier / "notes.pdf").write_bytes(b"contenu")

    chemin_relatif = "2026-1 Hiver/ABC-1000 Éthique/Documents/Module 1/notes.pdf"
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

    plan = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Plan de cours" / "plan-de-cours.pdf"
    assert plan.exists()
    assert ena.plans_captures


def test_plan_de_cours_telecharge_depuis_url_officielle(tmp_path):
    # Quand la page /portail/cours porte l'url du PDF officiel, on la
    # telecharge comme un fichier normal ; le filet de secours ne doit pas
    # etre sollicite.
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=SESSION,
        url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=a",
    )
    ena = EnaFactice()
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([cours])

    plan = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Plan de cours" / "plan-de-cours.pdf"
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

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
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
        id_site="100006",
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

    def capturer_pdf_page_courante(self, destination):
        # Delegue a la page factice, dont pdf() echoue au premier appel : la
        # doublure de base reussirait toujours, et ce test porte precisement
        # sur l'isolation d'une impression ratee.
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.session.page.pdf(path=str(destination), format="A4", print_background=True)

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

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Section deux"
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
    assert "ABC-1000 Éthique" in contenu
    assert "Examen 1" in contenu


def test_pages_de_la_section_evaluations_capturees(tmp_path):
    # La liste des evaluations et le sommaire des resultats sont des pages a
    # part entiere de la section, pas seulement les fichiers deposes et le
    # tableau des notes : elles doivent etre capturees comme les pages de
    # module, dans Pages/.
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert (base / "Pages" / "evaluations.pdf").exists()
    assert (base / "Pages" / "sommaire-des-resultats.pdf").exists()


def test_description_et_resultats_d_evaluation_captures_dans_evaluations(tmp_path):
    # La description (consignes du travail) et l'onglet Resultats (retroaction
    # possible) de chaque evaluation sont ranges dans son sous-dossier sous
    # Évaluations/.
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    assert (dossier / "description.pdf").exists()
    assert (dossier / "resultats.pdf").exists()


def test_page_boite_de_depot_capturee_en_pdf(tmp_path):
    # La boite de depot elle-meme (dates, ponderation, fichiers a consulter)
    # doit etre capturee en PDF au meme titre que la description : les
    # fichiers deposes ne portent pas ces informations.
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    assert (dossier / "boite-de-depot.pdf").exists()


def test_echec_capture_boite_de_depot_isole(tmp_path):
    # Une capture ratee de la boite de depot ne doit couter que ce fichier,
    # ni les depots deja telecharges, ni la description, ni le cours.
    depot = Depot(nom="travail.docx", url="/contenu/sitescours/x/travail.docx?identifiant=a")
    ena = EnaFactice(depots=[depot], erreur_capture_pdf_pour=("boite-de-depot.pdf",))
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert any(e.element == "boite-de-depot.pdf" for e in resultat.echecs)
    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    assert not (dossier / "boite-de-depot.pdf").exists()
    assert (dossier / "travail.docx").exists()
    assert (dossier / "description.pdf").exists()


def test_visites_description_et_boite_de_depot_annoncees(tmp_path):
    # L'utilisateur doit pouvoir constater, dans la console, que l'outil est
    # bien alle sur la Description et la Boite de depot de chaque evaluation
    # -- au meme titre que les fichiers telecharges.
    evenements = queue.Queue()
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, evenements).archiver([COURS])

    visites = []
    while not evenements.empty():
        type_evenement, texte = evenements.get()
        if type_evenement == "visite":
            visites.append(texte)

    assert any("Description" in v and "TP1" in v for v in visites)
    assert any("Boîte de dépôt" in v and "TP1" in v for v in visites)


def test_fichiers_joints_description_recuperes(tmp_path):
    # Une description d'evaluation peut porter l'enonce d'un travail en piece
    # jointe : sans cette recuperation, ces consignes disparaitraient sans
    # laisser de trace a la fermeture de la plateforme.
    enonce = Fichier(nom="enonce.pdf", url="/contenu/sitescours/x/enonce.pdf?identifiant=a")
    ena = EnaFactice(fichiers_description=[enonce])
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
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

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    assert (dossier / "retroaction.pdf").exists()


def test_capture_de_page_evaluation_en_echec_isolee(tmp_path):
    # Une capture ratee (ici la description) ne doit couter que ce fichier,
    # jamais l'evaluation ni le cours : l'onglet Resultats de la meme
    # evaluation doit quand meme etre capture.
    ena = EnaFactice(erreur_capture_pdf_pour=("description.pdf",))
    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert any(e.element == "description.pdf" for e in resultat.echecs)
    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    assert not (dossier / "description.pdf").exists()
    assert (dossier / "resultats.pdf").exists()


def test_cours_sans_evaluation_ne_produit_ni_dossier_ni_erreur(tmp_path):
    class EnaSansEvaluations(EnaFactice):
        def evaluations(self, cours):
            return []

    resultat = Archiveur(
        EnaSansEvaluations(), transport_ok, tmp_path, queue.Queue()
    ).archiver([COURS])

    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    assert not (base / "Évaluations").exists()
    assert not (base / "Pages" / "evaluations.pdf").exists()
    assert not (base / "Pages" / "sommaire-des-resultats.pdf").exists()
    assert resultat.echecs == []


def test_archivage_dun_seul_cours_preserve_les_notes_des_autres_cours(tmp_path):
    # Defaut critique : notes-tous-cours.csv etait ecrase avec le seul
    # perimetre de l'execution en cours (voir archiver()), perdant les notes
    # des cours archives lors d'executions precedentes (--tout, --session).
    # Relancer --un-seul-cours pour reparer un cours ne doit jamais reduire
    # ce fichier a ce seul cours.
    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
    ena_premier = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena_premier, transport_ok, tmp_path, queue.Queue()).archiver([autre])

    ena_second = EnaFactice(notes=[Note(evaluation="TP1 - Projet", note="9", sur="10")])
    Archiveur(ena_second, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    contenu = (tmp_path / "notes-tous-cours.csv").read_text(encoding="utf-8-sig")
    assert "DEF-2000 Projet" in contenu
    assert "Examen 1" in contenu
    assert "ABC-1000 Éthique" in contenu
    assert "TP1 - Projet" in contenu


def test_ecriture_notes_consolidees_verrouillee_ne_plante_pas_larchivage(tmp_path):
    # Reproduit le plantage reel : notes-tous-cours.csv ouvert dans Excel
    # (PermissionError) ne doit couter qu'un Echec ordinaire, jamais faire
    # planter tout l'archivage alors que le contenu du cours est deja
    # telecharge avec succes sur disque.
    (tmp_path / "notes-tous-cours.csv").mkdir()
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    ena = EnaFactice([fichier], notes=[Note(evaluation="Examen 1", note="18", sur="20")])

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert resultat.fichiers_ecrits >= 1
    echec = next(e for e in resultat.echecs if e.element == "notes-tous-cours.csv")
    assert "tableur" in echec.cause.lower() or "excel" in echec.cause.lower()


def test_ecriture_notes_par_cours_verrouillee_isolee(tmp_path):
    base = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"
    (base / "notes.csv").mkdir(parents=True)
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    echec = next(e for e in resultat.echecs if e.element == "notes.csv")
    assert echec.cours == COURS.dossier()
    assert "tableur" in echec.cause.lower() or "excel" in echec.cause.lower()
    # Le plan de cours (autre partie du meme cours) doit rester recupere :
    # l'isolation ne doit couter que ce fichier de synthese.
    assert (base / "Plan de cours" / "plan-de-cours.pdf").exists()


def test_ecriture_depots_verrouillee_isolee(tmp_path):
    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Évaluations" / "TP1"
    dossier.mkdir(parents=True)
    (dossier / "depots.csv").mkdir()
    depot = Depot(nom="travail.docx", url="/contenu/sitescours/x/travail.docx?identifiant=a")
    ena = EnaFactice(depots=[depot])

    resultat = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert (dossier / "travail.docx").exists()
    echec = next(e for e in resultat.echecs if e.element == "depots.csv")
    assert "tableur" in echec.cause.lower() or "excel" in echec.cause.lower()


def test_erreur_ecriture_fichier_de_contenu_reste_un_echec_de_cours(tmp_path):
    # Contre-test : une erreur d'ecriture sur un fichier de CONTENU (pas de
    # synthese) doit continuer a etre traitee comme avant l'isolation des
    # fichiers de synthese -- ici avalee par l'isolation par cours de
    # archiver(), pas par la nouvelle isolation des fichiers de synthese.
    def transport_verrouille(url):
        class R:
            statut = 200

            def morceaux(self):
                yield b"debut"
                raise PermissionError("simule : fichier de contenu verrouille")

        return R()

    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    ena = EnaFactice([fichier])
    archiveur = Archiveur(ena, transport_verrouille, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS, autre])

    assert len(resultat.echecs) == 2
    assert all(e.element == "(cours entier)" for e in resultat.echecs)
    assert all("verrouille" in e.cause for e in resultat.echecs)


def test_arborescence_des_depots_existants_inchangee(tmp_path):
    # Le dossier des evaluations porte desormais le nom "Evaluations" (decision
    # de l'utilisateur, qui accepte le retelechargement ponctuel que ce
    # renommage entraine sur les archives deja constituees) : chaque evaluation
    # y garde son sous-dossier a son titre, avec ses depots, ses pieces
    # jointes de description et le fichier de metadonnees des depots.
    depot = Depot(nom="travail.docx", url="/contenu/sitescours/x/travail.docx?identifiant=a")
    ena = EnaFactice(depots=[depot])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    attendu = (
        tmp_path
        / "2026-1 Hiver"
        / "ABC-1000 Éthique"
        / "Évaluations"
        / "TP1"
        / "travail.docx"
    )
    assert attendu.exists()


class EnaQuiDistingueLesCaptures(EnaAvecPages):
    """Separe les captures PDF qui ont exige une navigation de celles prises
    sur la page deja ouverte, pour mesurer les rechargements inutiles."""

    def __init__(self, pages, fichiers_par_id_page):
        super().__init__(pages, fichiers_par_id_page)
        self.captures_avec_navigation = []
        self.captures_sur_page_courante = []

    def capturer_pdf(self, chemin, destination):
        self.captures_avec_navigation.append(destination.name)
        super().capturer_pdf(chemin, destination)

    def capturer_pdf_page_courante(self, destination):
        self.captures_sur_page_courante.append(destination.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 factice")


def test_la_page_lue_pour_ses_fichiers_est_imprimee_sans_rechargement(tmp_path):
    # Chaque navigation est un aller-retour ADF de plusieurs secondes. La
    # lecture des fichiers d'une page y amene deja le navigateur : recharger
    # la meme URL pour l'imprimer doublait le cout de chaque module. Constate
    # en conditions reelles, l'operateur voyant la meme page se charger
    # plusieurs fois de suite.
    pages = [
        PageDeModule(id_page="1", chemin=("Section A",)),
        PageDeModule(id_page="2", chemin=("Section B",)),
    ]
    ena = EnaQuiDistingueLesCaptures(pages=pages, fichiers_par_id_page={"1": [], "2": []})

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert sorted(ena.captures_sur_page_courante) == [
        "Module 1 - Section A.pdf",
        "Module 1 - Section B.pdf",
    ]
    assert [n for n in ena.captures_avec_navigation if n.startswith("Module 1")] == []


def test_une_page_illisible_est_quand_meme_imprimee_par_navigation(tmp_path):
    # Quand la lecture des fichiers a echoue, le navigateur n'est PAS sur la
    # bonne page : imprimer la page courante produirait un PDF d'une autre
    # page, silencieusement faux. Il faut alors naviguer.
    pages = [PageDeModule(id_page="1", chemin=("Section A",))]
    ena = EnaQuiDistingueLesCaptures(pages=pages, fichiers_par_id_page={"1": []})
    ena._erreur_id_page = "1"

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert ena.captures_sur_page_courante == []
    # Filtre sur la page de module : le cours capture aussi les pages de
    # synthese des evaluations, qui passent par le meme chemin.
    assert "Module 1 - Section A.pdf" in ena.captures_avec_navigation


class EnaSansAucunContenu(EnaFactice):
    """Site lu sans aucun module, evaluation, note ni section de menu :
    exactement ce qu'avaient donne quatre cours reels lus trop tot."""

    def modules(self, cours):
        return []

    def evaluations(self, cours):
        return []

    def resultats(self, cours):
        return []

    def parcourir_menu(self, cours, action):
        return 0


def test_un_cours_sans_aucun_contenu_est_un_echec_au_rapport(tmp_path):
    # Quatre cours reels ressortis avec leur seul plan de cours, et pas une
    # ligne au rapport. Un site de cours porte toujours au moins un module,
    # une evaluation ou une section : n'en trouver aucun est une lecture
    # ratee, a signaler.
    from extracteur.archiveur import MESSAGE_COURS_SANS_CONTENU

    resultat = Archiveur(EnaSansAucunContenu(), transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert [e.cause for e in resultat.echecs] == [MESSAGE_COURS_SANS_CONTENU]
    assert resultat.echecs[0].element == "(cours entier)"


def test_un_cours_avec_seulement_des_notes_n_est_pas_un_echec(tmp_path):
    # Contre-epreuve : des notes suffisent a prouver que le site a ete lu.
    class EnaAvecNotesSeulement(EnaSansAucunContenu):
        def resultats(self, cours):
            return [Note(evaluation="Examen", note="80")]

    resultat = Archiveur(EnaAvecNotesSeulement(), transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert not any(e.element == "(cours entier)" for e in resultat.echecs)

