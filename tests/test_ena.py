import pytest
from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright

from extracteur.ena import (
    CANDIDATS_DIAGNOSTIC_SESSIONS,
    DELAI_OBSERVATION_MINIMALE_STABILISATION_MS,
    INTERVALLE_SONDAGE_STABILISATION_MS,
    URL,
    Ena,
    SelecteurSessionsIllisible,
    SelecteurSessionsIndisponible,
    SelecteurSessionsInstable,
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
    cours de navigation : aucun marqueur d'authentification, quel que soit le
    contenu demande."""

    def locator(self, _selecteur):
        return LocatorFactice(0)

    def get_by_text(self, _texte):
        return LocatorFactice(0)


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


# Nom URL-encode (espace et accent) : le nom du depot vient de l'URL, jamais
# du texte affiche du lien, qui peut etre tronque par la plateforme.
LIEN_DOCUMENT_DEPOSE = (
    "/contenu/sitescours/040/04000/202601/site181216/depots"
    "/Z1-PHI3900-H2026-TP2%20-%20%C3%89thique.docx?identifiant=abc"
)


def test_fichiers_de_depot_rend_des_depots_complets():
    # Cable sur depots_depuis_html, et non fichiers_depuis_html : seule la
    # premiere porte "Depose par" et la date de remise, la seule trace de qui
    # a remis quoi sur un travail d'equipe.
    html = f"""
    <table>
      <tr><th>Nom du document</th><th>Taille</th><th>Déposé par</th><th>Date de remise</th></tr>
      <tr>
        <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-PHI3900-H2026-TP2 - Éthique.docx</a></td>
        <td>3,25 Mo</td>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
      </tr>
    </table>
    """
    ena = Ena(SessionFactice({"onglet=boiteDepots": html}))

    depots = ena.fichiers_de_depot(
        Evaluation(id_site="181216", id_evaluation="1035434", titre="TP2")
    )

    assert len(depots) == 1
    assert depots[0].nom == "Z1-PHI3900-H2026-TP2 - Éthique.docx"
    assert depots[0].url == LIEN_DOCUMENT_DEPOSE
    assert depots[0].taille == "3,25 Mo"
    assert depots[0].depose_par == "Buteau, Laurent"
    assert depots[0].date_remise == "12 avr. 2026 18h43"


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


class LocatorBoutonSessionFactice:
    """Simule le Locator du bouton du selecteur de sessions
    (Ena.SELECTEUR_SESSIONS) : sa presence (wait_for), et le filtrage par
    texte exact utilise pour confirmer, apres un clic sur une option, que le
    bouton affiche bien la session demandee.

    Lit sa valeur courante directement sur la page factice
    (`page.session_selectionnee`), mise a jour par le clic sur une option
    (voir LocatorOptionsSessionsFactice.first) -- exactement le mecanisme de
    confirmation reel : le bouton porte la session choisie.
    """

    def __init__(self, page, present=True, texte_impose=None):
        self.page = page
        self.present = present
        self._texte_impose = texte_impose

    def _texte(self):
        if self._texte_impose is not None:
            return self._texte_impose
        return self.page.session_selectionnee or ""

    def count(self):
        return 1 if self.present else 0

    def wait_for(self, timeout=None):
        if not self.present:
            raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")

    def filter(self, has_text=None):
        correspond = self.present and (has_text is None or bool(has_text.search(self._texte())))
        return LocatorBoutonSessionFactice(self.page, present=correspond, texte_impose=self._texte())


class LocatorOptionsSessionsFactice:
    """Simule le Locator Playwright rendu par page.locator("a") (toute la
    page, pas seulement le bouton) puis filtre par la forme du libelle.

    Durcie a dessein : .filter(has_text=...) n'accepte qu'un motif regex
    compile, applique par .search() aux libelles reellement exposes par
    .locator("a"). Un code qui reperait les options par un mecanisme
    different (position DOM, role ARIA sans rapport) ne verrait donc rien
    ici, contrairement a l'ancienne doublure qui acceptait n'importe quoi et
    rendait le clic toujours gagnant.
    """

    def __init__(self, page, libelles):
        self.page = page
        self.libelles = libelles

    def count(self):
        return len(self.libelles)

    def all_text_contents(self):
        return list(self.libelles)

    def filter(self, has_text=None):
        correspondants = self.libelles
        if has_text is not None:
            correspondants = [libelle for libelle in self.libelles if has_text.search(libelle)]
        return LocatorOptionsSessionsFactice(self.page, correspondants)

    @property
    def first(self):
        page = self.page
        libelles = self.libelles

        class Lien:
            def click(self, **_kwargs):
                if not libelles:
                    raise ErreurDelaiPlaywright("Timeout 5000ms exceeded.")
                # Nettoyer le texte avant de l'assigner, pour correspondre
                # au comportement reel ou les cours sont recuperes via le
                # texte nettoye de la session selectionnee.
                page.session_selectionnee = libelles[0].strip()

        return Lien()


class PageAvecSelecteurSessions(PageFactice):
    """Simule /portail/cours : le menu deroulant ouvre un panneau ; cliquer
    une option (un lien) recharge la liste des cours de la session choisie.

    Le panneau n'est PAS un descendant du bouton SELECTEUR_SESSIONS : c'est
    la structure reelle constatee en session (menu deroulant AngularJS). Les
    options ne sont donc exposees que par SELECTEUR_OPTIONS_SESSIONS -- toute
    la page, sauf l'interieur du bouton -- jamais par un selecteur imbrique
    dans le bouton. Le bouton lui-meme (SELECTEUR_SESSIONS) est present des
    le depart et confirme, via LocatorBoutonSessionFactice, la session
    reellement selectionnee apres un clic.
    """

    def __init__(self, libelles_sessions, html_par_session=None):
        super().__init__({})
        self.libelles_sessions = libelles_sessions
        self.html_par_session = html_par_session or {}
        self.selecteur_ouvert = False
        self.session_selectionnee = None

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorBoutonSessionFactice(self)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_sessions)
        return LocatorFactice(0)

    def content(self):
        return self.html_par_session.get(self.session_selectionnee, "<html></html>")


class LocatorOptionsSessionsSansConfirmationFactice(LocatorOptionsSessionsFactice):
    """Simule un clic perdu : l'option est bien cliquee, mais la valeur du
    bouton ne change jamais -- reproduit un clic tombe dans le vide sur une
    page pas encore prete (le DOM ne s'est pas encore mis a jour)."""

    def filter(self, has_text=None):
        correspondants = self.libelles
        if has_text is not None:
            correspondants = [libelle for libelle in self.libelles if has_text.search(libelle)]
        return LocatorOptionsSessionsSansConfirmationFactice(self.page, correspondants)

    @property
    def first(self):
        libelles = self.libelles

        class Lien:
            def click(self, **_kwargs):
                if not libelles:
                    raise ErreurDelaiPlaywright("Timeout 5000ms exceeded.")
                # Ne met jamais a jour page.session_selectionnee : le bouton
                # ne confirmera donc jamais le changement demande.

        return Lien()


class PageAvecClicPerdu(PageAvecSelecteurSessions):
    """Le clic sur l'option de session ne fait jamais bouger la valeur
    affichee par le bouton -- reproduit un clic tombe dans le vide sur une
    page pas encore rendue. Lire la page dans cet etat rendrait les cours
    d'une autre session (celle encore affichee) sous le nom demande."""

    def locator(self, selecteur):
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsSansConfirmationFactice(self, self.libelles_sessions)
        return super().locator(selecteur)


def test_sessions_disponibles_leve_session_expiree_si_page_non_authentifiee():
    # Si la session Microsoft expire pendant la lecture de /portail/cours,
    # la plateforme sert une page de connexion. Sans ce garde-fou,
    # sessions_disponibles rendrait une liste vide : une archive silencieusement
    # incomplete. Il faut lever SessionExpiree pour mettre la file en pause.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifieeAvecSelecteurSessions(["Hiver 2026"])

    with pytest.raises(SessionExpiree):
        ena.sessions_disponibles()


def test_sites_de_session_leve_session_expiree_si_page_non_authentifiee():
    # Si la session Microsoft expire apres la selection d'une session,
    # la plateforme sert une page de connexion. Sans ce garde-fou,
    # sites_de_session rendrait une liste vide : une archive silencieusement
    # incomplete. Il faut lever SessionExpiree pour mettre la file en pause.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageNonAuthentifieeAvecSelecteurSessions(["Hiver 2026"])

    with pytest.raises(SessionExpiree):
        ena.sites_de_session(Session(code="202601", libelle="Hiver 2026"))


def test_sessions_disponibles_ouvre_le_selecteur_et_liste_les_sessions():
    # Aucun identifiant de site d'amorcage : la page est a une URL stable.
    page = PageAvecSelecteurSessions(["Hiver 2026", "Automne 2025"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert any("/portail/cours" in u for u in page.visitees)
    assert page.selecteur_ouvert is True
    assert [s.libelle for s in sessions] == ["Hiver 2026", "Automne 2025"]
    assert [s.code for s in sessions] == ["202601", "202509"]


def test_sites_de_session_selectionne_la_session_puis_extrait_les_cours():
    html_hiver_2026 = '<a href="/ena/site/accueil?idSite=181216">Éthique</a>'
    page = PageAvecSelecteurSessions(["Hiver 2026"], {"Hiver 2026": html_hiver_2026})
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202601", libelle="Hiver 2026")

    cours = ena.sites_de_session(session)

    assert page.session_selectionnee == "Hiver 2026"
    assert [c.id_site for c in cours] == ["181216"]
    assert cours[0].session == session


def test_sites_de_session_selectionne_exactement_le_bon_libelle():
    # Deux options partagent un prefixe : le libelle le plus court ne doit
    # jamais capturer par erreur le plus long (equivalent de exact=True).
    html_automne_2025 = '<a href="/ena/site/accueil?idSite=1">A</a>'
    page = PageAvecSelecteurSessions(
        ["Automne 2025", "Automne 2025 supplémentaire"],
        {"Automne 2025": html_automne_2025},
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202509", libelle="Automne 2025")

    cours = ena.sites_de_session(session)

    assert page.session_selectionnee == "Automne 2025"
    assert [c.id_site for c in cours] == ["1"]


def test_sites_de_session_sans_cours_rend_une_liste_vide():
    # La session courante de l'utilisateur est un exemple reel de session vide :
    # ce n'est pas une erreur, juste une liste vide. L'option existe dans le
    # selecteur (elle est bien selectionnable) mais ne rend aucun cours.
    page = PageAvecSelecteurSessions(["Hiver 2026"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202601", libelle="Hiver 2026")

    cours = ena.sites_de_session(session)

    assert page.session_selectionnee == "Hiver 2026"
    assert cours == []


def test_sites_de_session_leve_si_le_clic_ne_confirme_jamais_le_changement():
    # Le clic sur l'option est tombe dans le vide (page pas encore prete) :
    # le bouton continue d'afficher l'ancienne valeur (ou aucune). Lire la
    # page dans cet etat rendrait les cours d'une autre session sous le nom
    # demande -- une corruption de donnees pire qu'une liste vide. On leve
    # donc bruyamment plutot que de deviner.
    page = PageAvecClicPerdu(["Hiver 2023"], {"Hiver 2023": "<html></html>"})
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202301", libelle="Hiver 2023")

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sites_de_session(session)


class PageNonAuthentifieeAvecSelecteurSessions(PageNonAuthentifiee):
    """Simule une page non authentifiee (login) avec le selecteur des sessions,
    pour tester que sessions_disponibles() et sites_de_session() levent
    SessionExpiree plutot que de rendre une liste vide."""

    def __init__(self, libelles_sessions, html_par_session=None):
        super().__init__({})
        self.libelles_sessions = libelles_sessions
        self.html_par_session = html_par_session or {}
        self.selecteur_ouvert = False
        self.session_selectionnee = None

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_sessions)
        return LocatorFactice(0)

    def content(self):
        return self.html_par_session.get(self.session_selectionnee, "<html></html>")


class PageAvecPanneauFrere(PageFactice):
    """Reproduit fidelement le bug observe en session reelle : le clic sur le
    bouton ouvre bien le panneau, mais celui-ci n'est jamais un descendant du
    bouton -- c'est un conteneur frere, ailleurs dans le DOM. L'ancien
    selecteur imbrique (SELECTEUR_SESSIONS + " a") ne trouve donc jamais rien
    ici ; seule une recherche page entiere par la forme du libelle le peut.
    """

    def __init__(self, libelles_sessions):
        super().__init__({})
        self.libelles_sessions = libelles_sessions
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        # L'ancien mecanisme, imbrique dans le bouton : ne doit plus jamais
        # etre interroge, et ne trouverait de toute facon rien ici.
        if selecteur == f"{Ena.SELECTEUR_SESSIONS} a":
            return LocatorOptionsSessionsFactice(self, [])
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_sessions)
        return LocatorFactice(0)


def test_sessions_disponibles_lit_les_options_d_un_panneau_frere_du_bouton():
    # Symptome reel : "Aucune session trouvee" alors que la connexion
    # fonctionne. Cause reelle : le panneau ouvert par le clic est un
    # conteneur frere du bouton, pas un descendant -- l'ancien selecteur
    # imbrique ne pouvait donc jamais rien y trouver.
    page = PageAvecPanneauFrere(["Hiver 2026", "Automne 2025"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert page.selecteur_ouvert is True
    assert [s.libelle for s in sessions] == ["Hiver 2026", "Automne 2025"]


class PageAvecBoutonPortantLaValeurCourante(PageFactice):
    """Reproduit le risque signale en inspection reelle : le bouton du
    selecteur porte sa valeur courante (ex. "Automne 2025"), qui matche
    exactement le meme motif de libelle que les douze options. Aujourd'hui
    c'est un <span>, jamais un <a> -- mais si un futur changement de markup
    en faisait un <a>, il vivrait a l'interieur de SELECTEUR_SESSIONS et
    porterait le meme texte qu'une des options du conteneur frere.
    """

    def __init__(self, libelles_options, libelle_bouton):
        super().__init__({})
        self.libelles_options = libelles_options
        self.libelle_bouton = libelle_bouton
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_options)
        if selecteur == "a":
            # Sans l'exclusion, l'ancre du bouton apparaitrait aussi ici, en
            # plus des douze options du conteneur frere.
            return LocatorOptionsSessionsFactice(
                self, self.libelles_options + [self.libelle_bouton]
            )
        return LocatorFactice(0)


def test_sessions_disponibles_exclut_la_valeur_courante_du_bouton_sans_doublon():
    # Le bouton porte "Automne 2025" comme valeur courante ; les options du
    # conteneur frere l'incluent deja legitimement. Un mecanisme qui ne
    # distinguerait pas l'interieur du bouton du reste de la page rendrait
    # treize sessions, dont un "Automne 2025" en double.
    douze_sessions = [
        "Hiver 2027", "Automne 2026", "Hiver 2026", "Automne 2025", "Été 2025",
        "Hiver 2025", "Automne 2024", "Été 2024", "Hiver 2024", "Automne 2023",
        "Été 2023", "Hiver 2023",
    ]
    page = PageAvecBoutonPortantLaValeurCourante(douze_sessions, libelle_bouton="Automne 2025")
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert len(sessions) == 12
    assert len({s.libelle for s in sessions}) == 12


class PageSelecteurOuvertSansOptions(PageFactice):
    """Le clic sur le selecteur reussit (le panneau s'ouvre bel et bien),
    mais aucune option ne correspond a la forme attendue d'un libelle de
    session : DOM change, texte non reconnu, etc. Ne doit jamais rendre une
    liste vide en silence."""

    def click(self, *_args, **_kwargs):
        pass

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        return LocatorOptionsSessionsFactice(self, [])


def test_sessions_disponibles_leve_si_le_panneau_ouvert_ne_rend_aucune_option():
    # Le pire mode de defaillance du projet : une liste de sessions vide
    # signifie que l'outil n'archive plus rien du tout, en silence. Si le
    # panneau s'ouvre mais qu'aucune option n'y est trouvee, il faut le dire
    # bruyamment plutot que de rendre [].
    ena = Ena(SessionFactice({}))
    ena.session.page = PageSelecteurOuvertSansOptions({})

    with pytest.raises(SelecteurSessionsIllisible):
        ena.sessions_disponibles()


class PageSelecteurSessionsIntrouvable(PageFactice):
    """Le bouton du selecteur est bien present dans le DOM (wait_for reussit),
    mais le clic dessus echoue (DOM change entre l'attente et le clic, ou
    element non actionnable). Ne doit plus jamais rendre une liste vide en
    silence : une session ainsi ratee disparaitrait de l'enumeration sans le
    moindre signe."""

    def click(self, *_args, **_kwargs):
        raise ErreurDelaiPlaywright("Timeout 5000ms exceeded.")

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        return LocatorOptionsSessionsFactice(self, [])


def test_sessions_disponibles_leve_si_le_clic_sur_le_bouton_echoue_malgre_sa_presence():
    # Symptome reel corrige ici : une session vide et un echec de chargement
    # etaient indiscernables. Le bouton est present (wait_for reussit) mais
    # le clic echoue : ce n'est plus tolere, on leve une exception explicite
    # plutot que de rendre [] et laisser croire a une enumeration reussie.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageSelecteurSessionsIntrouvable({})

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sessions_disponibles()


class LocatorBoutonJamaisPretFactice:
    """Le bouton du selecteur n'est jamais trouve, quel que soit le delai
    accorde -- un vrai probleme de chargement, distinct d'une session sans
    cours."""

    def wait_for(self, timeout=None):
        raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")


class PageSelecteurJamaisRendu(PageFactice):
    """Le bouton du selecteur de sessions n'apparait jamais dans le DOM.
    Reproduit un vrai probleme de chargement (page cassee, contenu jamais
    rendu). Ne doit jamais rendre une liste vide."""

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorBoutonJamaisPretFactice()
        return LocatorFactice(0)


def test_sessions_disponibles_leve_si_le_bouton_du_selecteur_n_apparait_jamais():
    # Le pire mode de defaillance du projet : une liste de sessions vide
    # signifie que l'outil n'archive plus rien du tout, en silence. Si le
    # bouton du selecteur n'apparait jamais, meme avec un delai genereux, il
    # faut le dire bruyamment plutot que de rendre [].
    ena = Ena(SessionFactice({}))
    ena.session.page = PageSelecteurJamaisRendu({})

    with pytest.raises(SelecteurSessionsIndisponible):
        ena.sessions_disponibles()


class LocatorBoutonRenduLentFactice:
    """Simule un bouton qui ne repond a wait_for que si on lui laisse un
    delai au moins aussi genereux que celui observe en session reelle (8 a 9
    secondes de rendu AngularJS). Une fois ce delai atteint, marque la page
    factice comme rendue -- exactement ce que ferait un vrai Locator
    Playwright qui sonde le DOM jusqu'a trouver l'element ou expirer."""

    def __init__(self, page):
        self.page = page

    def wait_for(self, timeout=None):
        if timeout is None or timeout < self.page.delai_rendu_ms:
            raise ErreurDelaiPlaywright(f"Timeout {timeout}ms exceeded.")
        self.page.rendu = True


class PageAuRenduLent(PageFactice):
    """Reproduit fidelement le defaut constate en session reelle :
    /portail/cours (application AngularJS) ne rend son contenu qu'apres un
    delai (8 a 9 secondes en pratique), largement au-dela du signal reseau
    "networkidle" qu'attendait l'ancien code. Tant que ce delai n'est pas
    couvert par l'attente, le bouton du selecteur est introuvable et la page
    ne contient aucune option -- exactement ce que verrait une lecture
    prematuree.
    """

    def __init__(self, libelles_sessions, delai_rendu_ms):
        super().__init__({})
        self.libelles_sessions = libelles_sessions
        self.delai_rendu_ms = delai_rendu_ms
        self.rendu = False
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            # Marqueur d'authentification : distinct du rendu Angular du
            # selecteur de sessions, il ne depend donc pas de self.rendu.
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorBoutonRenduLentFactice(self)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_sessions if self.rendu else [])
        return LocatorFactice(0)


def test_sessions_disponibles_attend_un_rendu_lent_du_bouton_du_selecteur():
    # /portail/cours (application AngularJS) met 8 a 9 secondes a se rendre
    # en usage reel : "networkidle" se declenche bien avant que le bouton
    # n'existe dans le DOM. Le delai transmis a l'attente doit etre assez
    # genereux pour couvrir ce rendu, sinon la session semble a tort "sans
    # cours" -- exactement le defaut qui a fait disparaitre deux sessions
    # entieres d'une enumeration reelle.
    page = PageAuRenduLent(["Hiver 2023", "Été 2023"], delai_rendu_ms=9000)
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert [s.libelle for s in sessions] == ["Hiver 2023", "Été 2023"]


class LocatorTextesFactice(LocatorFactice):
    """Simule un Locator Playwright expose par un selecteur candidat de
    diagnostic : compte, texte, et filtrage par motif (comme _options_sessions)."""

    def __init__(self, textes):
        super().__init__(len(textes))
        self._textes = textes

    def all_text_contents(self):
        return list(self._textes)

    def filter(self, has_text=None):
        textes = self._textes
        if has_text is not None:
            textes = [texte for texte in textes if has_text.search(texte)]
        return LocatorTextesFactice(textes)


class PageDiagnostic(PageFactice):
    """Simule le DOM une fois le panneau ouvert, avec des reponses
    differentes selon le selecteur candidat interroge."""

    def __init__(self, textes_par_selecteur, nombre_liens_id_site):
        super().__init__({})
        self.textes_par_selecteur = textes_par_selecteur
        self.nombre_liens_id_site = nombre_liens_id_site
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == "a[href*='idSite=']":
            return LocatorFactice(self.nombre_liens_id_site)
        return LocatorTextesFactice(self.textes_par_selecteur.get(selecteur, []))


def test_diagnostiquer_sessions_rend_le_compte_et_l_echantillon_par_candidat():
    page = PageDiagnostic(
        {
            "[role=option]": [],
            ".mpo-deroulant-element": [],
            "li": ["Hiver 2026", "Automne 2025"],
            "a": ["Hiver 2026", "Automne 2025", "Profil"],
            # Mecanisme reellement utilise par _options_sessions : les memes
            # options que "a", mais deja privees de l'interieur du bouton.
            Ena.SELECTEUR_OPTIONS_SESSIONS: ["Hiver 2026", "Automne 2025", "Profil"],
        },
        nombre_liens_id_site=9,
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    rapport = ena.diagnostiquer_sessions()

    assert page.selecteur_ouvert is True
    candidats = dict((selecteur, (nombre, echantillon)) for selecteur, nombre, echantillon in rapport["candidats"])
    assert candidats["[role=option]"] == (0, [])
    assert candidats["li"] == (2, ["Hiver 2026", "Automne 2025"])
    assert candidats["a"] == (3, ["Hiver 2026", "Automne 2025", "Profil"])
    # La forme du libelle, mecanisme reellement utilise, exclut "Profil".
    assert candidats["forme du libelle (saison + annee)"] == (2, ["Hiver 2026", "Automne 2025"])
    assert rapport["liens_id_site"] == 9


class PageAvecClasseOptionSession(PageFactice):
    """Simule le DOM reel constate a l'inspection : les options portent la
    classe canonique `mpo-deroulant-elem`. Ce mecanisme est repere en premier,
    avant tout recours a la forme du libelle."""

    def __init__(self, libelles_sessions):
        super().__init__({})
        self.libelles_sessions = libelles_sessions
        self.selecteur_ouvert = False
        self.session_selectionnee = None

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS_CLASSE:
            return LocatorOptionsSessionsFactice(self, self.libelles_sessions)
        # Le repli par forme de libelle ne doit jamais etre interroge quand
        # la classe canonique a deja tout trouve.
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            raise AssertionError("le repli par forme de libelle a ete interroge alors que la classe canonique suffisait")
        return LocatorFactice(0)


def test_sessions_disponibles_repere_les_options_par_la_classe_canonique():
    # Inspection du DOM reel : chaque option porte la classe
    # `mpo-deroulant-elem`. C'est nettement plus stable qu'un motif de
    # libelle, et ca ne peut pas capter par erreur un lien du menu global.
    page = PageAvecClasseOptionSession(["Hiver 2027", "Automne 2026"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert [s.libelle for s in sessions] == ["Hiver 2027", "Automne 2026"]


class PageAvecEspacesAutourDuLibelle(PageFactice):
    """Simule un panneau ou la classe canonique a disparu (marquage change),
    forcant le repli par forme de libelle -- et ou le texte brut porte des
    espaces de mise en forme en tete et en fin, comme le redoutait la revue.
    """

    def __init__(self, libelles_bruts):
        super().__init__({})
        self.libelles_bruts = libelles_bruts
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS_CLASSE:
            # La classe canonique ne trouve plus rien : force le repli.
            return LocatorFactice(0)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS:
            return LocatorOptionsSessionsFactice(self, self.libelles_bruts)
        return LocatorFactice(0)


def test_sessions_disponibles_repli_tolere_les_espaces_de_tete_et_de_fin():
    # Meme si la classe canonique venait a disparaitre, le repli par forme de
    # libelle ne doit pas s'effondrer face a des espaces de mise en forme
    # autour du texte : un motif ancre confie tel quel au navigateur (donc
    # applique au texte brut, non nettoye) les manquerait completement.
    page = PageAvecEspacesAutourDuLibelle([" Hiver 2027 ", "\nAutomne 2026\n"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert [s.libelle for s in sessions] == ["Hiver 2027", "Automne 2026"]


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


def test_sites_de_session_tolere_les_espaces_autour_du_libelle():
    # Le mecanisme de selection doit tolerer les espaces parasites autour
    # du libelle, tout comme celui de lecture : si le DOM exposait un jour
    # le moindre espace ou saut de ligne autour du libelle (ex. " Hiver 2027 "),
    # la selection ne doit pas echouer silencieusement et laisser croire qu'on
    # a les cours d'une autre session.
    html_hiver_2026 = '<a href="/ena/site/accueil?idSite=181216">Ethique</a>'
    page = PageAvecSelecteurSessions(
        [" Hiver 2026 ", "\nAutomne 2025\n"],
        {"Hiver 2026": html_hiver_2026},
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session = Session(code="202601", libelle="Hiver 2026")

    cours = ena.sites_de_session(session)

    # Le selecteur s'est ouvert, une option avec espaces parasites a ete
    # trouvee (apres nettoyage), cliquee, et la page a ete reloadee.
    assert page.session_selectionnee == "Hiver 2026"
    assert [c.id_site for c in cours] == ["181216"]


class PageAvecPanneauProgressif(PageFactice):
    """Reproduit un panneau de sessions qui se peuple en differe (Angular) :
    le premier sondage du compte d'options n'en voit que trois sur douze ;
    tous les sondages suivants en voient les douze. La liste n'est jamais
    vide, donc aucune exception ne serait levee par une lecture immediate --
    c'est exactement le mode de defaillance partiel que la stabilisation du
    compte doit prevenir."""

    def __init__(self, libelles_partiels, libelles_complets):
        super().__init__({})
        self.libelles_partiels = libelles_partiels
        self.libelles_complets = libelles_complets
        self.nombre_sondages = 0
        self.selecteur_ouvert = False

    def click(self, selecteur, **_kwargs):
        if selecteur == Ena.SELECTEUR_SESSIONS:
            self.selecteur_ouvert = True

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS_CLASSE:
            self.nombre_sondages += 1
            libelles = (
                self.libelles_partiels if self.nombre_sondages == 1 else self.libelles_complets
            )
            return LocatorOptionsSessionsFactice(self, libelles)
        return LocatorFactice(0)


def test_sessions_disponibles_attend_la_stabilisation_d_un_panneau_qui_se_peuple_progressivement():
    # AngularJS peut peupler le panneau des sessions en differe : le premier
    # sondage du compte d'options peut tomber sur un etat partiel (neuf sur
    # douze en session reelle, ici trois sur douze). La liste n'est pas
    # vide, donc .count() et .all_text_contents() ne levent rien -- trois
    # sessions disparaitraient en silence sans une attente explicite de la
    # stabilisation du compte.
    douze_sessions = [
        "Hiver 2027", "Automne 2026", "Hiver 2026", "Automne 2025", "Été 2025",
        "Hiver 2025", "Automne 2024", "Été 2024", "Hiver 2024", "Automne 2023",
        "Été 2023", "Hiver 2023",
    ]
    page = PageAvecPanneauProgressif(
        libelles_partiels=douze_sessions[:3],
        libelles_complets=douze_sessions,
    )
    ena = Ena(SessionFactice({}))
    ena.session.page = page

    sessions = ena.sessions_disponibles()

    assert len(sessions) == 12
    assert [s.libelle for s in sessions] == douze_sessions


def test_diagnostiquer_sessions_survit_a_un_bouton_qui_n_apparait_jamais():
    # Le pire scenario pour ce mode : bouton absent, panne totale. C'est
    # precisement pour ce cas que le diagnostic existe ; il ne doit jamais
    # planter avant d'avoir rien examine.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageSelecteurJamaisRendu({})

    rapport = ena.diagnostiquer_sessions()

    assert rapport["echec_ouverture_selecteur"] is not None
    assert "selecteur de sessions" in rapport["echec_ouverture_selecteur"]
    # Tous les selecteurs candidats ont neanmoins ete interroges, plus la
    # forme du libelle : probablement tous a zero, ce qui est en soi une
    # information utile.
    assert len(rapport["candidats"]) == len(CANDIDATS_DIAGNOSTIC_SESSIONS) + 1
    for _selecteur, nombre, echantillon in rapport["candidats"]:
        assert nombre == 0
        assert echantillon == []
    assert rapport["liens_id_site"] == 0


class LocatorCompteOscillant:
    """Simule un Locator dont le compte oscille sans jamais se stabiliser
    (3, 9, 3, 9, ...), comme dans
    test_stabiliser_options_leve_si_le_compte_oscille_sans_jamais_se_stabiliser
    -- mais rejoue ici a travers diagnostiquer_sessions plutot que
    directement contre _stabiliser_options."""

    def __init__(self, page):
        self.page = page

    def count(self):
        self.page.nombre_sondages += 1
        return 3 if self.page.nombre_sondages % 2 else 9


class PageDiagnosticOptionsInstables(PageFactice):
    """Simule un panneau de sessions dont le compte d'options n'a jamais de
    palier : _stabiliser_options leve SelecteurSessionsInstable des qu'on
    l'interroge par la classe canonique. Sert a verifier que le diagnostic
    survit a cette exception plutot que de s'interrompre avant d'avoir
    examine le reste des selecteurs candidats."""

    def __init__(self):
        super().__init__({})
        self.nombre_sondages = 0

    def click(self, *_args, **_kwargs):
        pass

    def locator(self, selecteur):
        if selecteur == "a[href*='/ena/site/']":
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_SESSIONS:
            return LocatorFactice(1)
        if selecteur == Ena.SELECTEUR_OPTIONS_SESSIONS_CLASSE:
            return LocatorCompteOscillant(self)
        if selecteur == "a[href*='idSite=']":
            return LocatorFactice(4)
        return LocatorTextesFactice([])


def test_diagnostiquer_sessions_survit_a_une_oscillation_du_compte_d_options():
    # _stabiliser_options peut desormais lever SelecteurSessionsInstable
    # (voir test_stabiliser_options_leve_si_le_compte_oscille_sans_jamais_se_stabiliser).
    # Le diagnostic existe justement pour les situations ou plus rien ne
    # marche : il doit consigner cette instabilite dans son rapport et
    # poursuivre l'interrogation de tous les selecteurs candidats, jamais
    # s'interrompre.
    ena = Ena(SessionFactice({}))
    ena.session.page = PageDiagnosticOptionsInstables()

    rapport = ena.diagnostiquer_sessions()

    assert rapport["echec_stabilisation_options"] is not None
    assert "compte d'options" in rapport["echec_stabilisation_options"]
    # Les selecteurs candidats et le compte de liens idSite= restent
    # interroges malgre l'echec de stabilisation.
    assert len(rapport["candidats"]) == len(CANDIDATS_DIAGNOSTIC_SESSIONS) + 1
    assert rapport["liens_id_site"] == 4


def test_sites_de_session_leve_si_la_session_est_introuvable():
    # Une session absente du selecteur est une anomalie : ne pas lever
    # d'exception risque de rendre les cours de la session courante (ou d'une
    # autre) archives sous le nom de la session demandee, une corruption de
    # donnees pire qu'une extraction incomplete. On leve donc bruyamment.
    page = PageAvecSelecteurSessions(["Hiver 2026"])
    ena = Ena(SessionFactice({}))
    ena.session.page = page
    session_inexistante = Session(code="202509", libelle="Automne 2025")

    with pytest.raises(SelecteurSessionsIllisible):
        ena.sites_de_session(session_inexistante)


class LocatorDeCompte:
    """Simule un Locator dont .count() rend une valeur fixe, sans autre
    mecanisme -- suffisant pour tester _stabiliser_options isolement, qui
    n'appelle jamais autre chose que .count() sur ce qu'elle sonde."""

    def __init__(self, compte):
        self.compte = compte

    def count(self):
        return self.compte


class CompteEvolutif:
    """Rejoue une sequence de comptes programmee, comme celles mesurees par
    la revue contre la vraie fonction _stabiliser_options.

    Sans boucle (par defaut) : la derniere valeur de la sequence se repete
    indefiniment une fois atteinte -- simule un panneau qui trouve son palier
    (peuplement progressif, ou panneau vide en permanence).

    Avec boucle=True : la sequence entiere se repete sans fin -- simule une
    oscillation qui ne trouve jamais de palier.
    """

    def __init__(self, comptes, boucle=False):
        self.comptes = comptes
        self.boucle = boucle
        self.nombre_sondages = 0

    def obtenir_options(self):
        indice = self.nombre_sondages
        if self.boucle:
            indice %= len(self.comptes)
        else:
            indice = min(indice, len(self.comptes) - 1)
        self.nombre_sondages += 1
        return LocatorDeCompte(self.comptes[indice])


def _ena_pour_stabilisation():
    ena = Ena(SessionFactice({}))
    ena.session.page = PageFactice({})
    return ena


def test_stabiliser_options_attend_la_fin_d_un_peuplement_progressif():
    # Rejoue la sequence mesuree par la revue : 3, puis 12 en continu. Un
    # premier sondage a 3 ne doit plus suffire a conclure a la stabilite du
    # tout premier coup -- il faut voir le compte se maintenir sur plusieurs
    # sondages avant de conclure.
    sequence = CompteEvolutif([3, 12])
    ena = _ena_pour_stabilisation()

    options = ena._stabiliser_options(sequence.obtenir_options)

    assert options.count() == 12


def test_stabiliser_options_conclut_vite_un_compte_deja_stable():
    # Cas nominal : toutes les options sont presentes des le premier sondage.
    # Le cout doit rester borne (quelques sondages, une fraction de seconde),
    # meme si l'exigence est plus stricte qu'avant (trois sondages
    # consecutifs sur une fenetre d'au moins 600 ms, plutot que deux).
    sequence = CompteEvolutif([12])
    ena = _ena_pour_stabilisation()

    options = ena._stabiliser_options(sequence.obtenir_options)

    assert options.count() == 12
    sondages_attendus = (
        DELAI_OBSERVATION_MINIMALE_STABILISATION_MS // INTERVALLE_SONDAGE_STABILISATION_MS + 1
    )
    assert sequence.nombre_sondages == sondages_attendus


def test_stabiliser_options_leve_si_le_compte_oscille_sans_jamais_se_stabiliser():
    # Rejoue l'oscillation mesuree par la revue : 3, 9, 3, 9, ... ne trouve
    # jamais de palier. Rendre la derniere valeur sondee tronquerait la
    # liste de sessions en silence -- un echec de lecture explicite vaut
    # mieux qu'un resultat plausible mais faux.
    sequence = CompteEvolutif([3, 9], boucle=True)
    ena = _ena_pour_stabilisation()

    with pytest.raises(SelecteurSessionsInstable):
        ena._stabiliser_options(sequence.obtenir_options)


def test_stabiliser_options_conserve_le_comportement_actuel_pour_un_panneau_vide():
    # Un panneau vide en permanence (0, 0, 0, ...) est deja correct
    # aujourd'hui : c'est une vraie stabilite (le compte ne varie jamais),
    # pas une oscillation. Le durcissement ne doit pas le faire basculer en
    # SelecteurSessionsInstable.
    sequence = CompteEvolutif([0])
    ena = _ena_pour_stabilisation()

    options = ena._stabiliser_options(sequence.obtenir_options)

    assert options.count() == 0
