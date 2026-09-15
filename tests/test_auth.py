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


def test_reconnait_page_authentifiee_menu_de_compte_seul():
    """Test: une page avec le lien du menu de compte personnel est reconnue.

    Ce test verifie le cas de /portail/cours apres connexion, sans lien /ena/site/
    ni texte 'Liste des cours' ni 'Cours suivis', mais avec le lien du menu de
    compte personnel (monportail.ulaval.ca/mon-compte), releve en session reelle
    et absent de toute page publique ou d'erreur.
    """
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    # Pas de lien /ena/site/ ni texte "Liste des cours"
    page.ajouter_locateur("a[href*='/ena/site/']", 0)
    page.ajouter_texte("Liste des cours", 0)
    # Pas de "Cours suivis"
    page.ajouter_texte("Cours suivis", 0)
    # Mais le lien du menu de compte personnel est present
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/mon-compte']", 1)

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


def test_rejette_url_autorisation_microsoft_avec_redirect_uri_en_clair():
    """Test: fige le piege documente dans auth.py au lieu d'une URL Microsoft
    generique. L'URL d'autorisation reelle place le nom d'hote cible en clair
    dans son parametre redirect_uri (l'encodage pour cent ne touche que ':' et
    '/') ; un simple test par sous-chaine sur l'URL y trouverait
    "sitescours.monportail.ulaval.ca" avant toute connexion. Bug deja introduit
    une fois dans ce projet.
    """
    url = (
        "https://login.microsoftonline.com/56778bd5-6a3f-4bd3-a265-93163e4d5bfe/"
        "oauth2/v2.0/authorize?client_id=0925474d-a550-45a0-a749-396fbb526bdb"
        "&redirect_uri=https%3A%2F%2Fsitescours.monportail.ulaval.ca%2Fservices%2Foauth2%2Fretour%2F"
    )
    page = PageFactice(url)

    session = SessionNavigateurTestable(page)
    assert not session.est_connecte()


def test_rejette_page_sans_marqueur():
    """Test: une page sur le bon domaine sans marqueur n'est pas authentifiee."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/une/page/quelconque")
    # Aucun marqueur d'authentification
    page.ajouter_locateur("a[href*='/ena/site/']", 0)
    page.ajouter_texte("Liste des cours", 0)
    page.ajouter_texte("Cours suivis", 0)
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/mon-compte']", 0)

    session = SessionNavigateurTestable(page)
    assert not session.est_connecte()


def test_rejette_page_avec_menu_statique_mais_sans_menu_de_compte():
    """Test: un menu statique d'au moins cinq liens /portail ne suffit pas.

    Une page d'erreur ou une redirection intermediaire du domaine peut deja
    porter un menu statique de cinq liens ou plus vers /portail sans que
    l'utilisateur soit connecte : ce compte ne doit plus jamais suffire a lui
    seul, contrairement a l'ancien comptage heuristique.
    """
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/page_erreur")
    page.ajouter_locateur("a[href*='/ena/site/']", 0)
    page.ajouter_texte("Liste des cours", 0)
    page.ajouter_texte("Cours suivis", 0)
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/portail']", 19)
    page.ajouter_locateur("a[href*='monportail.ulaval.ca/mon-compte']", 0)

    session = SessionNavigateurTestable(page)
    assert not session.est_connecte()


def test_rejette_page_nulle():
    """Test: est_connecte() retourne False si page est None."""
    session = SessionNavigateurTestable()
    assert session.page is None
    assert not session.est_connecte()


# --- attendre_connexion : bavardage, rappel du selecteur de compte,
# --- dernier domaine observe (defauts 1, 2 et 4 de la tache) ---
#
# Horloge et sommeil sont toujours injectes : aucun de ces tests ne doit
# faire dormir la suite pour de vrai.


class HorlogeFactice:
    """Horloge deterministe : avance d'un pas fixe a chaque appel, sans
    jamais toucher a l'horloge reelle."""

    def __init__(self, pas=1.0):
        self._temps = 0.0
        self._pas = pas

    def __call__(self):
        maintenant = self._temps
        self._temps += self._pas
        return maintenant


def _sommeil_factice(_secondes):
    """Remplace time.sleep : ne dort jamais, seule l'horloge factice avance
    le temps simule."""


class SessionAttenteTestable(SessionNavigateur):
    """Version testable de SessionNavigateur pour attendre_connexion.

    est_connecte() est remplacee par un compteur d'appels plutot que par de
    vrais marqueurs Playwright, pour piloter precisement l'instant de la
    connexion reussie sans dependre de PageFactice. La page factice sert
    uniquement a exposer `.url`, lu par attendre_connexion pour peupler
    dernier_domaine_observe.
    """

    def __init__(self, page, connectee_au_nieme_appel=None):
        super().__init__(dossier_profil="/tmp/test")
        self.page = page
        self._connectee_au_nieme_appel = connectee_au_nieme_appel
        self._appels = 0

    def est_connecte(self):
        self._appels += 1
        if self._connectee_au_nieme_appel is None:
            return False
        return self._appels >= self._connectee_au_nieme_appel


def test_attendre_connexion_affiche_une_ligne_d_etat_a_intervalle_regulier():
    """La ligne d'etat apparait toutes les INTERVALLE_STATUT_ATTENTE secondes
    (5s), pas plus souvent : sur une attente de 15s avant succes, exactement
    trois lignes doivent etre emises (a 5s, 10s et 15s)."""
    page = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    # Connectee au 16e appel d'est_connecte() ; avec un pas d'horloge de 1s,
    # cela correspond a un succes juste apres la 3e ligne d'etat (15s).
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=16)
    messages = []

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is True
    lignes_etat = [message for message in messages if message.startswith("en attente")]
    assert len(lignes_etat) == 3
    for ligne, ecoule_attendu in zip(lignes_etat, (5, 10, 15)):
        assert f"({ecoule_attendu}s)" in ligne


def test_attendre_connexion_ne_dit_rien_si_la_connexion_est_immediate():
    """Une connexion detectee des le premier sondage n'affiche aucun bruit :
    pas de ligne d'etat pour une attente qui n'a pas eu lieu."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=1)
    messages = []

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is True
    assert messages == []


def test_attendre_connexion_rappelle_le_selecteur_de_compte_sur_microsoft():
    """Quand la page reste sur le domaine de connexion Microsoft au-dela du
    seuil, un rappel invite a choisir le bon compte parmi ceux proposes."""
    page = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=None)
    messages = []

    resultat = session.attendre_connexion(
        delai=40,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    rappels = [m for m in messages if "selecteur de compte" in m]
    assert len(rappels) == 1
    assert "Universite Laval" in rappels[0]
    assert session.dernier_domaine_observe == "login.microsoftonline.com"


def test_attendre_connexion_ne_rappelle_pas_le_selecteur_hors_domaine_microsoft():
    """Sur le bon domaine sans marqueur, rester bloque n'a rien a voir avec
    un selecteur de compte : aucun rappel ne doit apparaitre."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/une/page/quelconque")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=None)
    messages = []

    resultat = session.attendre_connexion(
        delai=40,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    assert not any("selecteur de compte" in m for m in messages)
    assert session.dernier_domaine_observe == HOTE_SITESCOURS


def test_attendre_connexion_pose_le_dernier_domaine_observe_sur_echec():
    """Le domaine du dernier sondage est conserve meme apres expiration du
    delai : c'est ce que _message_echec_connexion lit pour batir un message
    utile."""
    page = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=None)

    resultat = session.attendre_connexion(
        delai=3,
        imprimer=lambda _m: None,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    assert session.dernier_domaine_observe == "login.microsoftonline.com"
