from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright

from extracteur.ena import URL, Ena
from extracteur.modele import Cours, Evaluation, Module, Session


class PageFactice:
    """Enregistre les URL visitees et rend le HTML programme."""

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


class SessionFactice:
    def __init__(self, pages):
        self.page = PageFactice(pages)


def test_urls_canoniques_sont_bien_formees():
    assert URL.modules("181216") == "/ena/site/modules?idSite=181216"
    assert URL.evaluations("181216") == "/ena/site/evaluations?idSite=181216"
    assert URL.resultats("181216") == "/ena/site/resultats?idSite=181216"
    assert URL.boite_depot("181216", "1035434") == (
        "/ena/site/evaluation?idSite=181216&idEvaluation=1035434&onglet=boiteDepots"
    )
    assert URL.module("181216", "1795743") == (
        "/ena/site/module?idSite=181216&idModule=1795743&editionModule=false"
    )
    assert URL.redirection("181216", "liste_modules") == (
        "/lieninterne/redirection/181216/liste_modules"
    )


class PageAvecPlanDeCours(PageFactice):
    """Simule le routeur lieninterne/redirection : seule une section donnee
    mene reellement au plan de cours ; les autres restent sur le routeur lui-meme,
    ce qui echoue au critere « URL sous /ena/site/ »."""

    URL_PLAN = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=181216&idModule=999"

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
        id_site="181216",
        sigle="PHI-3900",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") == "plan_de_cours"
    assert any("/lieninterne/redirection/181216/" in u for u in ena.session.page.visitees)


class PageRedirigeeVersAccueil(PageFactice):
    """Les candidats invalides redirigent silencieusement vers l'accueil (code
    200, pas de page_erreur) : un piege pour un critere fonde sur page_erreur."""

    URL_ACCUEIL = "https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=181216"
    URL_PLAN = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=181216&idModule=999"

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
        id_site="181216",
        sigle="PHI-3900",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    resultat = ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf")

    assert resultat == "plancours"
    assert len(ena.session.page.visitees) == 2


class PageValideSansMarqueur(PageFactice):
    """URL finale reelle, differente de l'accueil, mais sans texte de plan de
    cours : un site dont aucune section n'est un plan de cours."""

    URL_REELLE = "https://sitescours.monportail.ulaval.ca/ena/site/module?idSite=181216&idModule=1"

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
        id_site="181216",
        sigle="PHI-3900",
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
        '<a href="/ena/site/module?idSite=181216&idModule=1795743&editionModule=false">'
        "1. Introduction</a>"
    )
    ena = Ena(SessionFactice({"/ena/site/modules": html}))
    cours = Cours(
        id_site="181216",
        sigle="PHI-3900",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    modules = ena.modules(cours)
    assert [m.id_module for m in modules] == ["1795743"]
    assert "/ena/site/modules?idSite=181216" in ena.session.page.visitees[0]


def test_modules_absents_ne_font_pas_echouer():
    # Le site de formation EDI n'a ni modules ni evaluations.
    ena = Ena(SessionFactice({}))
    cours = Cours(
        id_site="149047",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    assert ena.modules(cours) == []


def test_evaluations_extraites():
    html = """
    <a href="/ena/site/evaluation?idSite=183033&idEvaluation=1035434&onglet">Exposé oral I</a>
    <a href="/ena/site/evaluation?idSite=183033&idEvaluation=1035435&onglet">Rapport de suivi I</a>
    """
    ena = Ena(SessionFactice({"/ena/site/evaluations": html}))
    cours = Cours(
        id_site="183033",
        sigle="GIN-3320",
        titre="Projet",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    evaluations = ena.evaluations(cours)
    assert [e.id_evaluation for e in evaluations] == ["1035434", "1035435"]
    assert evaluations[0].titre == "Exposé oral I"


def test_fichiers_de_depot_visitent_l_onglet_boite_depots():
    ena = Ena(SessionFactice({}))
    ena.fichiers_de_depot(Evaluation(id_site="183033", id_evaluation="1035434", titre="T"))
    assert "onglet=boiteDepots" in ena.session.page.visitees[0]


def test_fichiers_du_module_visitent_l_url_du_module():
    ena = Ena(SessionFactice({}))
    ena.fichiers_du_module(Module(id_site="181216", id_module="1795743", titre="M"))
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
    html = '<a href="/contenu/sitescours/181216/module1795743/doc.pdf">Document</a>'
    session = SessionFactice({})
    session.page = PageSansOngletContenu({"idModule=1795743": html})
    ena = Ena(session)

    fichiers = ena.fichiers_du_module(Module(id_site="181216", id_module="1795743", titre="M"))

    assert [f.nom for f in fichiers] == ["doc.pdf"]


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
            id_site="149047",
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
            id_site="149047",
            sigle=None,
            titre="EDI",
            session=Session(code="202209", libelle="Automne 2022"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    assert vues == ["Six biais"]
    assert nombre == 1
