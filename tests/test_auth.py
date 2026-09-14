import pytest
from urllib.parse import urlparse

from extracteur.auth import SessionNavigateur, HOTE_SITESCOURS


class LocatorFactice:
    """Simule un locator Playwright avec une methode count()."""

    def __init__(self, nombre):
        self._nombre = nombre

    def count(self):
        return self._nombre


class PageFactice:
    """Simule une page Playwright pour tester la detection de connexion."""

    def __init__(self, url):
        self.url = url
        self._locateurs = {}
        self._textes = {}

    def locator(self, selector):
        """Retourne le nombre de locateurs pour ce selecteur."""
        return LocatorFactice(self._locateurs.get(selector, 0))

    def get_by_text(self, texte):
        """Retourne le nombre de fois ce texte apparait."""
        return LocatorFactice(self._textes.get(texte, 0))

    def ajouter_locateur(self, selector, nombre):
        """Ajoute un nombre de locateurs pour un selecteur donne."""
        self._locateurs[selector] = nombre

    def ajouter_texte(self, texte, nombre):
        """Ajoute une ou plusieurs occurrences d'un texte."""
        self._textes[texte] = nombre


class SessionNavigateurTestable(SessionNavigateur):
    """Version testable qui accepte une page factice."""

    def __init__(self, page_factice=None):
        super().__init__(dossier_profil="/tmp/test")
        if page_factice:
            self.page = page_factice


def test_reconnait_page_site_cours_avec_lien_ena_site():
    """Test: une page de site de cours avec lien /ena/site/ est reconnue."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/ena/site/123")
    page.ajouter_locateur("a[href*='/ena/site/']", 1)

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()


def test_reconnait_page_site_cours_avec_liste_des_cours():
    """Test: une page de site de cours avec 'Liste des cours' est reconnue."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/ena/site/456")
    page.ajouter_texte("Liste des cours", 1)

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()


def test_reconnait_page_portail_avec_cours_suivis():
    """Test: la page /portail/cours avec 'Cours suivis' est reconnue."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    page.ajouter_texte("Cours suivis", 1)

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()


def test_reconnait_page_authentifiee_menu_global_seul():
    """Test: une page avec menu global authentifie (liens /portail) est reconnue.

    Ce test verifie le cas de /portail/cours apres connexion, sans lien /ena/site/
    ni texte 'Liste des cours', mais avec le menu global contenant plusieurs liens
    vers monportail.ulaval.ca/portail.
    """
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    # Pas de lien /ena/site/ ni texte "Liste des cours"
    page.ajouter_locateur("a[href*='/ena/site/']", 0)
    page.ajouter_texte("Liste des cours", 0)
    # Pas de "Cours suivis"
    page.ajouter_texte("Cours suivis", 0)
    # Mais 19 liens du menu global vers /portail (au seuil minimum de 5)
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/portail']", 19)

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()


def test_rejette_page_connexion_microsoft():
    """Test: une page de connexion Microsoft ne doit pas etre reconnue.

    La verification du hostname est le premier filtre. Une page sur
    login.microsoft.com ne passe donc pas est_connecte().
    """
    page = PageFactice("https://login.microsoft.com/common/oauth2/authorize")

    session = SessionNavigateurTestable(page)
    assert not session.est_connecte()


def test_rejette_page_sans_marqueur():
    """Test: une page sur le bon domaine sans marqueur n'est pas authentifiee."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/une/page/quelconque")
    # Aucun marqueur d'authentification
    page.ajouter_locateur("a[href*='/ena/site/']", 0)
    page.ajouter_texte("Liste des cours", 0)
    page.ajouter_texte("Cours suivis", 0)
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/portail']", 2)  # Pas assez

    session = SessionNavigateurTestable(page)
    assert not session.est_connecte()


def test_rejette_page_nulle():
    """Test: est_connecte() retourne False si page est None."""
    session = SessionNavigateurTestable()
    assert session.page is None
    assert not session.est_connecte()
