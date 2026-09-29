import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright

from extracteur.ena import (
    BASE,
    CLASSE_TEXTE_OPTION_SESSION,
    LIMITE_PAGES_MODULE,
    SELECTEUR_CHAMP_SESSION,
    SELECTEUR_OPTION_SESSION,
    SELECTEUR_SQUELETTES_CHARGEMENT,
    URL,
    Ena,
    NavigateurBloque,
    SelecteurSessionsIllisible,
    SelecteurSessionsIndisponible,
    TropDePagesDansUnModule,
)
from extracteur.extraction import (
    ids_cours_suivis_depuis_reponse,
    session_depuis_libelle,
    urls_plans_de_cours_depuis_reponse,
)
from extracteur.modele import Cours, Evaluation, Module, Session
from extracteur.telechargement import SessionExpiree


class LocatorFactice:
    """Simule un locator Playwright avec une methode count() et wait_for().

    wait_for() reussit si le locator a deja au moins un element (nombre > 0),
    leve un depassement de delai sinon -- comme le ferait un vrai Locator
    Playwright interroge sur un element absent du DOM.
    """

    def __init__(self, nombre):
        self._nombre = nombre

    def count(self):
        return self._nombre

    def wait_for(self, timeout=None):
        if self._nombre <= 0:
            raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")

    def all_text_contents(self):
        # Aucun appelant reel ne demande le texte d'un locator construit
        # avec un nombre > 0 dans les tests actuels ; [] est le seul
        # comportement coherent avec un locator qui ne represente rien de
        # concret (utilise ici surtout comme repli "rien trouve").
        return []

    def filter(self, has_text=None):
        # Meme rationnel que all_text_contents() : ce doublure ne represente
        # jamais un ensemble d'options reelles, filtrer ne peut donc rien
        # trouver de plus.
        return LocatorFactice(0)


class PageFactice:
    """Enregistre les URL visitees et rend le HTML programme.

    Authentifiee par defaut (lien /ena/site/ present) : les tests de
    navigation qui ne portent pas sur l'authentification n'ont pas a la
    simuler explicitement. Voir PageNonAuthentifiee pour l'inverse.
    """

    def __init__(self, pages):
        self.pages = pages
        self.visitees = []
        self.url = ""

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = url

    def content(self):
        for motif, html in self.pages.items():
            if motif in self.url:
                return html
        return "<html></html>"

    def click(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def pdf(self, **_kwargs):
        pass

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        return LocatorFactice(0)

    def get_by_text(self, _texte):
        return LocatorFactice(0)


class SessionFactice:
    def __init__(self, pages):
        self.page = PageFactice(pages)


class PageNonAuthentifiee(PageFactice):
    """Simule la page de connexion servie quand la session Microsoft expire en
    cours de navigation : la redirection SSO fait atterrir sur un hote de
    connexion federee, hors du domaine ulaval.ca, quel que soit le chemin
    demande (est_page_authentifiee ne verifie plus que le nom d'hote)."""

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = "https://login.microsoftonline.com/common/oauth2/authorize"


COURS_QUELCONQUE = Cours(
    id_site="1", sigle=None, titre="X", session=Session(code="202601", libelle="Hiver 2026")
)


def test_modules_leve_session_expiree_si_page_non_authentifiee():
    # Sans ce garde-fou, une session expiree en cours de navigation rend une
    # page de connexion vide, que modules_depuis_html lirait comme "aucun
    # module" : une archive silencieusement incomplete.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.modules(COURS_QUELCONQUE)


def test_modules_page_authentifiee_laisse_passer():
    html = '<a href="/ena/site/module?idSite=1&idModule=1&editionModule=false">M</a>'
    ena = Ena(SessionFactice({"/ena/site/modules": html}))

    assert len(ena.modules(COURS_QUELCONQUE)) == 1


def test_evaluations_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.evaluations(COURS_QUELCONQUE)


def test_fichiers_de_depot_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.fichiers_de_depot(Evaluation(id_site="1", id_evaluation="1", titre="T"))


def test_resultats_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.resultats(COURS_QUELCONQUE)


def test_fichiers_de_description_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.fichiers_de_description(Evaluation(id_site="1", id_evaluation="1", titre="T"))


def test_fichiers_de_resultats_evaluation_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.fichiers_de_resultats_evaluation(Evaluation(id_site="1", id_evaluation="1", titre="T"))


def test_parcourir_menu_leve_session_expiree_si_page_non_authentifiee():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.parcourir_menu(COURS_QUELCONQUE, lambda libelle, html: None)


def test_fichiers_du_module_leve_session_expiree_si_page_non_authentifiee():
    # Sans ce garde-fou, une session expiree pendant la boucle des modules
    # rend une page de connexion vide, que fichiers_depuis_html lirait comme
    # "aucun fichier" : une archive silencieusement incomplete.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.fichiers_du_module(Module(id_site="1", id_module="1", titre="M"))


def test_capturer_pdf_leve_session_expiree_si_page_non_authentifiee(tmp_path):
    # Le plus grave : sans ce garde-fou, capturer_pdf imprime la page de
    # connexion et la range sous le nom du module, comme une reussite. Un
    # faux contenu presente comme authentique est pire qu'un contenu manquant.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.capturer_pdf("/ena/site/module?idSite=1&idModule=1", tmp_path / "m.pdf")

    assert not (tmp_path / "m.pdf").exists()


def test_urls_canoniques_sont_bien_formees():
    assert URL.modules("100001") == "/ena/site/modules?idSite=100001"
    assert URL.evaluations("100001") == "/ena/site/evaluations?idSite=100001"
    assert URL.resultats("100001") == "/ena/site/resultats?idSite=100001"
    assert URL.boite_depot("100001", "1035434") == (
        "/ena/site/evaluation?idSite=100001&idEvaluation=1035434&onglet=boiteDepots"
    )
    assert URL.module("100001", "1795743") == (
        "/ena/site/module?idSite=100001&idModule=1795743&editionModule=false"
    )
    assert URL.redirection("100001", "liste_modules") == (
        "/lieninterne/redirection/100001/liste_modules"
    )
    assert URL.evaluation("100001", "1035434") == (
        "/ena/site/evaluation?idSite=100001&idEvaluation=1035434"
    )
    assert URL.evaluation_resultats("100001", "1035434") == (
        "/ena/site/evaluation?idSite=100001&idEvaluation=1035434&onglet=resultats"
    )


def test_url_module_ajoute_idpage_quand_fourni():
    # Sans idPage explicite, le serveur ADF sert un onglet imprevisible (le
    # dernier consulte dans la session) : voir docs/api-monportail.md, etape 3.
    assert URL.module("100001", "1795743", "4874493") == (
        "/ena/site/module?idSite=100001&idModule=1795743"
        "&editionModule=false&idPage=4874493"
    )


def test_url_module_sans_idpage_reste_identique():
    # Les appels existants, sans idPage, ne doivent pas changer d'URL.
    assert URL.module("100001", "1795743") == (
        "/ena/site/module?idSite=100001&idModule=1795743&editionModule=false"
    )


class PageAvecPlanDeCours(PageFactice):
    """Simule le routeur lieninterne/redirection : seule une section donnee
    mene reellement au plan de cours ; les autres restent sur le routeur lui-meme,
    ce qui echoue au critere « URL sous /ena/site/ »."""

    URL_PLAN = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=100001&idModule=999"

    def __init__(self, section_valide, html_plan):
        super().__init__({})
        self.section_valide = section_valide
        self.html_plan = html_plan

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = self.URL_PLAN if f"/{self.section_valide}" in url else url

    def content(self):
        return self.html_plan if self.url == self.URL_PLAN else "<html></html>"


def test_plan_de_cours_capture_en_pdf(tmp_path):
    # Le lien PDF du menu est une commande ADF qui PUBLIE : on imprime la page.
    # Seule la section "plan_de_cours" mene reellement au plan ici ; les autres
    # noms de section ne sont que des candidats non confirmes.
    session = SessionFactice({})
    session.page = PageAvecPlanDeCours(
        "plan_de_cours", "<html><body><h1>Plan de cours</h1></body></html>"
    )
    ena = Ena(session)
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") == "plan_de_cours"
    assert any("/lieninterne/redirection/100001/" in u for u in ena.session.page.visitees)


class PageRedirigeeVersAccueil(PageFactice):
    """Les candidats invalides redirigent silencieusement vers l'accueil (code
    200, pas de page_erreur) : un piege pour un critere fonde sur page_erreur."""

    URL_ACCUEIL = "https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=100001"
    URL_PLAN = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=100001&idModule=999"

    def __init__(self, section_valide, html_plan):
        super().__init__({})
        self.section_valide = section_valide
        self.html_plan = html_plan

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = self.URL_PLAN if f"/{self.section_valide}" in url else self.URL_ACCUEIL

    def content(self):
        if self.url == self.URL_PLAN:
            return self.html_plan
        return "<html><body>Accueil du site</body></html>"


def test_plan_de_cours_rejette_une_redirection_vers_l_accueil(tmp_path):
    # Un candidat de section invalide peut retomber, sans erreur HTTP, sur
    # l'accueil du site : ce n'est pas un succes, meme si l'URL ne contient
    # pas "page_erreur". Le candidat suivant doit etre tente.
    session = SessionFactice({})
    session.page = PageRedirigeeVersAccueil(
        section_valide="plancours",
        html_plan="<html><body><h1>Plan de cours</h1></body></html>",
    )
    ena = Ena(session)
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    resultat = ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf")

    assert resultat == "plancours"
    assert len(ena.session.page.visitees) == 2


class PageValideSansMarqueur(PageFactice):
    """URL finale reelle, differente de l'accueil, mais sans texte de plan de
    cours : un site dont aucune section n'est un plan de cours."""

    URL_REELLE = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=100001&idModule=1"

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = self.URL_REELLE

    def content(self):
        return "<html><body>Bienvenue sur le site du cours</body></html>"


def test_plan_de_cours_sans_marqueur_est_rejete(tmp_path):
    # Une page reelle, distincte de l'accueil, ne suffit pas si elle ne porte
    # pas le contenu d'un plan de cours.
    session = SessionFactice({})
    session.page = PageValideSansMarqueur({})
    ena = Ena(session)
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") is False
    # Les trois candidats ont ete tentes : aucun ne porte le marqueur.
    assert len(ena.session.page.visitees) == 3


def test_plan_de_cours_absent_renvoie_faux(tmp_path):
    class PageEnErreur(PageFactice):
        def goto(self, url, **_):
            super().goto(url, **_)
            self.url = "https://sitescours.monportail.ulaval.ca/portail/page_erreur"

    session = SessionFactice({})
    session.page = PageEnErreur({})
    ena = Ena(session)
    cours = Cours(
        id_site="1",
        sigle=None,
        titre="Sans plan",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") is False


def test_modules_utilise_l_url_canonique():
    html = (
        '<a href="/ena/site/module?idSite=100001&idModule=1795743&editionModule=false">'
        "1. Introduction</a>"
    )
    ena = Ena(SessionFactice({"/ena/site/modules": html}))
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    modules = ena.modules(cours)
    assert [m.id_module for m in modules] == ["1795743"]
    assert "/ena/site/modules?idSite=100001" in ena.session.page.visitees[0]


def test_modules_absents_ne_font_pas_echouer():
    # Le site de formation EDI n'a ni modules ni evaluations.
    ena = Ena(SessionFactice({}))
    cours = Cours(
        id_site="100006",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    assert ena.modules(cours) == []


def test_evaluations_extraites():
    html = """
    <a href="/ena/site/evaluation?idSite=100002&idEvaluation=1035434&onglet">Exposé oral I</a>
    <a href="/ena/site/evaluation?idSite=100002&idEvaluation=1035435&onglet">Rapport de suivi I</a>
    """
    ena = Ena(SessionFactice({"/ena/site/evaluations": html}))
    cours = Cours(
        id_site="100002",
        sigle="DEF-2000",
        titre="Projet",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    evaluations = ena.evaluations(cours)
    assert [e.id_evaluation for e in evaluations] == ["1035434", "1035435"]
    assert evaluations[0].titre == "Exposé oral I"


def test_fichiers_de_depot_visitent_l_onglet_boite_depots():
    ena = Ena(SessionFactice({}))
    ena.fichiers_de_depot(Evaluation(id_site="100002", id_evaluation="1035434", titre="T"))
    assert "onglet=boiteDepots" in ena.session.page.visitees[0]


# Nom URL-encode (espace et accent) : le nom du depot vient de l'URL, jamais
# du texte affiche du lien, qui peut etre tronque par la plateforme.
LIEN_DOCUMENT_DEPOSE = (
    "/contenu/sitescours/040/04000/202601/site100001/depots"
    "/Z1-ABC1000-H2026-TP2%20-%20%C3%89thique.docx?identifiant=abc"
)


def test_fichiers_de_depot_rend_des_depots_complets():
    # Cable sur depots_depuis_html, et non fichiers_depuis_html : seule la
    # premiere porte "Depose par" et la date de remise, la seule trace de qui
    # a remis quoi sur un travail d'equipe.
    html = f"""
    <table>
      <tr><th>Nom du document</th><th>Taille</th><th>Déposé par</th><th>Date de remise</th></tr>
      <tr>
        <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-ABC1000-H2026-TP2 - Éthique.docx</a></td>
        <td>3,25 Mo</td>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
      </tr>
    </table>
    """
    ena = Ena(SessionFactice({"onglet=boiteDepots": html}))

    depots = ena.fichiers_de_depot(
        Evaluation(id_site="100001", id_evaluation="1035434", titre="TP2")
    )

    assert len(depots) == 1
    assert depots[0].nom == "Z1-ABC1000-H2026-TP2 - Éthique.docx"
    assert depots[0].url == LIEN_DOCUMENT_DEPOSE
    assert depots[0].taille == "3,25 Mo"
    assert depots[0].depose_par == "Buteau, Laurent"
    assert depots[0].date_remise == "12 avr. 2026 18h43"


def test_fichiers_de_description_visitent_l_onglet_par_defaut():
    ena = Ena(SessionFactice({}))
    ena.fichiers_de_description(Evaluation(id_site="100002", id_evaluation="1035434", titre="T"))
    visitee = ena.session.page.visitees[0]
    assert "idEvaluation=1035434" in visitee
    assert "onglet" not in visitee


def test_fichiers_de_description_extrait_les_pieces_jointes():
    # Une description d'evaluation peut porter l'enonce d'un travail en piece
    # jointe : reutilise fichiers_depuis_html, deja fiable pour les modules.
    html = '<a href="/contenu/sitescours/x/enonce.pdf?identifiant=a">Énoncé</a>'
    ena = Ena(SessionFactice({"idEvaluation=1035434": html}))

    fichiers = ena.fichiers_de_description(
        Evaluation(id_site="100002", id_evaluation="1035434", titre="T")
    )

    assert [f.nom for f in fichiers] == ["enonce.pdf"]


def test_fichiers_de_resultats_evaluation_visitent_l_onglet_resultats():
    ena = Ena(SessionFactice({}))
    ena.fichiers_de_resultats_evaluation(
        Evaluation(id_site="100002", id_evaluation="1035434", titre="T")
    )
    assert "onglet=resultats" in ena.session.page.visitees[0]


def test_fichiers_de_resultats_evaluation_extrait_les_pieces_jointes():
    # L'onglet Resultats peut porter une retroaction en piece jointe.
    html = '<a href="/contenu/sitescours/x/retroaction.pdf?identifiant=a">Rétroaction</a>'
    ena = Ena(SessionFactice({"onglet=resultats": html}))

    fichiers = ena.fichiers_de_resultats_evaluation(
        Evaluation(id_site="100002", id_evaluation="1035434", titre="T")
    )

    assert [f.nom for f in fichiers] == ["retroaction.pdf"]


def test_fichiers_du_module_visitent_l_url_du_module():
    ena = Ena(SessionFactice({}))
    ena.fichiers_du_module(Module(id_site="100001", id_module="1795743", titre="M"))
    assert "idModule=1795743" in ena.session.page.visitees[0]
    assert "editionModule=false" in ena.session.page.visitees[0]


class PageSansOngletContenu(PageFactice):
    """L'onglet « Contenu du module » est absent : le clic leve un depassement
    de delai Playwright, comme un vrai clic sur un onglet qui n'existe pas."""

    def click(self, *_args, **_kwargs):
        raise ErreurDelaiPlaywright("Timeout 5000ms exceeded.")


def test_fichiers_du_module_tolere_un_onglet_contenu_introuvable():
    # Le module n'a pas d'onglet "Contenu du module" : ce n'est pas une
    # erreur, le module peut simplement n'avoir pas de documents en ligne.
    html = '<a href="/contenu/sitescours/100001/module1795743/doc.pdf">Document</a>'
    session = SessionFactice({})
    session.page = PageSansOngletContenu({"idModule=1795743": html})
    ena = Ena(session)

    fichiers = ena.fichiers_du_module(Module(id_site="100001", id_module="1795743", titre="M"))

    assert [f.nom for f in fichiers] == ["doc.pdf"]


def test_fichiers_du_module_visite_l_idpage_quand_fourni():
    # Sans idPage, le serveur ADF sert un onglet imprevisible : chaque onglet
    # doit etre visite par sa propre URL.
    ena = Ena(SessionFactice({}))
    ena.fichiers_du_module(
        Module(id_site="100001", id_module="1795743", titre="M"), "4874493"
    )
    assert "idPage=4874493" in ena.session.page.visitees[0]


def test_pages_du_module_module_sans_onglets_rend_une_seule_page_racine():
    # Un module monte sans barre d'onglets est legitime (voir
    # docs/api-monportail.md) : comportement d'avant l'ajout des onglets,
    # conserve tel quel -- une seule page, sans chemin.
    ena = Ena(SessionFactice({}))
    pages = ena.pages_du_module(Module(id_site="100001", id_module="1795743", titre="M"))
    assert [(p.id_page, p.chemin) for p in pages] == [(None, ())]


def test_pages_du_module_leve_session_expiree_si_page_non_authentifiee():
    # Sans ce garde-fou, une session expiree rendrait une page de connexion
    # vide, lue comme "un seul module sans onglets" : une archive
    # silencieusement incomplete.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifiee({})

    with pytest.raises(SessionExpiree):
        ena.pages_du_module(Module(id_site="100001", id_module="1795743", titre="M"))


class PageParcoursOngletsImbriques(PageFactice):
    """Reconstitution simplifiee de MNO-5000 (idSite=100004, idModule=1310231
    -- voir docs/api-monportail.md, etape 3) : deux niveaux d'onglets, le
    niveau 2 ("Avancé") portant deux feuilles ("Sous A", "Sous B").

    Sans idPage, sert "Général" (feuille de premier niveau, sans second
    niveau). idPage=2 (le parent "Avancé") redescend automatiquement sur sa
    premiere feuille "Sous A" (regle 3 de docs/api-monportail.md), exactement
    comme idPage=21 directement. idPage=22 selectionne "Sous B" directement
    (regle 4)."""

    HTML_GENERAL = (
        '<div class="ul_customizablePanelTabbed_tabs">'
        '<span _ulitemid="r1:0:page:t1" class="ul_customizablePanelTabbed_tab p_AFSelected">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Général">Général</a>'
        "</span>"
        '<span _ulitemid="r1:0:page:t2" class="ul_customizablePanelTabbed_tab">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Avancé">Avancé</a>'
        "</span>"
        "</div>"
    )
    HTML_AVANCE_SOUS_A = (
        '<div class="ul_customizablePanelTabbed_tabs">'
        '<span _ulitemid="r1:0:page:t1" class="ul_customizablePanelTabbed_tab">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Général">Général</a>'
        "</span>"
        '<span _ulitemid="r1:0:page:t2" class="ul_customizablePanelTabbed_tab p_AFSelected">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Avancé">Avancé</a>'
        "</span>"
        "</div>"
        '<div class="ul_customizablePanelTabbed_tabs">'
        '<span _ulitemid="r1:0:page:t2:z2:t21" class="ul_customizablePanelTabbed_tab p_AFSelected">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Sous A">Sous A</a>'
        "</span>"
        '<span _ulitemid="r1:0:page:t2:z2:t22" class="ul_customizablePanelTabbed_tab">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Sous B">Sous B</a>'
        "</span>"
        "</div>"
    )
    HTML_AVANCE_SOUS_B = (
        '<div class="ul_customizablePanelTabbed_tabs">'
        '<span _ulitemid="r1:0:page:t1" class="ul_customizablePanelTabbed_tab">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Général">Général</a>'
        "</span>"
        '<span _ulitemid="r1:0:page:t2" class="ul_customizablePanelTabbed_tab p_AFSelected">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Avancé">Avancé</a>'
        "</span>"
        "</div>"
        '<div class="ul_customizablePanelTabbed_tabs">'
        '<span _ulitemid="r1:0:page:t2:z2:t21" class="ul_customizablePanelTabbed_tab">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Sous A">Sous A</a>'
        "</span>"
        '<span _ulitemid="r1:0:page:t2:z2:t22" class="ul_customizablePanelTabbed_tab p_AFSelected">'
        '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Sous B">Sous B</a>'
        "</span>"
        "</div>"
    )

    def __init__(self):
        super().__init__({})
        self._html_par_id_page = {
            "1": self.HTML_GENERAL,
            "2": self.HTML_AVANCE_SOUS_A,
            "21": self.HTML_AVANCE_SOUS_A,
            "22": self.HTML_AVANCE_SOUS_B,
        }

    def content(self):
        from urllib.parse import parse_qs, urlsplit

        id_page = parse_qs(urlsplit(self.url).query).get("idPage", [None])[0]
        return self._html_par_id_page.get(id_page, self.HTML_GENERAL)


def test_pages_du_module_parcourt_les_onglets_imbriques_par_idpage():
    # Un clic code en dur sur un seul onglet perdrait "Sous A" et "Sous B" :
    # le parcours doit visiter chaque onglet decouvert, y compris ceux qui
    # ne sont reveles qu'apres etre descendu dans un onglet parent.
    session = SessionFactice({})
    session.page = PageParcoursOngletsImbriques()
    ena = Ena(session)

    pages = ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="Module 1"))

    releve = {(p.id_page, p.chemin) for p in pages}
    assert releve == {
        ("1", ("Général",)),
        ("21", ("Avancé", "Sous A")),
        ("22", ("Avancé", "Sous B")),
    }
    # Chaque feuille a bien ete visitee par sa propre URL, jamais par un clic.
    assert any("idPage=22" in u for u in session.page.visitees)


class PageCycleInfini(PageFactice):
    """Simule une structure d'onglets pathologique : chaque page visitee se
    pretend etre une nouvelle feuille distincte (compteur toujours
    croissant) et decouvre toujours un nouvel onglet a visiter -- l'ensemble
    des pages vues ne se stabilise donc jamais. Sert a verifier la borne dure
    de securite (LIMITE_PAGES_MODULE)."""

    def __init__(self):
        super().__init__({})
        self.compteur = 0

    def goto(self, url, **_):
        super().goto(url, **_)
        self.compteur += 1

    def content(self):
        n = self.compteur
        return (
            '<div class="ul_customizablePanelTabbed_tabs">'
            f'<span _ulitemid="r1:0:page:t{n}" class="ul_customizablePanelTabbed_tab p_AFSelected">'
            f'<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Page {n}">Page {n}</a>'
            "</span>"
            f'<span _ulitemid="r1:0:page:t{n + 1}" class="ul_customizablePanelTabbed_tab">'
            f'<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Page {n + 1}">Page {n + 1}</a>'
            "</span>"
            "</div>"
        )


def test_pages_du_module_leve_si_la_borne_de_securite_est_atteinte():
    # Le nombre d'identifiants reels d'un module est fini en usage normal :
    # une structure qui ne se stabilise jamais (bogue, ou DOM totalement
    # inattendu) ne doit pas bloquer tout l'archivage du cours en bouclant
    # indefiniment. On leve bruyamment plutot que de deviner.
    session = SessionFactice({})
    session.page = PageCycleInfini()
    ena = Ena(session)

    with pytest.raises(TropDePagesDansUnModule):
        ena.pages_du_module(Module(id_site="100001", id_module="1795743", titre="M"))

    # La borne s'applique au nombre de pages DISTINCTES retenues, pas au
    # nombre brut de navigations : elle s'arrete des qu'elle est atteinte,
    # jamais avant.
    assert session.page.compteur >= LIMITE_PAGES_MODULE


class LienFactice:
    def __init__(self, identifiant=None):
        self.identifiant = identifiant
        self.clique = False

    def get_attribute(self, _nom):
        return self.identifiant

    def click(self, **_kwargs):
        self.clique = True


class PageAvecMenu(PageFactice):
    """Rend un menu de site et enregistre les libelles cliques."""

    def __init__(self, html_menu, liens=None):
        super().__init__({"/ena/site/accueil": html_menu})
        self.liens = liens or {}
        self.cliques = []

    def get_by_role(self, _role, name=None, exact=False):
        lien = self.liens.get(name, LienFactice())
        self.cliques.append(name)

        class Localisateur:
            first = lien

        return Localisateur()


def test_parcourir_menu_ouvre_chaque_section_du_site():
    page = PageAvecMenu(
        '<a href="#">Concepts de base</a><a href="#">Six biais</a>'
        '<a href="#">Tableau de bord</a>'
    )
    session = SessionFactice({})
    session.page = page
    ena = Ena(session)

    vues = []
    nombre = ena.parcourir_menu(
        Cours(
            id_site="100006",
            sigle=None,
            titre="EDI",
            session=Session(code="202209", libelle="Automne 2022"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    # Le chrome du portail est ecarte par sections_du_menu.
    assert vues == ["Concepts de base", "Six biais"]
    assert nombre == 2


def test_parcourir_menu_n_ouvre_jamais_une_commande_adf():
    # cmdObtenirPlanCours publie une nouvelle version du plan de cours.
    page = PageAvecMenu(
        '<a href="#">Plan de cours</a>',
        liens={"Plan de cours": LienFactice("m:j_id_1:cmdObtenirPlanCours")},
    )
    session = SessionFactice({})
    session.page = page
    ena = Ena(session)

    vues = []
    nombre = ena.parcourir_menu(
        Cours(
            id_site="1",
            sigle=None,
            titre="X",
            session=Session(code="202601", libelle="Hiver 2026"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    assert vues == []
    assert nombre == 0
    assert page.liens["Plan de cours"].clique is False


class LienNonCliquable(LienFactice):
    """Un lien de menu present dans le DOM mais que Playwright ne peut pas
    cliquer (ADF pas encore rendu, element masque, etc.)."""

    def click(self, **_kwargs):
        raise ErreurDelaiPlaywright("Timeout 5000ms exceeded.")


def test_parcourir_menu_tolere_un_lien_non_cliquable():
    # Une section du menu peut echouer au clic sans que ce soit une erreur du
    # site : on passe a la suivante au lieu d'interrompre tout le parcours.
    page = PageAvecMenu(
        '<a href="#">Section fantome</a><a href="#">Six biais</a>',
        liens={"Section fantome": LienNonCliquable()},
    )
    session = SessionFactice({})
    session.page = page
    ena = Ena(session)

    vues = []
    nombre = ena.parcourir_menu(
        Cours(
            id_site="100006",
            sigle=None,
            titre="EDI",
            session=Session(code="202209", libelle="Automne 2022"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    assert vues == ["Six biais"]
    assert nombre == 1


def test_ena_ne_confie_plus_aucun_motif_ancre_a_has_text():
    # Piege deja constate deux fois sur l'ancien selecteur AngularJS de
    # /portail/cours (lecture des options, puis confirmation de la session
    # selectionnee, toutes deux disparues avec la refonte du portail) :
    # Playwright ne normalise pas les espaces d'un texte compare a un motif
    # ancre (^...$) confie a has_text. Audit statique conserve en garde-fou
    # de non-regression, plutot que de faire confiance a la relecture : aucun
    # motif ancre ne doit etre transmis a has_text= dans ena.py.
    source = (Path(__file__).resolve().parent.parent / "extracteur" / "ena.py").read_text(
        encoding="utf-8"
    )

    assert "has_text=" not in source
    assert not re.search(r"""f["']\^.*\$["']""", source), (
        "un motif ancre (f-string commencant par ^ et finissant par $) est "
        "construit dans ena.py -- verifier qu'il n'est pas confie a has_text"
    )


class PageOngletsLarges(PageFactice):
    """Un parent ("Racine") portant N feuilles, comme les modules reels de
    MNO-5000 qui en portent six par en-tete.

    Sert a mesurer le NOMBRE de navigations, pas leur resultat : c'est la
    grandeur qui distingue un parcours lineaire d'un parcours quadratique, et
    donc quelques secondes de quelques heures sur un cours entier.
    """

    NOMBRE_FEUILLES = 6

    def _barre_feuilles(self, selectionnee: str) -> str:
        spans = []
        for numero in range(1, self.NOMBRE_FEUILLES + 1):
            id_feuille = f"1{numero}"
            classe = "ul_customizablePanelTabbed_tab"
            if id_feuille == selectionnee:
                classe += " p_AFSelected"
            spans.append(
                f'<span _ulitemid="r1:0:page:t1:z1:t{id_feuille}" class="{classe}">'
                f'<a class="ul_customizablePanelTabbed_tab-link" href="#" '
                f'title="Feuille {numero}">Feuille {numero}</a></span>'
            )
        return '<div class="ul_customizablePanelTabbed_tabs">' + "".join(spans) + "</div>"

    def _html(self, id_feuille: str) -> str:
        parent = (
            '<div class="ul_customizablePanelTabbed_tabs">'
            '<span _ulitemid="r1:0:page:t1" class="ul_customizablePanelTabbed_tab p_AFSelected">'
            '<a class="ul_customizablePanelTabbed_tab-link" href="#" title="Racine">Racine</a>'
            "</span></div>"
        )
        return parent + self._barre_feuilles(id_feuille)

    def content(self):
        from urllib.parse import parse_qs, urlsplit

        id_page = parse_qs(urlsplit(self.url).query).get("idPage", [None])[0]
        # Le parent "1" et l'absence d'idPage redescendent sur la premiere
        # feuille, comme le serveur ADF reel (regle 3 de la doc).
        if id_page in (None, "1"):
            id_page = "11"
        return self._html(id_page)


def test_pages_du_module_ne_demande_jamais_deux_fois_la_meme_page():
    # Defaut constate en conditions reelles : `vus` retient l'identifiant
    # REELLEMENT servi, jamais celui demande. Un onglet parent, qui redescend
    # toujours sur une feuille, n'entre donc jamais dans `vus` -- et se fait
    # re-enfiler a chaque nouvelle page decouverte. Meme chose pour les
    # feuilles voisines pas encore visitees. Le parcours termine, mais le
    # nombre de navigations croit avec le carre du nombre d'onglets : sur un
    # cours reel, des heures la ou quelques minutes suffisent, et l'operateur
    # voit defiler indefiniment les memes noms d'en-tetes.
    session = SessionFactice({})
    session.page = PageOngletsLarges({})
    ena = Ena(session)

    ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="Module large"))

    demandes = session.page.visitees
    assert len(demandes) == len(set(demandes)), (
        f"{len(demandes)} navigations pour {len(set(demandes))} pages distinctes : "
        f"{[d.split('idPage=')[-1] for d in demandes]}"
    )


def test_pages_du_module_visite_au_plus_une_fois_chaque_onglet():
    # Borne haute explicite : une page par onglet declare (6 feuilles + 1
    # parent), plus la visite initiale sans idPage. Sans cette borne, rien ne
    # signalerait un retour a un parcours quadratique.
    session = SessionFactice({})
    session.page = PageOngletsLarges({})
    ena = Ena(session)

    pages = ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="Module large"))

    # Borne serree a dessein : la visite initiale sans idPage, la descente
    # dans le parent, et une visite par feuille restante. Un cran de plus et
    # le test cesserait de voir la re-demande de l'identifiant deja servi.
    assert len(pages) == PageOngletsLarges.NOMBRE_FEUILLES
    assert len(session.page.visitees) <= PageOngletsLarges.NOMBRE_FEUILLES + 1


def test_pages_du_module_parcours_large_trouve_toutes_les_feuilles():
    # Contre-epreuve : la deduplication ne doit pas faire perdre de feuille.
    session = SessionFactice({})
    session.page = PageOngletsLarges({})
    ena = Ena(session)

    pages = ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="Module large"))

    assert {p.id_page for p in pages} == {
        f"1{n}" for n in range(1, PageOngletsLarges.NOMBRE_FEUILLES + 1)
    }
    assert all(p.chemin[0] == "Racine" for p in pages)
class PageOngletsQuiNeChangentJamais(PageFactice):
    """DOM qui declare beaucoup d'onglets distincts, mais sert toujours la
    meme feuille -- ce que ferait un serveur qui ignore l'idPage demande.

    Aucune page nouvelle n'est donc jamais retenue : un garde-fou compte sur
    le nombre de pages RETENUES ne se declencherait jamais, et le parcours
    enchainerait les allers-retours reseau en silence. C'est pourquoi la
    borne compte les navigations.
    """

    NOMBRE_ONGLETS = 400

    def content(self):
        spans = []
        for numero in range(1, self.NOMBRE_ONGLETS + 1):
            classe = "ul_customizablePanelTabbed_tab"
            if numero == 1:
                classe += " p_AFSelected"
            spans.append(
                f'<span _ulitemid="r1:0:page:t{numero}" class="{classe}">'
                f'<a class="ul_customizablePanelTabbed_tab-link" href="#" '
                f'title="Onglet {numero}">Onglet {numero}</a></span>'
            )
        return '<div class="ul_customizablePanelTabbed_tabs">' + "".join(spans) + "</div>"


def test_pages_du_module_borne_les_navigations_meme_sans_page_nouvelle():
    # Le cas que la borne doit reellement attraper : le serveur resert
    # toujours le meme onglet quel que soit l'idPage demande. Une seule page
    # est retenue, mais des centaines de navigations sont enfilees. Comptee
    # sur les pages retenues, la borne resterait muette et l'archivage
    # paraitrait bloque.
    session = SessionFactice({})
    session.page = PageOngletsQuiNeChangentJamais({})
    ena = Ena(session)

    with pytest.raises(TropDePagesDansUnModule):
        ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="M"))

    assert len(session.page.visitees) <= LIMITE_PAGES_MODULE + 1
class PageDeuxSectionsAvecSousOnglets(PageFactice):
    """Deux en-tetes ("Section A", "Section B"), chacun portant trois
    sous-onglets -- la forme reelle des modules de MNO-5000.

    Reproduit les deux regles du serveur ADF (voir docs/api-monportail.md,
    etape 3) : demander un en-tete redescend sur son premier sous-onglet, et
    demander un sous-onglet le sert directement.
    """

    SOUS = {"1": ["11", "12", "13"], "2": ["21", "22", "23"]}
    TITRES = {
        "1": "Section A", "2": "Section B",
        "11": "A-un", "12": "A-deux", "13": "A-trois",
        "21": "B-un", "22": "B-deux", "23": "B-trois",
    }

    def _parent_de(self, id_page: str) -> str:
        return id_page[0]

    def content(self):
        from urllib.parse import parse_qs, urlsplit

        id_page = parse_qs(urlsplit(self.url).query).get("idPage", [None])[0]
        if id_page in (None, "1"):
            id_page = "11"
        elif id_page == "2":
            id_page = "21"

        parent = self._parent_de(id_page)
        entetes = "".join(
            f'<span _ulitemid="r1:0:page:t{p}" class="ul_customizablePanelTabbed_tab'
            f'{" p_AFSelected" if p == parent else ""}">'
            f'<a class="ul_customizablePanelTabbed_tab-link" href="#" '
            f'title="{self.TITRES[p]}">{self.TITRES[p]}</a></span>'
            for p in ("1", "2")
        )
        feuilles = "".join(
            f'<span _ulitemid="r1:0:page:t{parent}:z{parent}:t{f}" '
            f'class="ul_customizablePanelTabbed_tab'
            f'{" p_AFSelected" if f == id_page else ""}">'
            f'<a class="ul_customizablePanelTabbed_tab-link" href="#" '
            f'title="{self.TITRES[f]}">{self.TITRES[f]}</a></span>'
            for f in self.SOUS[parent]
        )
        return (
            f'<div class="ul_customizablePanelTabbed_tabs">{entetes}</div>'
            f'<div class="ul_customizablePanelTabbed_tabs">{feuilles}</div>'
        )


def test_pages_du_module_termine_une_section_avant_de_passer_a_la_suivante():
    # L'ordre demande explicitement : faire une section, chacun de ses
    # onglets, puis passer a la suivante. Un parcours en largeur couvrirait
    # tout aussi, mais en alternant entre les en-tetes -- ce qui, sur un cours
    # reel, donne a l'operateur l'impression que l'archivage tourne en rond.
    session = SessionFactice({})
    session.page = PageDeuxSectionsAvecSousOnglets({})
    ena = Ena(session)

    pages = ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="M"))

    sections = [p.chemin[0] for p in pages]
    # Chaque section apparait d'un seul tenant : autant de blocs que de
    # sections distinctes, jamais de retour en arriere.
    blocs = [nom for indice, nom in enumerate(sections) if indice == 0 or nom != sections[indice - 1]]
    assert blocs == ["Section A", "Section B"], sections
    assert len(pages) == 6


def test_pages_du_module_deux_sections_trouve_toutes_les_feuilles():
    # Contre-epreuve de l'ordre : ranger n'autorise pas a perdre une feuille.
    session = SessionFactice({})
    session.page = PageDeuxSectionsAvecSousOnglets({})
    ena = Ena(session)

    pages = ena.pages_du_module(Module(id_site="100001", id_module="1310231", titre="M"))

    assert {p.chemin for p in pages} == {
        ("Section A", "A-un"), ("Section A", "A-deux"), ("Section A", "A-trois"),
        ("Section B", "B-un"), ("Section B", "B-deux"), ("Section B", "B-trois"),
    }


def test_capturer_pdf_page_courante_ne_navigue_pas(tmp_path):
    # Chaque navigation est un aller-retour ADF de plusieurs secondes. Quand
    # l'appelant vient de lire une page, le navigateur y est deja : recharger
    # la meme URL pour l'imprimer doublait le cout de chaque module.
    session = SessionFactice({})
    ena = Ena(session)
    session.page.goto(
        "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=100001&idModule=1"
    )
    avant = len(session.page.visitees)

    ena.capturer_pdf_page_courante(tmp_path / "sortie.pdf")

    assert len(session.page.visitees) == avant


def test_capturer_pdf_page_courante_refuse_une_page_non_authentifiee(tmp_path):
    # Sans ce garde-fou, une session expiree produirait un PDF de la page de
    # connexion : un fichier d'apparence valide, au contenu faux, et une
    # archive silencieusement trouee -- exactement ce que ce projet evite.
    session = SessionFactice({})
    session.page = PageNonAuthentifiee({})
    ena = Ena(session)

    with pytest.raises(SessionExpiree):
        ena.capturer_pdf_page_courante(tmp_path / "sortie.pdf")


def test_ressources_ignorees_page_courante_ne_navigue_pas():
    # Meme motif que capturer_pdf_page_courante : le navigateur est deja sur
    # la bonne page (on vient d'y lire les fichiers), la recharger couterait
    # un aller-retour ADF de plusieurs secondes pour rien.
    lien_video = (
        "/analytique/evenement/multimedia.mp4?idFichier=1&idSite=100001"
        "&url=%2Fcontenu%2Fsitescours%2Fx%2Fcapsule.mp4"
    )
    session = SessionFactice({})
    ena = Ena(session)
    session.page.goto(
        "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=100001&idModule=1"
    )
    session.page._html = f'<a href="{lien_video}">Capsule</a>'
    session.page.content = lambda: session.page._html
    avant = len(session.page.visitees)

    ressources = ena.ressources_ignorees_page_courante()

    assert len(session.page.visitees) == avant
    assert len(ressources) == 1
    assert ressources[0].genre == "video"


def test_ressources_ignorees_page_courante_refuse_une_page_non_authentifiee():
    # Meme garde-fou que capturer_pdf_page_courante : sans lui, une session
    # expiree ferait lire silencieusement la page de connexion, rendant une
    # liste vide de ressources ignorees plutot que de signaler l'interruption.
    session = SessionFactice({})
    session.page = PageNonAuthentifiee({})
    ena = Ena(session)

    with pytest.raises(SessionExpiree):
        ena.ressources_ignorees_page_courante()


# --- _visiter se repare une seule fois apres un echec de navigation ---
#
# Incident reel : un goto qui expire (Timeout) laisse la page dans un etat de
# navigation qui ne se resorbe jamais toute seule. Le goto suivant entre en
# conflit, puis meme la lecture du contenu finit par echouer definitivement.
# Sans reparation, cette page cassee sert a TOUTES les navigations
# suivantes : 17 cours sur 39 perdus ainsi lors d'un archivage reel, alors
# que l'isolation par cours attrapait pourtant bien chaque exception -- la
# ressource partagee, elle, n'etait jamais reparee.


class PageCassee(PageFactice):
    """Simule une page dont goto() echoue systematiquement, comme un vrai
    Timeout Playwright qui laisse la page dans un etat de navigation qui ne
    se resorbe jamais toute seule."""

    def goto(self, url, **_):
        raise ErreurDelaiPlaywright("Timeout 30000ms exceeded.")


class SessionAvecReinitialisation(SessionFactice):
    """Simule SessionNavigateur.reinitialiser_page() : remplace la page
    cassee par une page saine, comme le ferait une vraie reinitialisation
    (fermeture de la page, ouverture d'une neuve dans le meme contexte)."""

    def __init__(self, page_cassee, page_saine):
        self.page = page_cassee
        self._page_saine = page_saine
        self.appels_reinitialisation = 0

    def reinitialiser_page(self, imprimer=print):
        self.appels_reinitialisation += 1
        self.imprimer_recu = imprimer
        self.page = self._page_saine


def test_visiter_se_repare_une_fois_apres_un_echec_de_navigation():
    # Sans le correctif, le premier goto() leve et _visiter laisse
    # l'exception remonter directement : la page cassee n'est jamais
    # remplacee, et resterait cassee pour tous les cours suivants.
    page_cassee = PageCassee({})
    page_saine = PageFactice({"/ena/site/modules": "<html>ok</html>"})
    session = SessionAvecReinitialisation(page_cassee, page_saine)
    ena = Ena(session)

    html = ena._visiter("/ena/site/modules?idSite=1")

    assert html == "<html>ok</html>"
    assert session.appels_reinitialisation == 1
    assert session.page is page_saine


class SessionAvecReinitialisationInsuffisante(SessionFactice):
    """Meme mecanisme que SessionAvecReinitialisation, mais la page neuve est
    tout aussi cassee : verifie qu'aucune boucle ne se met en place, et que
    l'exception finit par remonter telle quelle."""

    def __init__(self, page_cassee):
        self.page = page_cassee
        self.appels_reinitialisation = 0

    def reinitialiser_page(self, imprimer=print):
        # imprimer accepte et ignore : l'interface reelle le porte
        # pour que la trace atteigne la fenetre graphique.
        self.appels_reinitialisation += 1
        self.page = PageCassee({})


def test_visiter_ne_boucle_pas_si_la_reprise_echoue_aussi():
    session = SessionAvecReinitialisationInsuffisante(PageCassee({}))
    ena = Ena(session)

    with pytest.raises(ErreurPlaywright):
        ena._visiter("/ena/site/modules?idSite=1")

    # Un seul rejeu, jamais une boucle : la reparation n'a ete tentee qu'une
    # fois avant que l'exception ne remonte.
    assert session.appels_reinitialisation == 1


def test_visiter_fait_passer_son_canal_d_affichage_a_la_reparation():
    # La trace « page reinitialisee » doit atterrir la ou l'utilisateur
    # regarde. En console c'est la sortie standard, mais dans la fenetre
    # graphique c'est son journal -- et le coeur y pose son propre canal sur
    # l'objet Ena (voir extracteur.__main__). Si _visiter appelle
    # reinitialiser_page sans le transmettre, la reparation redevient
    # invisible pour qui archive depuis la fenetre : une execution qui se
    # repare en silence empeche de voir que la plateforme faiblit.
    journal: list = []
    session = SessionAvecReinitialisation(
        PageCassee({}), PageFactice({"/ena/site/modules": "<html>ok</html>"})
    )
    ena = Ena(session, imprimer=journal.append)

    ena._visiter("/ena/site/modules?idSite=1")

    assert session.appels_reinitialisation == 1
    assert session.imprimer_recu == journal.append


# --- Filtre de session du tableau de bord (/portail/), depuis la refonte du
# portail constatee le 25 septembre 2026 -- remplace l'ancien selecteur
# AngularJS de /portail/cours (page desormais en 404). ---


def _carte_cours(id_site: str, titre: str) -> str:
    """Une carte de cours minimale, dans la forme reelle relevee sur le
    tableau de bord (voir docs/api-monportail.md)."""
    return (
        '<li class="mpo-gabarit-item-liste-cours">'
        '<h4 class="mpo-gabarit-item-liste-cours__titre">'
        f'<a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite={id_site}">'
        f'<span class="m-link__text">{titre}</span></a></h4>'
        "</li>"
    )


def _accordeon_cours_suivis(cartes: str) -> str:
    return f'<div class="mpo-accordeon-cours-suivis"><ul>{cartes}</ul></div>'


# Piege reel constate en inspection directe : pendant le chargement, le
# squelette occupe la place des vrais cours, mais les « Autres activites »
# (formations institutionnelles, hors perimetre) sont deja affichees, avec
# elles aussi un lien /ena/site/accueil?idSite=. Contenu volontairement SANS
# accordeon-cours-suivis : une lecture qui se fierait a la seule presence
# d'un lien idSite= y trouverait a tort un « cours ».
HTML_AUTRES_ACTIVITES_SEULEMENT = (
    '<div class="mpo-smart-boite-liste-cours__sites-hors-session"><ul>'
    + _carte_cours("999999", "Formation EDI")
    + "</ul></div>"
)


class LocatorChampSessionFactice:
    """Simule input.m-dropdown__input : click() ouvre le filtre (ou echoue,
    si le champ est configure indisponible) ; input_value() rend la valeur
    actuellement affichee -- jamais lisible dans page.content(), voir
    docs/api-monportail.md."""

    def __init__(self, page):
        self.page = page

    def click(self, timeout=None):
        if self.page.champ_indisponible:
            raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")
        self.page.filtre_ouvert = True

    def input_value(self):
        return self.page.valeur_champ


class LocatorOptionUniqueFactice:
    """Rendu par .nth(i) sur le Locator des options : cliquer declenche le
    chargement de la session correspondante (voir
    PageAvecFiltreSession._declencher_chargement)."""

    def __init__(self, page, indice):
        self.page = page
        self.indice = indice

    def click(self, **_kwargs):
        libelle = self.page.libelles[self.indice]
        self.page._emettre_reponse_liste_cours(libelle)
        self.page._declencher_chargement(libelle)


class LocatorOptionsFiltreFactice:
    """Simule li[role=option].m-dropdown-item, une fois le filtre ouvert."""

    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    def wait_for(self, timeout=None):
        if not self.page.filtre_ouvert or self.page._nombre_options_presentes == 0:
            raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")

    def nth(self, indice):
        return LocatorOptionUniqueFactice(self.page, indice)


class LocatorTextesOptionsFactice:
    """Simule span.m-dropdown-item__element-text, a l'interieur de chaque
    option : peut rendre [] meme si des options existent (li presents), pour
    simuler un marquage change qui casse seulement l'extraction du texte."""

    def __init__(self, page):
        self.page = page

    def all_text_contents(self):
        return list(self.page.libelles) if self.page.filtre_ouvert else []


class LocatorSqueletteFactice:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1 if self.page.squelette_present else 0


class RequeteFactice:
    def __init__(self, entetes):
        self._entetes = entetes

    def all_headers(self):
        return dict(self._entetes)


class ReponseFactice:
    def __init__(self, url, donnees, ok=True, entetes_requete=None):
        self.url = url
        self._donnees = donnees
        self.ok = ok
        self.status = 200 if ok else 500
        self.request = RequeteFactice(entetes_requete or {"authorization": "Bearer jeton-de-test"})

    def json(self):
        return self._donnees


class ClientApiFactice:
    """Simule page.request (APIRequestContext) : rend la liste DETAILLEE
    programmee pour la session demandee, et retient chaque appel."""

    def __init__(self, page):
        self.page = page
        self.appels = []

    def get(self, url, headers=None, timeout=None):
        self.appels.append({"url": url, "headers": dict(headers or {})})
        if self.page.liste_detaillee_en_echec:
            return ReponseFactice(url, None, ok=False)
        code = parse_qs(urlsplit(url).query)["codesession"][0]
        sites = [
            {
                "idSite": id_site,
                "typeSite": "COURS",
                "sectionsSiteUtilisateur": [{"urlPlanDeCoursPdf": lien}],
            }
            for id_site, lien in self.page.plans_de_cours.items()
        ]
        return ReponseFactice(url, {"codeSession": code, "sitesSuivis": sites})


class AttenteReponseFactice:
    """Simule le gestionnaire de contexte rendu par page.expect_response :
    a la sortie du bloc, leve un delai depasse si aucune reponse retenue par
    le predicat n'a ete emise, comme Playwright."""

    def __init__(self, page, predicat):
        self.page = page
        self.predicat = predicat
        self.value = None

    def __enter__(self):
        self.page._attente_reponse = self
        return self

    def __exit__(self, type_exception, *_):
        self.page._attente_reponse = None
        if type_exception is None and self.value is None:
            raise ErreurDelaiPlaywright("Timeout exceeded while waiting for event \"response\"")
        return False


def _ids_dans_html(html: str) -> list:
    return re.findall(r"accueil\?idSite=(\d+)", html)


class PageAvecFiltreSession(PageFactice):
    """Simule le tableau de bord (/portail/) : filtre de session (champ
    readonly + options) et squelette de chargement du bloc des cours.

    `sondages_avant_disparition` regle combien d'appels a wait_for_timeout
    sont necessaires, apres un clic sur une option, avant que le squelette ne
    disparaisse (0 = disparait des le premier sondage, avant meme d'attendre
    -- cas nominal rapide). None simule un chargement qui ne se termine
    jamais (squelette present en permanence).

    `html_pendant_chargement`, quand fourni, est ce que rend content() tant
    que le squelette est present -- sert a reproduire le piege des
    « Autres activites » deja affichees pendant le chargement des cours.
    """

    def __init__(
        self,
        libelles,
        html_par_session=None,
        valeur_initiale="",
        sondages_avant_disparition=0,
        champ_indisponible=False,
        nombre_options_presentes=None,
        html_pendant_chargement="<html></html>",
    ):
        super().__init__({})
        self.libelles = libelles
        self.html_par_session = html_par_session or {}
        self.valeur_champ = valeur_initiale
        self.filtre_ouvert = False
        self.squelette_present = False
        self._sondages_avant_disparition = sondages_avant_disparition
        self._sondages_restants = None
        self.champ_indisponible = champ_indisponible
        self._nombre_options_presentes = (
            len(libelles) if nombre_options_presentes is None else nombre_options_presentes
        )
        self._html_pendant_chargement = html_pendant_chargement
        self.nombre_sondages = 0
        self._attente_reponse = None
        # Liste DETAILLEE (liens des plans de cours), demandee par l'outil
        # lui-meme : idSite -> lien du PDF officiel.
        self.plans_de_cours = {}
        self.liste_detaillee_en_echec = False
        self.request = ClientApiFactice(self)
        # Reponse du serveur a emettre pour chaque libelle, si differente de
        # celle deduite de html_par_session (cas d'une reponse d'une autre
        # session, ou d'aucune reponse du tout : valeur None).
        self.reponses_forcees = {}

    def expect_response(self, predicat, timeout=None):
        return AttenteReponseFactice(self, predicat)

    def _emettre_reponse_liste_cours(self, libelle: str) -> None:
        """Forme reelle relevee en inspection : codesession dans l'URL,
        codeSession et sitesSuivis dans le corps, les formations
        institutionnelles (typeSite FORMATION) melees aux cours."""
        code = session_depuis_libelle(libelle).code
        if libelle in self.reponses_forcees:
            forcee = self.reponses_forcees[libelle]
            if forcee is None:
                return
            code, donnees = forcee
        else:
            ids = _ids_dans_html(self.html_par_session.get(libelle, ""))
            donnees = {
                "codeSession": code,
                "sitesSuivis": [{"idSite": i, "typeSite": "COURS"} for i in ids]
                + [{"idSite": "999999", "typeSite": "FORMATION"}],
            }
        url = (
            "https://sitescours.monportail.ulaval.ca/services/listecours/cours/"
            f"?codesession={code}&degreinformation=SIMPLE&idutilisateurena=1"
        )
        reponse = ReponseFactice(url, donnees)
        if self._attente_reponse is not None and self._attente_reponse.predicat(reponse):
            self._attente_reponse.value = reponse

    def _declencher_chargement(self, libelle: str) -> None:
        self.valeur_champ = libelle
        if self._sondages_avant_disparition is None:
            self.squelette_present = True
            self._sondages_restants = None
        else:
            self._sondages_restants = self._sondages_avant_disparition
            self.squelette_present = self._sondages_restants > 0

    def wait_for_timeout(self, _ms):
        self.nombre_sondages += 1
        if self._sondages_restants is None:
            return
        if self._sondages_restants > 0:
            self._sondages_restants -= 1
        if self._sondages_restants == 0:
            self.squelette_present = False

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == SELECTEUR_CHAMP_SESSION:
            return LocatorChampSessionFactice(self)
        if selecteur == f"{SELECTEUR_OPTION_SESSION} .{CLASSE_TEXTE_OPTION_SESSION}":
            return LocatorTextesOptionsFactice(self)
        if selecteur == SELECTEUR_OPTION_SESSION:
            return LocatorOptionsFiltreFactice(self)
        if selecteur == SELECTEUR_SQUELETTES_CHARGEMENT:
            return LocatorSqueletteFactice(self)
        return LocatorFactice(0)

    def content(self):
        if self.squelette_present:
            return self._html_pendant_chargement
        return self.html_par_session.get(self.valeur_champ, "<html></html>")


def test_sessions_disponibles_ouvre_le_filtre_et_liste_les_sessions():
    page = PageAvecFiltreSession(["Hiver 2026", "Automne 2025"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert URL.cours() in page.visitees
    assert page.filtre_ouvert is True
    assert [s.libelle for s in sessions] == ["Hiver 2026", "Automne 2025"]
    assert [s.code for s in sessions] == ["202601", "202509"]


def test_sessions_disponibles_leve_si_le_champ_n_est_jamais_ouvrable():
    # Panne totale du filtre : ni l'un ni l'autre n'est tolere en silence,
    # une session ainsi ratee disparaitrait de l'enumeration sans le moindre
    # signe.
    page = PageAvecFiltreSession(["Hiver 2026"], champ_indisponible=True)
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sessions_disponibles()


def test_sessions_disponibles_leve_si_aucune_option_n_apparait():
    page = PageAvecFiltreSession([])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sessions_disponibles()


def test_sessions_disponibles_leve_si_aucune_option_n_est_lisible():
    # Le filtre s'ouvre bel et bien (des <li> existent), mais aucun n'expose
    # le texte attendu (span.m-dropdown-item__element-text) : marquage
    # change. Le pire mode de defaillance du projet est une liste vide rendue
    # en silence -- on leve donc bruyamment plutot que de deviner.
    page = PageAvecFiltreSession([], nombre_options_presentes=3)
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIllisible):
        ena.sessions_disponibles()


def test_sites_de_session_selectionne_la_session_puis_extrait_les_cours():
    html = _accordeon_cours_suivis(_carte_cours("100001", "Éthique"))
    page = PageAvecFiltreSession(["Hiver 2026"], {"Hiver 2026": html})
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202601", libelle="Hiver 2026")

    cours = ena.sites_de_session(session)

    assert page.valeur_champ == "Hiver 2026"
    assert [c.id_site for c in cours] == ["100001"]
    assert cours[0].session == session


def test_sites_de_session_sans_cours_rend_une_liste_vide():
    # La session courante de l'utilisateur, pas encore remplie, est un
    # exemple reel de session vide : une fois le changement confirme, ce
    # n'est pas une erreur.
    html = '<div class="mpo-sites-lies-session mpo-smart-boite-liste-cours__cours-lies-session"></div>'
    page = PageAvecFiltreSession(["Hiver 2026"], {"Hiver 2026": html})
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202601", libelle="Hiver 2026"))

    assert cours == []


def test_sites_de_session_leve_si_la_session_est_introuvable():
    # Une session absente du filtre est une anomalie : archiver les cours
    # d'une autre session sous le nom demande serait une corruption de
    # donnees pire qu'un arret franc.
    page = PageAvecFiltreSession(["Hiver 2026"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session_inexistante = Session(code="202509", libelle="Automne 2025")

    with pytest.raises(SelecteurSessionsIllisible):
        ena.sites_de_session(session_inexistante)


def test_sites_de_session_leve_si_le_chargement_ne_se_termine_jamais():
    # Le squelette de chargement ne disparait jamais : une session reellement
    # vide et un echec de chargement sont sinon indiscernables. On leve donc
    # bruyamment plutot que de lire une liste de cours tronquee ou fausse.
    html = _accordeon_cours_suivis(_carte_cours("1", "A"))
    page = PageAvecFiltreSession(
        ["Hiver 2023"], {"Hiver 2023": html}, sondages_avant_disparition=None
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sites_de_session(Session(code="202301", libelle="Hiver 2023"))


def test_sites_de_session_attend_la_disparition_du_squelette_avant_de_lire_les_cours():
    # Chronologie mesuree en inspection reelle (sondage aux 100 ms) : le
    # squelette reste present plusieurs sondages apres que le champ affiche
    # deja la nouvelle session. Une lecture qui ne sonderait qu'une fois
    # tomberait sur un contenu pas encore pret.
    html = _accordeon_cours_suivis(
        _carte_cours("1", "A") + _carte_cours("2", "B") + _carte_cours("3", "C") + _carte_cours("4", "D")
    )
    page = PageAvecFiltreSession(
        ["Automne 2025"], {"Automne 2025": html}, sondages_avant_disparition=6
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202509", libelle="Automne 2025"))

    assert sorted(c.id_site for c in cours) == ["1", "2", "3", "4"]
    assert page.nombre_sondages >= 6


def test_sites_de_session_ignore_les_autres_activites_deja_affichees_pendant_le_chargement():
    # Piege reel constate en inspection directe : pendant le chargement, le
    # squelette « Chargement de la liste de cours » occupe la place des
    # cours, mais les « Autres activites » sont deja affichees -- avec elles
    # aussi un lien /ena/site/accueil?idSite=. Le signal de fin de chargement
    # (squelette absent, jamais la seule presence d'une carte) empeche de
    # confondre ce contenu transitoire avec les vrais cours de la session.
    html_final = _accordeon_cours_suivis(_carte_cours("100002", "Analyse de donnees"))
    page = PageAvecFiltreSession(
        ["Automne 2025"],
        {"Automne 2025": html_final},
        sondages_avant_disparition=3,
        html_pendant_chargement=HTML_AUTRES_ACTIVITES_SEULEMENT,
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202509", libelle="Automne 2025"))

    # Le seul cours rendu est celui de la session, jamais l'activite hors
    # perimetre entrevue pendant le chargement.
    assert [c.id_site for c in cours] == ["100002"]
    assert page.nombre_sondages >= 3


class PageAvecRenduDiffere(PageAvecFiltreSession):
    """Reproduit la fenetre mesuree de 0 a ~100 ms apres le clic sur une
    option : le clic est pris en compte, mais le rendu n'a pas encore eu
    lieu. Le champ affiche encore l'ANCIENNE session, l'ancienne liste est
    encore a l'ecran, et aucun squelette n'est encore apparu."""

    def __init__(self, *args, sondages_avant_rendu=2, **kwargs):
        super().__init__(*args, **kwargs)
        self._sondages_avant_rendu = sondages_avant_rendu
        self._avant_rendu = None
        self._libelle_en_attente = None

    def _declencher_chargement(self, libelle):
        self._libelle_en_attente = libelle
        self._avant_rendu = self._sondages_avant_rendu

    def wait_for_timeout(self, ms):
        if self._avant_rendu is not None:
            self.nombre_sondages += 1
            self._avant_rendu -= 1
            if self._avant_rendu <= 0:
                self._avant_rendu = None
                PageAvecFiltreSession._declencher_chargement(self, self._libelle_en_attente)
            return
        super().wait_for_timeout(ms)


def test_sites_de_session_ne_lit_jamais_l_ancienne_session_avant_le_rendu():
    # Juste apres le clic, avant le rendu, il n'y a pas encore de squelette :
    # l'absence de squelette seule ferait lire la page tout de suite -- et
    # archiver les cours de la session PRECEDENTE sous le nom de la nouvelle.
    # Une corruption silencieuse, pire qu'une liste vide. Seule la valeur du
    # champ, egale a la session demandee, prouve que le rendu a eu lieu.
    page = PageAvecRenduDiffere(
        ["Hiver 2026", "Automne 2025"],
        {
            "Hiver 2026": _accordeon_cours_suivis(_carte_cours("91", "Cours de Hiver")),
            "Automne 2025": _accordeon_cours_suivis(_carte_cours("7", "Cours d'Automne")),
        },
        valeur_initiale="Hiver 2026",
        sondages_avant_disparition=3,
        sondages_avant_rendu=2,
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202509", libelle="Automne 2025"))

    assert [c.id_site for c in cours] == ["7"]
    assert all(c.session.libelle == "Automne 2025" for c in cours)


class PageAvecAncienneListeAffichee(PageAvecFiltreSession):
    """Chronologie reelle mesuree le 29 septembre 2026, apres un clic sur
    « Automne 2022 » alors que « Ete 2023 » etait affichee :
        43 ms   champ = Automne 2022, AUCUN squelette, ANCIENNES cartes
        154 ms  champ = Automne 2022, squelette present, 0 carte
        1755 ms champ = Automne 2022, squelette absent, cartes finales
    Pendant `sondages_ancienne_liste` sondages, la page affiche donc deja la
    nouvelle session dans le champ, sans squelette, avec la liste de la
    session precedente."""

    def __init__(self, *args, sondages_ancienne_liste=2, **kwargs):
        super().__init__(*args, **kwargs)
        self._sondages_ancienne_liste = sondages_ancienne_liste
        self._html_ancien = None
        self._restants_ancienne_liste = 0

    def _declencher_chargement(self, libelle):
        self._html_ancien = self.html_par_session.get(self.valeur_champ, "<html></html>")
        self._restants_ancienne_liste = self._sondages_ancienne_liste
        self.valeur_champ = libelle
        self.squelette_present = False

    def wait_for_timeout(self, ms):
        if self._restants_ancienne_liste > 0:
            self.nombre_sondages += 1
            self._restants_ancienne_liste -= 1
            if self._restants_ancienne_liste == 0:
                PageAvecFiltreSession._declencher_chargement(self, self.valeur_champ)
            return
        super().wait_for_timeout(ms)

    def content(self):
        if self._restants_ancienne_liste > 0:
            return self._html_ancien
        return super().content()


def test_sites_de_session_ne_lit_jamais_la_liste_de_la_session_precedente():
    # Incident reel du 29 septembre 2026 : les cours d'Automne 2022 ont ete
    # archives sous « 2023-1 Hiver ». Juste apres le clic, le champ affiche
    # DEJA la nouvelle session, sans squelette, mais la liste a l'ecran est
    # encore celle de la session precedente. Le couple « valeur du champ +
    # absence de squelette » concluait a tort des cet instant.
    page = PageAvecAncienneListeAffichee(
        ["Hiver 2023", "Automne 2022"],
        {
            "Automne 2022": _accordeon_cours_suivis(_carte_cours("11", "Dessin") + _carte_cours("12", "Maths")),
            "Hiver 2023": _accordeon_cours_suivis(_carte_cours("21", "Python")),
        },
        valeur_initiale="Automne 2022",
        sondages_avant_disparition=3,
        sondages_ancienne_liste=2,
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202301", libelle="Hiver 2023"))

    assert [c.id_site for c in cours] == ["21"]


def test_sites_de_session_leve_si_le_serveur_ne_repond_jamais_pour_la_session():
    # Sans la reponse du serveur pour la session demandee, rien ne prouve que
    # la liste affichee lui appartient : on leve plutot que de deviner.
    html = _accordeon_cours_suivis(_carte_cours("1", "A"))
    page = PageAvecFiltreSession(["Hiver 2023"], {"Hiver 2023": html})
    page.reponses_forcees["Hiver 2023"] = None
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sites_de_session(Session(code="202301", libelle="Hiver 2023"))


def test_sites_de_session_leve_si_la_liste_affichee_ne_correspond_jamais_au_serveur():
    # Le serveur annonce deux cours, la page n'en affiche qu'un seul, a
    # demeure : un cours manquerait en silence a l'archive.
    html = _accordeon_cours_suivis(_carte_cours("1", "A"))
    page = PageAvecFiltreSession(["Hiver 2023"], {"Hiver 2023": html})
    page.reponses_forcees["Hiver 2023"] = (
        "202301",
        {"codeSession": "202301", "sitesSuivis": [{"idSite": "1", "typeSite": "COURS"}, {"idSite": "2", "typeSite": "COURS"}]},
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sites_de_session(Session(code="202301", libelle="Hiver 2023"))


def test_ids_cours_suivis_ne_retient_que_les_cours_de_la_session():
    donnees = {
        "codeSession": "202209",
        "sitesSuivis": [
            {"idSite": "11", "typeSite": "COURS"},
            {"idSite": "12", "typeSite": "COURS"},
            {"idSite": "99", "typeSite": "FORMATION"},
        ],
    }

    assert ids_cours_suivis_depuis_reponse(donnees, "202209") == {"11", "12"}


def test_ids_cours_suivis_refuse_une_reponse_d_une_autre_session():
    donnees = {"codeSession": "202305", "sitesSuivis": [{"idSite": "11", "typeSite": "COURS"}]}

    with pytest.raises(ValueError):
        ids_cours_suivis_depuis_reponse(donnees, "202209")


# --- Chien de garde : un appel au navigateur qui ne rend jamais la main ---


class PageQuiGele(PageFactice):
    """Reproduit l'incident reel du 29 septembre 2026 : un appel au
    navigateur (ici l'impression PDF) ne rend jamais la main, navigateur et
    pilote Playwright a 0 % de processeur. Seule la fermeture de l'onglet,
    demandee depuis un autre thread, le debloque -- en levant une erreur,
    comme le fait Playwright pour tout appel en attente sur un onglet ferme.
    Garde-fou de 5 s pour ne jamais geler la suite de tests elle-meme."""

    def __init__(self, pages, operation_gelee="pdf"):
        super().__init__(pages)
        self.operation_gelee = operation_gelee
        self.fermee = threading.Event()

    def _geler(self):
        if not self.fermee.wait(timeout=5):
            raise AssertionError("le chien de garde n'a jamais ferme l'onglet")
        raise ErreurPlaywright("Target page, context or browser has been closed")

    def pdf(self, **_kwargs):
        if self.operation_gelee == "pdf":
            self._geler()

    def content(self):
        if self.operation_gelee == "content" and self.visitees:
            self._geler()
        return super().content()


class SessionAvecGarde(SessionFactice):
    def __init__(self, page):
        self.page = page
        self.fermetures_demandees = 0
        self.reinitialisations = 0
        self.thread_de_fermeture = None

    def fermer_page_bloquee(self):
        self.fermetures_demandees += 1
        self.thread_de_fermeture = threading.current_thread()
        self.page.fermee.set()
        return True

    def reinitialiser_page(self, imprimer=print):
        self.reinitialisations += 1
        self.page = PageFactice({})


def test_une_impression_pdf_qui_ne_rend_jamais_la_main_est_interrompue(tmp_path):
    page = PageQuiGele({})
    page.goto("https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=1")
    session = SessionAvecGarde(page)
    journal = []
    ena = Ena(session, imprimer=journal.append)
    ena.delai_garde_s = 0.05

    debut = time.monotonic()
    with pytest.raises(NavigateurBloque):
        ena.capturer_pdf_page_courante(tmp_path / "page.pdf")

    assert time.monotonic() - debut < 3
    assert session.fermetures_demandees == 1
    # La fermeture vient d'un AUTRE thread : celui de l'archivage est
    # precisement celui qui est bloque.
    assert session.thread_de_fermeture is not threading.current_thread()
    # Une page neuve remplace l'onglet ferme, pour que la suite reparte.
    assert session.reinitialisations == 1
    assert any("aucune reponse" in ligne for ligne in journal)


def test_navigateur_bloque_est_une_erreur_playwright_isolee_comme_les_autres():
    # L'archiveur isole deja chaque page sur ErreurPlaywright : un blocage
    # interrompu ne doit couter que cette page, jamais le cours.
    assert issubclass(NavigateurBloque, ErreurPlaywright)


def test_une_navigation_qui_ne_rend_jamais_la_main_est_rejouee_sur_une_page_neuve():
    # _visiter se repare deja une fois sur ErreurPlaywright : un blocage
    # interrompu par le chien de garde profite de la meme reprise, sur la
    # page neuve deja ouverte par le chien de garde (une seule reparation).
    page = PageQuiGele({"modules": "<html></html>"}, operation_gelee="content")
    session = SessionAvecGarde(page)
    ena = Ena(session, imprimer=lambda _t: None)
    ena.delai_garde_s = 0.05

    html = ena._visiter("/ena/site/modules?idSite=1")

    assert html == "<html></html>"
    assert session.fermetures_demandees == 1
    assert session.reinitialisations == 1
    assert session.page.visitees == [BASE + "/ena/site/modules?idSite=1"]


def test_le_chien_de_garde_n_intervient_pas_sur_un_appel_normal(tmp_path):
    page = PageQuiGele({}, operation_gelee=None)
    page.goto("https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=1")
    session = SessionAvecGarde(page)
    ena = Ena(session, imprimer=lambda _t: None)
    ena.delai_garde_s = 0.05

    ena.capturer_pdf_page_courante(tmp_path / "page.pdf")
    time.sleep(0.2)

    assert session.fermetures_demandees == 0
    assert session.reinitialisations == 0


# --- Plan de cours officiel : lien lu dans la liste DETAILLEE des cours ---

LIEN_PLAN = (
    "https://sitescours.monportail.ulaval.ca/analytique/evenement/plancours"
    "?idFichier=1&idSite=100001&url=https%3A%2F%2Fsitescours.monportail.ulaval.ca"
    "%2Fcontenu%2Fsitescours%2F000%2F00000%2F202601%2Fsite100001%2Fplancours%2FABC-1000.pdf"
)


def test_sites_de_session_renseigne_le_lien_du_plan_de_cours_officiel():
    # Le tableau de bord demande la liste SIMPLE, sans lien de plan de
    # cours ; la liste DETAILLEE du meme service le porte
    # (sectionsSiteUtilisateur[].urlPlanDeCoursPdf). Ce lien est un
    # telechargement ordinaire, jamais la commande cmdObtenirPlanCours du
    # site de cours, qui publie une nouvelle version.
    html = _accordeon_cours_suivis(_carte_cours("100001", "Ethique"))
    page = PageAvecFiltreSession(["Hiver 2026"], {"Hiver 2026": html})
    page.plans_de_cours = {"100001": LIEN_PLAN}
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202601", libelle="Hiver 2026"))

    assert cours[0].url_plan_de_cours == LIEN_PLAN
    appel = page.request.appels[0]
    requete = parse_qs(urlsplit(appel["url"]).query)
    assert requete["degreinformation"] == ["DETAILLE"]
    assert requete["codesession"] == ["202601"]
    # Le service s'authentifie par jeton, pas par temoin : celui du tableau
    # de bord est repris tel quel.
    assert appel["headers"] == {"Authorization": "Bearer jeton-de-test"}


def test_sites_de_session_rend_les_cours_meme_si_la_liste_detaillee_echoue():
    # Sans lien, l'archiveur consigne le plan de cours en echec : jamais la
    # perte de toute la session pour un lien manquant.
    html = _accordeon_cours_suivis(_carte_cours("100001", "Ethique"))
    page = PageAvecFiltreSession(["Hiver 2026"], {"Hiver 2026": html})
    page.liste_detaillee_en_echec = True
    journal = []
    ena = Ena(SessionFactice({}), imprimer=journal.append)
    ena.session.page = page

    cours = ena.sites_de_session(Session(code="202601", libelle="Hiver 2026"))

    assert [c.id_site for c in cours] == ["100001"]
    assert cours[0].url_plan_de_cours is None
    assert any("plan" in ligne for ligne in journal)


def test_urls_plans_de_cours_lit_le_premier_lien_de_chaque_cours():
    donnees = {
        "codeSession": "202601",
        "sitesSuivis": [
            {"idSite": "1", "typeSite": "COURS", "sectionsSiteUtilisateur": [{}, {"urlPlanDeCoursPdf": "https://a/1.pdf"}]},
            {"idSite": "2", "typeSite": "COURS", "sectionsSiteUtilisateur": [{"urlPlanDeCoursPdf": ""}]},
            {"idSite": "3", "typeSite": "COURS"},
            {"idSite": "9", "typeSite": "FORMATION", "sectionsSiteUtilisateur": [{"urlPlanDeCoursPdf": "https://a/9.pdf"}]},
        ],
    }

    assert urls_plans_de_cours_depuis_reponse(donnees, "202601") == {"1": "https://a/1.pdf"}


def test_urls_plans_de_cours_refuse_une_reponse_d_une_autre_session():
    with pytest.raises(ValueError):
        urls_plans_de_cours_depuis_reponse({"codeSession": "202509", "sitesSuivis": []}, "202601")

