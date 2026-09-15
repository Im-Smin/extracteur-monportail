"""Tests de la fenetre de controle.

Tout ce qui decide quelque chose (validation des champs, choix du mode,
production du ZIP, texte de l'etat final) vit dans des fonctions libres de
Tkinter, testees ici sans ouvrir la moindre fenetre. Ne reste sous Tk que le
placement des widgets et la frontiere entre les deux fils, couverts par des
tests de fumee qui se sautent d'eux-memes quand aucun affichage n'est
disponible.
"""

import inspect
import threading
from pathlib import Path

import pytest

from extracteur.modele import Echec, Resultat
from extracteur.ui import (
    PORTEE_COURS,
    PORTEE_SESSION,
    PORTEE_TOUT,
    TEXTE_ACCUEIL,
    ChampManquant,
    Fenetre,
    _modes_par_defaut,
    executer_archivage,
    nom_du_zip,
    valider_les_champs,
    verdict,
)

CONTROLE_PROPRE = {"inscrits": 3, "manquants": [], "taille_incorrecte": []}


# --- valider_les_champs : rejeter une saisie fautive AVANT la connexion ---


def test_valider_tout_n_a_pas_d_argument():
    assert valider_les_champs(PORTEE_TOUT, "C:/archive", "", "") == ""


def test_valider_refuse_une_destination_vide():
    with pytest.raises(ChampManquant, match="emplacement"):
        valider_les_champs(PORTEE_TOUT, "", "", "")


def test_valider_refuse_une_destination_faite_d_espaces():
    with pytest.raises(ChampManquant, match="emplacement"):
        valider_les_champs(PORTEE_TOUT, "   ", "", "")


def test_valider_session_rend_le_libelle_nettoye():
    assert valider_les_champs(PORTEE_SESSION, "C:/a", "  Automne 2022 ", "") == "Automne 2022"


def test_valider_refuse_une_session_sans_libelle():
    with pytest.raises(ChampManquant, match="libelle"):
        valider_les_champs(PORTEE_SESSION, "C:/a", "   ", "")


def test_valider_cours_rend_l_id_site_nettoye():
    assert valider_les_champs(PORTEE_COURS, "C:/a", "", " 181216 ") == "181216"


def test_valider_refuse_un_cours_sans_id_site():
    with pytest.raises(ChampManquant, match="idSite"):
        valider_les_champs(PORTEE_COURS, "C:/a", "", "")


def test_valider_refuse_un_id_site_non_numerique():
    # Le cout de la faute de frappe : un idSite non numerique ne correspondra
    # jamais a rien, et ne le constater qu'apres le MFA couterait plusieurs
    # minutes a chaque essai.
    with pytest.raises(ChampManquant, match="nombre"):
        valider_les_champs(PORTEE_COURS, "C:/a", "", "MQT-2101")


def test_valider_ignore_le_champ_hors_portee():
    # Un libelle de session reste dans son champ pendant qu'on archive tout :
    # il ne doit ni bloquer ni etre pris pour argument.
    assert valider_les_champs(PORTEE_TOUT, "C:/a", "Automne 2022", "999") == ""


# --- nom_du_zip : a cote du dossier, jamais dedans ---


def test_nom_du_zip_est_pose_a_cote_du_dossier(tmp_path):
    cible = nom_du_zip(tmp_path / "Archive monPortail")

    assert cible == tmp_path / "Archive monPortail.zip"
    assert cible.parent == tmp_path


# --- verdict : ne jamais annoncer complet ce qui ne l'est pas ---


def test_verdict_complet_seulement_sur_code_zero():
    titre, detail, complet = verdict(
        0, Resultat(fichiers_ecrits=12, fichiers_sautes=3), CONTROLE_PROPRE, Path("a.zip")
    )

    assert complet is True
    assert titre == "TERMINE"
    assert "12" in detail
    assert "a.zip" in detail


def test_verdict_echecs_partiels_annonce_incomplet():
    resultat = Resultat(fichiers_ecrits=200, echecs=[Echec("X", "y.pdf", "404")])

    titre, detail, complet = verdict(4, resultat, CONTROLE_PROPRE, Path("a.zip"))

    assert complet is False
    assert "INCOMPLETE" in titre
    assert "_rapport.html" in detail


def test_verdict_annulation_dit_combien_de_cours_restent():
    resultat = Resultat(fichiers_ecrits=40, cours_non_tentes=7)

    titre, detail, complet = verdict(4, resultat, CONTROLE_PROPRE)

    assert complet is False
    assert "7 cours" in detail
    assert "relancez" in detail.lower()


def test_verdict_anomalies_de_verification_annoncees_meme_sans_echec():
    controle = {"inscrits": 5, "manquants": ["a.pdf"], "taille_incorrecte": ["b.pdf"]}

    _titre, detail, complet = verdict(4, Resultat(fichiers_ecrits=5), controle)

    assert complet is False
    assert "2 anomalies" in detail


def test_verdict_cours_introuvable_est_un_arret_pas_un_succes():
    titre, detail, complet = verdict(2)

    assert complet is False
    assert titre == "ARRETE"
    assert "idSite" in detail


def test_verdict_session_expiree_invite_a_relancer():
    titre, detail, complet = verdict(3)

    assert complet is False
    assert "EXPIREE" in titre
    assert "reprise" in detail.lower()


def test_verdict_sans_resultat_ne_promet_aucun_zip():
    _titre, detail, complet = verdict(1)

    assert complet is False
    assert "rien n'a ete compresse" in detail


def test_verdict_code_zero_sans_resultat_n_est_pas_un_succes():
    # Etat impossible en pratique, mais le defaut doit pencher du bon cote :
    # sans compteurs, on ne peut rien certifier de complet.
    _titre, _detail, complet = verdict(0, None, None)

    assert complet is False


# --- executer_archivage : enchainement archivage -> ZIP ---


class ModesFactices:
    """Doublure des trois modes du coeur : enregistre l'appel recu, joue le
    resultat demande, et n'ouvre evidemment aucun navigateur."""

    def __init__(self, code=0, resultat=None, controle=None, effet=None):
        self.appels: list = []
        self._code = code
        self._resultat = resultat
        self._controle = controle or CONTROLE_PROPRE
        self._effet = effet

    def _jouer(self, portee, argument, **reste):
        self.appels.append((portee, argument, reste))
        if self._effet is not None:
            self._effet()
        if self._resultat is not None:
            reste["sur_fin"](self._resultat, self._controle, Path("_rapport.html"))
        return self._code

    def dictionnaire(self) -> dict:
        return {
            portee: (lambda argument, _p=portee, **reste: self._jouer(_p, argument, **reste))
            for portee in (PORTEE_TOUT, PORTEE_SESSION, PORTEE_COURS)
        }


def _sortie():
    lignes: list = []
    return lignes, lignes.append


def test_executer_appelle_le_mode_choisi_avec_son_argument(tmp_path):
    modes = ModesFactices(code=0, resultat=Resultat(fichiers_ecrits=1))
    lignes, ecrire = _sortie()

    executer_archivage(
        PORTEE_SESSION,
        "Automne 2022",
        tmp_path,
        ecrire,
        ecrire,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: cible,
    )

    portee, argument, reste = modes.appels[0]
    assert portee == PORTEE_SESSION
    assert argument == "Automne 2022"
    assert reste["destination"] == tmp_path


def test_executer_compresse_apres_un_archivage_complet(tmp_path):
    modes = ModesFactices(code=0, resultat=Resultat(fichiers_ecrits=4))
    compressions: list = []
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: compressions.append((racine, cible)) or cible,
    )

    assert etat["code"] == 0
    assert etat["chemin_zip"] == tmp_path / "Archive.zip"
    assert compressions == [(tmp_path / "Archive", tmp_path / "Archive.zip")]
    assert any("Archive.zip" in ligne for ligne in lignes)


def test_executer_compresse_aussi_une_archive_partielle(tmp_path):
    # Un archivage qui a produit des fichiers ET des echecs reste une archive
    # a conserver : la plateforme ferme, refuser le ZIP ferait perdre ce qui
    # a bien ete recupere. Ce qui change, c'est le verdict, pas le ZIP.
    modes = ModesFactices(
        code=4, resultat=Resultat(fichiers_ecrits=200, echecs=[Echec("X", "y", "404")])
    )
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: cible,
    )

    assert etat["code"] == 4
    assert etat["chemin_zip"] == tmp_path / "Archive.zip"


def test_executer_ne_compresse_rien_quand_l_archivage_n_a_pas_abouti(tmp_path):
    # Code 2 (cours introuvable) : sur_fin n'a jamais ete appele, donc aucune
    # verification n'a eu lieu. Compresser ici fabriquerait un fichier
    # d'apparence definitive a partir de rien.
    modes = ModesFactices(code=2, resultat=None)
    compressions: list = []
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_COURS,
        "999999",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: compressions.append(cible) or cible,
    )

    assert etat["code"] == 2
    assert etat["chemin_zip"] is None
    assert compressions == []


def test_executer_ne_compresse_rien_apres_une_interruption(tmp_path):
    annulation = threading.Event()
    modes = ModesFactices(
        code=4,
        resultat=Resultat(fichiers_ecrits=40, cours_non_tentes=7),
        effet=annulation.set,
    )
    compressions: list = []
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        annulation=annulation,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: compressions.append(cible) or cible,
    )

    assert etat["chemin_zip"] is None
    assert compressions == []
    assert any("Relancez" in ligne for ligne in lignes)


def test_executer_transmet_l_annulation_au_coeur(tmp_path):
    annulation = threading.Event()
    modes = ModesFactices(code=0, resultat=Resultat(fichiers_ecrits=1))
    lignes, ecrire = _sortie()

    executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path,
        ecrire,
        ecrire,
        annulation=annulation,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: cible,
    )

    assert modes.appels[0][2]["annulation"] is annulation


def test_executer_survit_a_un_zip_qui_echoue(tmp_path):
    # Fichier verrouille, disque plein : l'archivage lui-meme a reussi et ses
    # centaines de fichiers sont sur disque. L'echec du ZIP doit etre dit, pas
    # faire perdre le resultat -- et la commande de rattrapage doit etre
    # donnee telle quelle.
    modes = ModesFactices(code=0, resultat=Resultat(fichiers_ecrits=300))
    lignes, ecrire = _sortie()

    def compresser_qui_echoue(racine, cible):
        raise PermissionError("fichier verrouille")

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        modes=modes.dictionnaire(),
        compresser=compresser_qui_echoue,
    )

    assert etat["chemin_zip"] is None
    assert etat["resultat"].fichiers_ecrits == 300
    texte = "\n".join(lignes)
    assert "fichier verrouille" in texte
    assert "--zip" in texte


def test_executer_ne_laisse_jamais_remonter_une_exception(tmp_path):
    def mode_qui_explose(argument, **reste):
        raise RuntimeError("playwright a disparu")

    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path,
        ecrire,
        ecrire,
        modes={PORTEE_TOUT: mode_qui_explose},
        compresser=lambda racine, cible: cible,
    )

    assert etat["code"] == 1
    assert etat["chemin_zip"] is None
    assert any("playwright a disparu" in ligne for ligne in lignes)


def test_executer_compresse_vraiment_le_dossier(tmp_path):
    # Contre-epreuve sans doublure de compression : le vrai creer_zip doit
    # accepter les arguments que la fenetre lui passe, et produire un fichier.
    racine = tmp_path / "Archive"
    racine.mkdir()
    (racine / "notes.csv").write_text("a,b\n", encoding="utf-8")
    modes = ModesFactices(code=0, resultat=Resultat(fichiers_ecrits=1))
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT, "", racine, ecrire, ecrire, modes=modes.dictionnaire()
    )

    assert etat["chemin_zip"].exists()
    assert etat["chemin_zip"].stat().st_size > 0


# --- garde-fou de signature entre la fenetre et le coeur ---


def test_les_modes_reels_acceptent_tous_les_arguments_de_la_fenetre():
    # Sans ce test, renommer un parametre dans __main__.py laisserait la suite
    # au vert et ne casserait qu'au premier clic sur Commencer, apres le MFA.
    from extracteur.__main__ import _session, _tout, _un_seul_cours

    attendus = {"destination", "imprimer", "imprimer_erreur", "annulation", "sur_fin"}
    for fonction in (_tout, _session, _un_seul_cours):
        parametres = set(inspect.signature(fonction).parameters)
        assert attendus <= parametres, f"{fonction.__name__} : {attendus - parametres}"


def test_modes_par_defaut_couvre_les_trois_portees():
    modes = _modes_par_defaut()

    assert set(modes) == {PORTEE_TOUT, PORTEE_SESSION, PORTEE_COURS}


# --- fumee Tkinter : la fenetre se construit et reagit sans exploser ---


@pytest.fixture(scope="module")
def _interpreteur_tk():
    """Un seul interpreteur Tk pour tout le module.

    Creer puis detruire plusieurs Tk() dans un meme processus echoue par
    intermittence sous Windows (« Can't find a usable tk.tcl », puis
    « invalid command name tcl_findLibrary ») : chaque test recoit donc un
    Toplevel de cette racine unique, jamais une racine a lui.
    """
    tkinter = pytest.importorskip("tkinter")
    try:
        racine = tkinter.Tk()
    except tkinter.TclError as erreur:  # pas d'affichage disponible
        pytest.skip(f"Tk indisponible : {erreur}")
    racine.withdraw()
    yield racine
    racine.destroy()


@pytest.fixture
def fenetre_tk(_interpreteur_tk):
    """Fabrique de Fenetre posee sur un Toplevel jetable."""
    import tkinter

    ouverts: list = []

    def fabriquer(destination):
        sommet = tkinter.Toplevel(_interpreteur_tk)
        sommet.withdraw()
        ouverts.append(sommet)
        return Fenetre(sommet, destination)

    yield fabriquer
    for sommet in ouverts:
        sommet.destroy()


def test_la_fenetre_se_construit_et_journalise(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)
    fenetre._ecrire("bonjour")

    contenu = fenetre.journal.get("1.0", "end")
    assert contenu.startswith("ARCHIVEUR MONPORTAIL")  # le mode d'emploi, en tete
    assert "bonjour" in contenu
    assert fenetre.portee.get() == PORTEE_TOUT


def test_le_texte_d_accueil_dit_ce_que_le_programme_ne_recupere_pas():
    # Le point le plus couteux a decouvrir trop tard : ce qui n'est PAS
    # archive doit etre recupere a la main pendant que la plateforme existe
    # encore. Si cette liste disparaissait du texte, le manque ne serait
    # constate qu'apres le 1er novembre 2026.
    assert "N'EST PAS ARCHIVE" in TEXTE_ACCUEIL
    for exclu in ("annonces", "forums", "videos", "Autres activites", "Bibliographie"):
        assert exclu in TEXTE_ACCUEIL, exclu


def test_le_texte_d_accueil_annonce_la_fenetre_de_navigateur_separee():
    # La confusion qui a deja coute une soiree entiere : se connecter dans son
    # propre Chrome au lieu de la fenetre ouverte par le programme.
    assert "SEPAREE" in TEXTE_ACCUEIL
    assert "DEDANS" in TEXTE_ACCUEIL
    assert "chaque lancement" in TEXTE_ACCUEIL


def test_le_texte_d_accueil_decrit_ce_qui_sera_cree(tmp_path):
    for produit in (
        "Plan de cours",
        "Documents",
        "Pages",
        "Évaluations",
        "notes.csv",
        "notes-tous-cours.csv",
        "manifeste.csv",
        "_rapport.html",
        ".zip",
    ):
        assert produit in TEXTE_ACCUEIL, produit


def test_commencer_remplace_le_mode_d_emploi_par_la_progression(tmp_path, fenetre_tk, monkeypatch):
    # Le mode d'emploi fait une cinquantaine de lignes : le laisser en place
    # enfouirait la progression, seule chose a suivre pendant l'heure qui
    # vient. Il revient au prochain lancement.
    fenetre = fenetre_tk(tmp_path)
    monkeypatch.setattr("extracteur.ui.threading.Thread", _FilFactice)

    fenetre._commencer()

    contenu = fenetre.journal.get("1.0", "end")
    assert "ARCHIVEUR MONPORTAIL" not in contenu
    assert "=== Archivage vers" in contenu
    assert "DEDANS" in contenu  # l'essentiel de l'avertissement est redit ici


class _FilFactice:
    """Doublure de threading.Thread : retient l'appel, ne demarre rien.

    Sans elle, tester le bouton Commencer ouvrirait un vrai navigateur.
    """

    def __init__(self, target=None, args=(), daemon=None, **reste):
        self.target = target
        self.args = args
        self.demarre = False

    def start(self):
        self.demarre = True

    def is_alive(self):
        return False


def test_la_fenetre_grise_les_champs_hors_portee(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)

    assert str(fenetre.champ_session["state"]) == "disabled"
    assert str(fenetre.champ_cours["state"]) == "disabled"

    fenetre.portee.set(PORTEE_SESSION)
    fenetre._rafraichir_champs()

    assert str(fenetre.champ_session["state"]) == "normal"
    assert str(fenetre.champ_cours["state"]) == "disabled"


def test_la_fenetre_refuse_de_commencer_sur_un_id_site_fautif(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)
    fenetre.portee.set(PORTEE_COURS)
    fenetre.id_site.set("MQT-2101")

    fenetre._commencer()

    assert fenetre.fil is None  # aucun navigateur n'a ete ouvert
    assert "nombre" in fenetre.journal.get("1.0", "end")
    assert str(fenetre.bouton_commencer["state"]) == "normal"


def test_la_fenetre_affiche_un_verdict_incomplet_sans_le_maquiller(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)

    fenetre._conclure(
        {
            "code": 4,
            "resultat": Resultat(fichiers_ecrits=2, echecs=[Echec("X", "y", "404")]),
            "controle": CONTROLE_PROPRE,
            "chemin_zip": None,
        }
    )

    contenu = fenetre.journal.get("1.0", "end")
    assert "INCOMPLETE" in contenu
    assert str(fenetre.bouton_commencer["state"]) == "normal"
    assert str(fenetre.bouton_arreter["state"]) == "disabled"


def test_la_fenetre_affiche_termine_sur_un_archivage_complet(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)

    fenetre._conclure(
        {
            "code": 0,
            "resultat": Resultat(fichiers_ecrits=278),
            "controle": CONTROLE_PROPRE,
            "chemin_zip": tmp_path / "Archive.zip",
        }
    )

    contenu = fenetre.journal.get("1.0", "end")
    assert "=== TERMINE ===" in contenu
    assert "Archive.zip" in contenu


def test_le_bouton_arreter_pose_l_evenement_sans_toucher_au_navigateur(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)

    fenetre._arreter()

    assert fenetre.annulation.is_set()
    assert str(fenetre.bouton_arreter["state"]) == "disabled"


def test_la_file_est_videe_vers_le_journal(tmp_path, fenetre_tk):
    # Preuve que la frontiere entre les fils fonctionne : rien n'apparait tant
    # que la boucle d'affichage n'a pas lu la file.
    fenetre = fenetre_tk(tmp_path)
    fenetre.evenements.put(("ligne", "depuis le fil de travail"))

    assert "depuis le fil de travail" not in fenetre.journal.get("1.0", "end")

    fenetre._vider_la_file()

    assert "depuis le fil de travail" in fenetre.journal.get("1.0", "end")


def test_le_journal_ne_grossit_pas_sans_fin(tmp_path, monkeypatch, fenetre_tk):
    monkeypatch.setattr("extracteur.ui.LIGNES_JOURNAL_MAX", 50)
    fenetre = fenetre_tk(tmp_path)
    for numero in range(300):
        fenetre._ecrire(f"ligne {numero}")

    contenu = fenetre.journal.get("1.0", "end")
    assert contenu.count("\n") <= 52  # le plafond, a une ligne de fin pres
    assert "ligne 299" in contenu  # ce sont bien les dernieres qui restent
    assert "ligne 0\n" not in contenu
# --- constats de revue : ZIP, fermeture par le X, publication de « fin » ---


def test_executer_compresse_quand_l_arret_arrive_trop_tard(tmp_path):
    # L'archiveur ne lit le drapeau d'annulation qu'a la frontiere de chaque
    # cours : cliquer Arreter pendant le DERNIER cours le laisse finir, et
    # l'archive est complete. Lire le drapeau plutot que cours_non_tentes
    # ferait sauter la compression en annoncant une interruption qui n'a rien
    # coute.
    annulation = threading.Event()
    modes = ModesFactices(
        code=0,
        resultat=Resultat(fichiers_ecrits=278, cours_non_tentes=0),
        effet=annulation.set,
    )
    compressions: list = []
    lignes, ecrire = _sortie()

    etat = executer_archivage(
        PORTEE_TOUT,
        "",
        tmp_path / "Archive",
        ecrire,
        ecrire,
        annulation=annulation,
        modes=modes.dictionnaire(),
        compresser=lambda racine, cible: compressions.append(cible) or cible,
    )

    assert annulation.is_set()  # le drapeau est bien reste pose
    assert etat["chemin_zip"] == tmp_path / "Archive.zip"
    assert compressions == [tmp_path / "Archive.zip"]
    assert not any("interrompu" in ligne.lower() for ligne in lignes)


def test_fin_est_publie_meme_quand_le_travail_explose(tmp_path, fenetre_tk):
    # Sans cet evenement, la fenetre resterait figee pour toujours : Commencer
    # desactive, Arreter actif, et aucun fil vivant pour lui repondre.
    fenetre = fenetre_tk(tmp_path)

    def exploser(*args, **reste):
        raise KeyboardInterrupt("arret brutal")

    import extracteur.ui

    ancien = extracteur.ui.executer_archivage
    extracteur.ui.executer_archivage = exploser
    try:
        with pytest.raises(KeyboardInterrupt):
            fenetre._travailler(PORTEE_TOUT, "", tmp_path)
    finally:
        extracteur.ui.executer_archivage = ancien

    evenements = []
    while not fenetre.evenements.empty():
        evenements.append(fenetre.evenements.get_nowait())

    assert evenements[-1][0] == "fin"
    assert evenements[-1][1]["resultat"] is None


def test_fermer_au_repos_detruit_la_fenetre_tout_de_suite(tmp_path, fenetre_tk):
    fenetre = fenetre_tk(tmp_path)
    detruites: list = []
    fenetre.racine.destroy = lambda: detruites.append(True)

    fenetre._fermer()

    assert detruites == [True]
    assert fenetre.fermeture_demandee is False


def test_fermer_pendant_un_archivage_attend_le_fil(tmp_path, fenetre_tk):
    # Le geste naturel -- le X du systeme -- ne doit jamais tuer le fil avant
    # qu'il ait ferme Chromium et ecrit _rapport.html : sans quoi
    # l'utilisateur garde une archive tronquee et aucune trace de ce qui
    # manque.
    fenetre = fenetre_tk(tmp_path)
    detruites: list = []
    fenetre.racine.destroy = lambda: detruites.append(True)

    barriere = threading.Event()
    fenetre.fil = threading.Thread(target=barriere.wait, daemon=True)
    fenetre.fil.start()
    try:
        fenetre._fermer()

        assert detruites == []  # rien n'est detruit tant que le fil vit
        assert fenetre.fermeture_demandee is True
        assert fenetre.annulation.is_set()  # l'arret a bien ete demande

        fenetre._vider_la_file()
        assert detruites == []  # toujours vivant, toujours pas de destruction
    finally:
        barriere.set()
        fenetre.fil.join(timeout=5)

    fenetre._vider_la_file()

    assert detruites == [True]  # fil mort, file videe, fenetre fermee


def test_le_cycle_complet_commencer_puis_conclure(tmp_path, fenetre_tk):
    # Le seul test qui parcoure la chaine entiere : clic sur Commencer, vrai
    # fil de travail, depot dans la file, vidage par la boucle d'affichage,
    # verdict. Sans lui, une regression sur la livraison de « fin » passerait
    # inapercue jusqu'au premier usage reel.
    import extracteur.ui

    def travail_factice(portee, argument, destination, imprimer, imprimer_erreur, **reste):
        imprimer("deux fichiers recuperes")
        return {
            "code": 0,
            "resultat": Resultat(fichiers_ecrits=2),
            "controle": CONTROLE_PROPRE,
            "chemin_rapport": None,
            "chemin_zip": destination.parent / "Archive.zip",
        }

    fenetre = fenetre_tk(tmp_path)
    ancien = extracteur.ui.executer_archivage
    extracteur.ui.executer_archivage = travail_factice
    try:
        fenetre._commencer()
        assert fenetre.fil is not None
        fenetre.fil.join(timeout=5)
        assert not fenetre.fil.is_alive()
        fenetre._vider_la_file()
    finally:
        extracteur.ui.executer_archivage = ancien

    contenu = fenetre.journal.get("1.0", "end")
    assert "deux fichiers recuperes" in contenu
    assert "=== TERMINE ===" in contenu
    assert "Archive.zip" in contenu
    assert str(fenetre.bouton_commencer["state"]) == "normal"
    assert str(fenetre.bouton_arreter["state"]) == "disabled"
