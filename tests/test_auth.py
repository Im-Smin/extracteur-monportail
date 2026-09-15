import tempfile
from pathlib import Path

import pytest
from urllib.parse import urlparse

from playwright.sync_api import Error as ErreurPlaywright

from extracteur.auth import (
    SessionNavigateur,
    HOTE_SITESCOURS,
    HOTE_PORTAIL,
    PREFIXE_TITRE_FENETRE_PILOTEE,
    TITRE_FENETRE_PILOTEE,
    URL_DEPART,
)


class PageFactice:
    """Simule une page Playwright pour tester la detection de connexion.

    `titre` simule Page.title() ; `titre_leve`, quand fourni, fait echouer
    title() avec cette exception plutot que de rendre `titre` -- reproduit
    une page en pleine navigation dont le titre est momentanement illisible.
    """

    def __init__(self, url, titre=None, titre_leve=None):
        self.url = url
        self._fermee = False
        # Appels a goto() : sert a verifier que la page adoptee est bien
        # renavigee vers la page des cours, sans supposer qu'elle y est deja.
        self.appels_goto = []
        self._titre = titre
        self._titre_leve = titre_leve
        # Contenu passe a set_content(), et appels a bring_to_front() : sert
        # a verifier que ouvrir() affiche bien la page d'identification et
        # demande le focus, avant de naviguer vers monPortail.
        self.contenu_html = None
        self.appels_bring_to_front = 0
        # Journal chronologique de set_content/bring_to_front/goto : seul
        # moyen de verifier que la page d'identification est bien affichee
        # AVANT le depart vers monPortail, pas seulement qu'elle l'a ete a un
        # moment donne.
        self.journal = []

    def goto(self, url, **_kwargs):
        """Simule Page.goto : met a jour l'url et enregistre l'appel."""
        self.url = url
        self.appels_goto.append(url)
        self.journal.append(("goto", url))

    def is_closed(self):
        """Reflete l'etat reel de fermeture, comme Page.is_closed()."""
        return self._fermee

    def fermer(self):
        """Simule la fermeture reelle de la fenetre par l'utilisateur."""
        self._fermee = True

    def title(self):
        """Simule Page.title()."""
        if self._titre_leve is not None:
            raise self._titre_leve
        return self._titre

    def set_content(self, html):
        """Simule Page.set_content() : retient le contenu recu."""
        self.contenu_html = html
        self.journal.append(("set_content", html))

    def bring_to_front(self):
        """Simule Page.bring_to_front() : compte les appels."""
        self.appels_bring_to_front += 1
        self.journal.append(("bring_to_front", None))


class SessionNavigateurTestable(SessionNavigateur):
    """Version testable qui accepte une page factice."""

    def __init__(self, page_factice=None):
        super().__init__()
        if page_factice:
            self.page = page_factice


def test_reconnait_page_sitescours_quel_que_soit_son_contenu():
    """Une page sur sitescours.monportail.ulaval.ca est reconnue authentifiee
    des le nom d'hote, quel que soit son contenu.

    L'exigence d'un marqueur de contenu (lien /ena/site/, texte "Cours
    suivis", menu de compte personnel...) a ete abandonnee : trois correctifs
    successifs sur ce marqueur n'ont pas suffi en usage reel, une
    authentification federee n'atterrissant pas toujours au meme endroit. La
    garantie s'est deplacee en aval, au selecteur de sessions.
    """
    page = PageFactice(f"https://{HOTE_SITESCOURS}/une/page/quelconque")

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()


def test_reconnait_page_monportail_quel_que_soit_son_contenu():
    """Meme constat sur monportail.ulaval.ca (sans le sous-domaine
    sitescours) : autre hote attendu cote portail apres connexion, reconnu
    lui aussi sans marqueur de contenu."""
    page = PageFactice(f"https://{HOTE_PORTAIL}/mon-compte")

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


def test_rejette_page_nulle():
    """Test: est_connecte() retourne False si page est None."""
    session = SessionNavigateurTestable()
    assert session.page is None
    assert not session.est_connecte()


def test_est_connecte_navigue_vers_la_page_des_cours_apres_adoption():
    """Une fois la page authentifiee adoptee, le programme y navigue lui-meme
    vers la page des cours, plutot que de supposer qu'elle s'y trouve deja :
    le critere assoupli (domaine seul) ne garantit plus que la page
    authentifiee atterrisse au bon endroit."""
    page = PageFactice(f"https://{HOTE_PORTAIL}/mon-compte")

    session = SessionNavigateurTestable(page)
    assert session.est_connecte()
    assert page.appels_goto == [URL_DEPART]


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
        super().__init__()
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


def test_attendre_connexion_annulation_posee_rend_false_sans_attendre_le_delai():
    """Le bouton d'interruption de l'interface graphique pose un
    threading.Event : le sondage suivant doit rendre False immediatement,
    bien avant l'echeance du delai (jamais connectee ici)."""
    import threading

    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=None)
    annulation = threading.Event()
    annulation.set()

    resultat = session.attendre_connexion(
        delai=1000,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
        annulation=annulation,
    )

    assert resultat is False


def test_attendre_connexion_sans_annulation_posee_continue_normalement():
    """Contre-epreuve : un Event fourni mais jamais pose ne change rien au
    comportement existant."""
    import threading

    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    session = SessionAttenteTestable(page, connectee_au_nieme_appel=1)
    annulation = threading.Event()

    resultat = session.attendre_connexion(
        delai=1000,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
        annulation=annulation,
    )

    assert resultat is True


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


# --- attendre_connexion : erreur Playwright transitoire pendant la chaine
# --- de redirections OAuth, distinguee d'une fenetre reellement fermee ---
#
# "Execution context was destroyed, most likely because of a navigation" est
# le message reel constate : il survient couramment et normalement pendant
# une authentification, et ne doit jamais etre confondu avec une fermeture
# de la fenetre par l'utilisateur.


class SessionAttenteErreurTestable(SessionNavigateur):
    """Session testable dont est_connecte() leve une erreur Playwright
    generale un nombre de fois donne (simulant une navigation OAuth en
    cours), puis se presente authentifiee.

    La page factice porte l'etat de fermeture reel (is_closed()), verifie
    par attendre_connexion pour distinguer une fenetre reellement fermee
    d'une erreur transitoire.
    """

    def __init__(self, page, echecs_avant_succes=0):
        super().__init__()
        self.page = page
        self._echecs_avant_succes = echecs_avant_succes
        self._appels = 0

    def est_connecte(self):
        self._appels += 1
        if self._appels <= self._echecs_avant_succes:
            raise ErreurPlaywright(
                "Execution context was destroyed, most likely because of a navigation"
            )
        return True


def test_attendre_connexion_absorbe_une_erreur_transitoire_de_navigation():
    """Une erreur Playwright generale (contexte d'execution detruit par une
    navigation) survenant sur les premiers sondages ne doit pas mettre fin a
    l'attente : la page n'est pas fermee, le sondage continue jusqu'a ce que
    la connexion soit detectee.
    """
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    session = SessionAttenteErreurTestable(page, echecs_avant_succes=3)

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=lambda _m: None,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is True


def test_attendre_connexion_se_termine_sur_fenetre_reellement_fermee():
    """Une fenetre reellement fermee (page.is_closed() vrai) doit terminer
    l'attente en rendant False, sans attendre l'epuisement du delai."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    page.fermer()
    session = SessionAttenteErreurTestable(page, echecs_avant_succes=1000)
    horloge = HorlogeFactice(pas=1.0)

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=lambda _m: None,
        horloge=horloge,
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    # L'attente s'est arretee des le premier sondage, pas apres 1000
    # appels d'horloge simulant l'ecoulement complet du delai.
    assert session._appels == 1


def test_attendre_connexion_erreur_transitoire_ne_pollue_pas_l_affichage():
    """Une erreur transitoire de navigation est normale pendant une
    authentification : elle ne doit produire aucun message a l'utilisateur,
    seules les lignes d'etat habituelles doivent apparaitre."""
    page = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    session = SessionAttenteErreurTestable(page, echecs_avant_succes=3)
    messages = []

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is True
    for message in messages:
        assert "erreur" not in message.lower()
        assert "Execution context" not in message


# --- Sondage de toutes les pages du contexte, pas d'une seule reference
# --- figee (defaut : la page authentifiee peut vivre dans un onglet ou une
# --- popup distincte de la page capturee a l'ouverture) ---
#
# Rien n'accede au reseau ni ne dort : ContexteFactice est une simple liste
# de pages mutable en direct, comme playwright.sync_api.BrowserContext.pages.


class ContexteFactice:
    """Simule playwright.sync_api.BrowserContext : une liste de pages
    consultable et modifiable en direct (`.pages`), et un etat de fermeture
    explicite (`.is_closed()`)."""

    def __init__(self, pages):
        self.pages = list(pages)
        self._ferme = False

    def is_closed(self):
        return self._ferme

    def fermer(self):
        self._ferme = True


class PageErreurExamenFactice:
    """Page dont l'examen (lecture de l'URL) leve une erreur Playwright,
    simulant une navigation en cours au moment du sondage. Reproduit
    "Execution context was destroyed, most likely because of a navigation" :
    un etat normal pendant une chaine de redirections OAuth, qui ne doit
    jamais interrompre l'examen des autres pages du contexte."""

    def __init__(self):
        self._fermee = False

    @property
    def url(self):
        raise ErreurPlaywright(
            "Execution context was destroyed, most likely because of a navigation"
        )

    def is_closed(self):
        return self._fermee


class SessionContexteTestable(SessionNavigateur):
    """Version testable exposant un contexte factice a plusieurs pages,
    plutot qu'une seule page capturee."""

    def __init__(self, pages):
        super().__init__()
        self.contexte = ContexteFactice(pages)
        self.page = pages[0] if pages else None


def _page_authentifiee_factice():
    """Page factice de /portail/cours : authentifiee des le nom d'hote, sans
    plus exiger aucun marqueur de contenu."""
    return PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")


def test_est_connecte_examine_toutes_les_pages_et_adopte_la_page_authentifiee():
    """Un contexte a deux pages, l'une restee sur le domaine de connexion et
    l'autre authentifiee : la connexion est detectee, et la page de travail
    retenue (session.page) est la page authentifiee, pas la premiere page du
    contexte ni une reference figee a l'ouverture du navigateur."""
    page_connexion = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    page_authentifiee = _page_authentifiee_factice()

    session = SessionContexteTestable([page_connexion, page_authentifiee])

    assert session.est_connecte() is True
    assert session.page is page_authentifiee


def test_est_connecte_ignore_une_page_en_echec_d_examen_sans_bloquer_les_autres():
    """Une page qui leve une erreur de navigation pendant qu'on l'examine ne
    doit pas empecher l'examen des autres pages du contexte : la page
    authentifiee est tout de meme trouvee et adoptee."""
    page_en_navigation = PageErreurExamenFactice()
    page_authentifiee = _page_authentifiee_factice()

    session = SessionContexteTestable([page_en_navigation, page_authentifiee])

    assert session.est_connecte() is True
    assert session.page is page_authentifiee


class HorlogeAvecApparitionTardive:
    """Horloge deterministe qui, a un appel donne, fait apparaitre une
    nouvelle page dans le contexte -- reproduit le flux OAuth reel, ou la
    page authentifiee n'existe pas encore au premier sondage mais apparait
    en cours d'attente (nouvel onglet, redirection). N'attend jamais pour de
    vrai : seule l'horloge simulee avance."""

    def __init__(self, contexte, nouvelle_page, appel_apparition, pas=1.0):
        self._temps = 0.0
        self._pas = pas
        self._contexte = contexte
        self._nouvelle_page = nouvelle_page
        self._appel_apparition = appel_apparition
        self._appels = 0

    def __call__(self):
        self._appels += 1
        if self._appels == self._appel_apparition:
            self._contexte.pages.append(self._nouvelle_page)
        maintenant = self._temps
        self._temps += self._pas
        return maintenant


def test_attendre_connexion_decouvre_une_page_apparue_apres_le_debut_de_l_attente():
    """Cas exact du flux OAuth qui a cause le defaut initial : la page
    authentifiee n'existe pas encore au premier sondage, elle apparait plus
    tard dans le contexte. attendre_connexion doit la decouvrir et l'adopter,
    plutot que de continuer a sonder la seule page capturee a l'ouverture."""
    page_connexion = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    page_authentifiee = _page_authentifiee_factice()

    session = SessionContexteTestable([page_connexion])
    horloge = HorlogeAvecApparitionTardive(
        session.contexte, page_authentifiee, appel_apparition=5
    )

    resultat = session.attendre_connexion(
        delai=1000,
        imprimer=lambda _m: None,
        horloge=horloge,
        sommeil=_sommeil_factice,
    )

    assert resultat is True
    assert session.page is page_authentifiee


class HorlogeFacticeComptee:
    """Horloge deterministe qui compte ses appels, pour verifier qu'une
    attente s'est arretee tot plutot que d'avoir consomme tout le delai."""

    def __init__(self, pas=1.0):
        self._temps = 0.0
        self._pas = pas
        self.appels = 0

    def __call__(self):
        self.appels += 1
        maintenant = self._temps
        self._temps += self._pas
        return maintenant


def test_attendre_connexion_toutes_pages_fermees_rend_false():
    """Quand toutes les pages du contexte sont fermees, attendre_connexion
    se termine en rendant False sans attendre l'echeance du delai : c'est la
    seule disparition de toutes les pages qui doit y mettre fin, pas la
    fermeture d'une seule page parmi d'autres.

    Le delai est enorme (100000s) : si l'attente ne se terminait qu'a
    l'echeance (comportement de l'ancien code, qui ne verifiait la fermeture
    qu'a la faveur d'une exception jamais levee ici), l'horloge factice
    serait appelee des dizaines de milliers de fois. Un arret des les
    premiers sondages prouve que la fermeture a ete detectee directement.
    """
    page_une = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    page_deux = PageFactice(f"https://{HOTE_SITESCOURS}/portail/cours")
    page_une.fermer()
    page_deux.fermer()

    session = SessionContexteTestable([page_une, page_deux])
    horloge = HorlogeFacticeComptee(pas=1.0)

    resultat = session.attendre_connexion(
        delai=100_000,
        imprimer=lambda _m: None,
        horloge=horloge,
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    assert horloge.appels < 5


def test_attendre_connexion_ligne_d_etat_mentionne_tous_les_domaines_ouverts():
    """La ligne d'etat rend compte de toutes les pages ouvertes et de leurs
    domaines, pas du domaine d'une seule page choisie arbitrairement : c'est
    ce qui aurait revele immediatement le defaut de page abandonnee sur le
    domaine de connexion pendant qu'une autre page, sur un autre domaine,
    restait invisible du programme."""
    page_connexion = PageFactice("https://login.microsoftonline.com/common/oauth2/authorize")
    # Autre relais de connexion federee, distinct du premier : aucun des deux
    # n'est authentifie (le critere rejette tout hote de la famille
    # Microsoft), sert seulement a peupler la ligne d'etat avec un second
    # domaine.
    page_relais = PageFactice("https://login.live.com/oauth20_authorize.srf")

    session = SessionContexteTestable([page_connexion, page_relais])
    messages = []

    resultat = session.attendre_connexion(
        delai=10,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    lignes_etat = [m for m in messages if m.startswith("en attente")]
    assert lignes_etat
    assert any(
        "login.microsoftonline.com" in ligne and "login.live.com" in ligne
        for ligne in lignes_etat
    )


# --- SessionNavigateur.ouvrir/fermer : profil temporaire, jamais persistant
# --- (changement de conception du 2026-09-15 : voir docs/api-monportail.md,
# --- section "Pieges d'exploitation", pour l'incident qui l'a motive) ---
#
# Aucun test ici ne touche au reseau ni ne lance un vrai navigateur :
# sync_playwright est remplace par une doublure complete (start/chromium/
# launch_persistent_context/stop), suffisante pour observer ce que
# SessionNavigateur fait du dossier de profil sans jamais toucher a
# Playwright pour de vrai.


class ContextePersistantFactice:
    """Simule playwright.sync_api.BrowserContext rendu par
    launch_persistent_context : conserve le chemin de profil recu, pour que
    les tests verifient qu'il correspond bien a self.dossier_profil."""

    def __init__(self, user_data_dir, headless, accept_downloads, leve_bring_to_front=None):
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.accept_downloads = accept_downloads
        self.pages = []
        self._ferme = False
        # Scripts recus par add_init_script() : sert a verifier que ouvrir()
        # installe bien le prefixe de titre distinctif sur le contexte.
        self.scripts_initiaux = []
        self._leve_bring_to_front = leve_bring_to_front

    def new_page(self):
        page = PageFactice("about:blank")
        if self._leve_bring_to_front is not None:
            page.bring_to_front = _leve(self._leve_bring_to_front)
        self.pages.append(page)
        return page

    def is_closed(self):
        return self._ferme

    def close(self):
        self._ferme = True

    def add_init_script(self, script):
        """Simule BrowserContext.add_init_script()."""
        self.scripts_initiaux.append(script)


class ChromiumFactice:
    def __init__(self, leve_a_la_fermeture=None, leve_bring_to_front=None):
        self.appels_launch = []
        self._leve_a_la_fermeture = leve_a_la_fermeture
        self._leve_bring_to_front = leve_bring_to_front

    def launch_persistent_context(self, user_data_dir, headless, accept_downloads):
        contexte = ContextePersistantFactice(
            user_data_dir,
            headless,
            accept_downloads,
            leve_bring_to_front=self._leve_bring_to_front,
        )
        if self._leve_a_la_fermeture is not None:
            contexte.close = _leve(self._leve_a_la_fermeture)
        self.appels_launch.append(contexte)
        return contexte


def _leve(erreur):
    def _fonction():
        raise erreur

    return _fonction


class PlaywrightFactice:
    """Simule l'objet Playwright rendu par sync_playwright().start()."""

    def __init__(self, chromium=None, leve_a_l_arret=None):
        self.chromium = chromium or ChromiumFactice()
        self._arrete = False
        self._leve_a_l_arret = leve_a_l_arret

    def stop(self):
        if self._leve_a_l_arret is not None:
            raise self._leve_a_l_arret
        self._arrete = True


class GestionnairePlaywrightFactice:
    """Simule playwright.sync_api.sync_playwright() : .start() rend
    directement l'objet Playwright factice, comme le vrai gestionnaire."""

    def __init__(self, instance):
        self._instance = instance

    def start(self):
        return self._instance


def _installer_playwright_factice(monkeypatch, instance=None):
    """Remplace extracteur.auth.sync_playwright par une doublure, et rend
    l'instance Playwright factice utilisee (pour inspection apres coup)."""
    instance = instance or PlaywrightFactice()
    monkeypatch.setattr(
        "extracteur.auth.sync_playwright", lambda: GestionnairePlaywrightFactice(instance)
    )
    return instance


def test_ouvrir_cree_un_dossier_de_profil_distinct_a_chaque_ouverture(monkeypatch):
    """Deux ouvertures successives (deux instances, comme deux lancements
    du programme) ne doivent jamais reutiliser le meme dossier de profil :
    c'est tout le sens de l'abandon du profil persistant."""
    _installer_playwright_factice(monkeypatch)

    session_un = SessionNavigateur()
    session_un.ouvrir(sommeil=_sommeil_factice)
    session_deux = SessionNavigateur()
    session_deux.ouvrir(sommeil=_sommeil_factice)

    assert session_un.dossier_profil != session_deux.dossier_profil
    assert session_un.dossier_profil.is_dir()
    assert session_deux.dossier_profil.is_dir()

    session_un.fermer()
    session_deux.fermer()


def test_ouvrir_cree_le_profil_dans_le_dossier_temporaire_du_systeme(monkeypatch):
    """Jamais dans le depot, jamais dans un dossier synchronise OneDrive :
    le profil doit vivre sous le dossier temporaire du systeme
    (tempfile.gettempdir()), qui n'est ni l'un ni l'autre."""
    _installer_playwright_factice(monkeypatch)

    session = SessionNavigateur()
    session.ouvrir(sommeil=_sommeil_factice)

    dossier_temporaire_systeme = Path(tempfile.gettempdir()).resolve()
    assert session.dossier_profil.resolve().is_relative_to(dossier_temporaire_systeme)
    # Le contexte Playwright factice recoit bien ce meme chemin, preuve que
    # c'est ce dossier-la qui sert vraiment de profil, pas un autre.
    assert session.contexte.user_data_dir == str(session.dossier_profil)

    session.fermer()


def test_fermer_supprime_le_dossier_de_profil(monkeypatch):
    _installer_playwright_factice(monkeypatch)
    session = SessionNavigateur()
    session.ouvrir(sommeil=_sommeil_factice)
    dossier_profil = session.dossier_profil
    assert dossier_profil.is_dir()

    session.fermer()

    assert not dossier_profil.exists()
    assert session.dossier_profil is None


def test_fermer_supprime_le_profil_meme_si_la_fermeture_du_contexte_echoue(monkeypatch):
    """Le profil doit disparaitre meme quand contexte.close() leve une
    exception entre l'ouverture et la fin normale de fermer() : c'est la
    garantie centrale du changement de conception (voir la docstring de
    fermer())."""
    instance = PlaywrightFactice(
        chromium=ChromiumFactice(leve_a_la_fermeture=RuntimeError("contexte deja mort"))
    )
    _installer_playwright_factice(monkeypatch, instance)
    session = SessionNavigateur()
    session.ouvrir(sommeil=_sommeil_factice)
    dossier_profil = session.dossier_profil

    with pytest.raises(RuntimeError):
        session.fermer()

    assert not dossier_profil.exists()


def test_fermer_supprime_le_profil_meme_si_l_arret_de_playwright_echoue(monkeypatch):
    """Meme garantie que ci-dessus, pour un echec un cran plus loin dans
    fermer() (playwright.stop() plutot que contexte.close())."""
    instance = PlaywrightFactice(leve_a_l_arret=RuntimeError("playwright deja arrete"))
    _installer_playwright_factice(monkeypatch, instance)
    session = SessionNavigateur()
    session.ouvrir(sommeil=_sommeil_factice)
    dossier_profil = session.dossier_profil

    with pytest.raises(RuntimeError):
        session.fermer()

    assert not dossier_profil.exists()


def test_fermer_deux_fois_de_suite_ne_leve_rien():
    """fermer() peut etre appelee plusieurs fois sans effet de bord (aucun
    contexte ni profil a nettoyer la seconde fois) : les appelants du projet
    la placent dans des finally qui peuvent, selon le chemin emprunte,
    s'executer sur une session jamais ouverte."""
    session = SessionNavigateur()

    session.fermer()
    session.fermer()


# --- Fenetre pilotee reconnaissable : page d'identification, premier plan
# --- et prefixe de titre (tache : rendre impossible la confusion entre la
# --- fenetre pilotee et une autre fenetre de navigateur ouverte a cote) ---
#
# Incident de terrain qui motive cette section : l'utilisateur s'authentifiait
# dans une fenetre qui n'etait pas celle pilotee par le programme (capture
# montrant barre de favoris et avatar de compte Google, impossible sur le
# profil temporaire et vierge cree par ouvrir()), pendant que la fenetre
# reellement pilotee restait bloquee sur login.microsoftonline.com.


def test_ouvrir_affiche_la_page_d_identification_avant_de_naviguer_vers_monportail(
    monkeypatch,
):
    """ouvrir() affiche d'abord une page d'identification sans equivoque
    (titre distinctif, contenu explicatif), et seulement ensuite navigue vers
    monPortail -- jamais l'inverse."""
    _installer_playwright_factice(monkeypatch)
    session = SessionNavigateur()

    session.ouvrir(sommeil=_sommeil_factice)

    page = session.page
    assert page.contenu_html is not None
    assert TITRE_FENETRE_PILOTEE in page.contenu_html
    assert "C'est ICI qu'il faut se connecter" in page.contenu_html
    # L'ordre reel des appels, pas seulement leur presence : set_content()
    # (page d'identification) doit precede goto() (depart vers monPortail).
    types_appeles = [type_appel for type_appel, _valeur in page.journal]
    assert types_appeles.index("set_content") < types_appeles.index("goto")
    assert page.appels_goto == [URL_DEPART]

    session.fermer()


def test_ouvrir_amene_la_fenetre_au_premier_plan(monkeypatch):
    """La fenetre pilotee est amenee au premier plan au moment ou l'on
    affiche la page d'identification, l'invitation a se connecter."""
    _installer_playwright_factice(monkeypatch)
    session = SessionNavigateur()

    session.ouvrir(sommeil=_sommeil_factice)

    assert session.page.appels_bring_to_front >= 1
    # Demandee apres l'affichage de la page d'identification, jamais avant :
    # rien a mettre au premier plan avant que le contenu distinctif existe.
    types_appeles = [type_appel for type_appel, _valeur in session.page.journal]
    assert types_appeles.index("set_content") < types_appeles.index("bring_to_front")

    session.fermer()


def test_ouvrir_n_echoue_pas_si_bring_to_front_leve_une_erreur_playwright(monkeypatch):
    """bring_to_front() n'est pas garanti par Playwright : une erreur ne doit
    jamais empecher la suite de ouvrir() (affichage puis navigation vers
    monPortail)."""
    instance = PlaywrightFactice(
        chromium=ChromiumFactice(
            leve_bring_to_front=ErreurPlaywright("pas de gestionnaire de fenetres")
        )
    )
    _installer_playwright_factice(monkeypatch, instance)
    session = SessionNavigateur()

    session.ouvrir(sommeil=_sommeil_factice)  # ne doit pas planter

    assert session.page.appels_goto == [URL_DEPART]

    session.fermer()


def test_ouvrir_installe_le_prefixe_de_titre_sur_le_contexte(monkeypatch):
    """Le script qui prefixe le titre de chaque page (voir
    SCRIPT_PREFIXE_TITRE) est installe sur le contexte via add_init_script(),
    pour survivre a la navigation vers la page de connexion et monPortail."""
    _installer_playwright_factice(monkeypatch)
    session = SessionNavigateur()

    session.ouvrir(sommeil=_sommeil_factice)

    assert len(session.contexte.scripts_initiaux) == 1
    assert PREFIXE_TITRE_FENETRE_PILOTEE in session.contexte.scripts_initiaux[0]

    session.fermer()


def test_attendre_connexion_ligne_d_etat_inclut_le_titre_de_chaque_page():
    """La ligne d'etat inclut le titre de chaque page, pas seulement son
    hote : deux titres distincts rendent un ecart visible d'un coup d'oeil,
    la ou deux noms de domaine ne disent rien.

    Les deux pages restent volontairement sur des hotes de connexion
    Microsoft (jamais authentifies) : une page sur ulaval.ca serait adoptee
    des le premier sondage (voir est_page_authentifiee), avant meme qu'une
    ligne d'etat n'ait eu la chance de s'afficher."""
    page_connexion = PageFactice(
        "https://login.microsoftonline.com/common/oauth2/authorize",
        titre="Sign in to your account",
    )
    page_selecteur_compte = PageFactice(
        "https://login.live.com/oauth20_authorize.srf",
        titre="Choisissez un compte",
    )

    session = SessionContexteTestable([page_connexion, page_selecteur_compte])
    messages = []

    session.attendre_connexion(
        delai=10,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    lignes_etat = [m for m in messages if m.startswith("en attente")]
    assert lignes_etat
    ligne = lignes_etat[0]
    assert "login.microsoftonline.com [Sign in to your account]" in ligne
    assert "login.live.com [Choisissez un compte]" in ligne


def test_attendre_connexion_titre_inaccessible_n_interrompt_pas_les_autres_pages():
    """Une page dont le titre est illisible (navigation en cours) ne doit ni
    disparaitre de la ligne d'etat, ni empecher l'affichage du titre des
    autres pages : seul son propre titre est omis."""
    page_en_navigation = PageFactice(
        "https://login.microsoftonline.com/common/oauth2/authorize",
        titre_leve=ErreurPlaywright(
            "Execution context was destroyed, most likely because of a navigation"
        ),
    )
    page_avec_titre = PageFactice(
        "https://login.live.com/oauth20_authorize.srf",
        titre="Choisissez un compte",
    )

    session = SessionContexteTestable([page_en_navigation, page_avec_titre])
    messages = []

    resultat = session.attendre_connexion(
        delai=10,
        imprimer=messages.append,
        horloge=HorlogeFactice(pas=1.0),
        sommeil=_sommeil_factice,
    )

    assert resultat is False
    lignes_etat = [m for m in messages if m.startswith("en attente")]
    assert lignes_etat
    ligne = lignes_etat[0]
    # Le titre illisible n'empeche ni la mention du nom d'hote de sa page...
    assert "login.microsoftonline.com" in ligne
    assert "login.microsoftonline.com [" not in ligne
    # ...ni l'affichage du titre de l'autre page.
    assert "login.live.com [Choisissez un compte]" in ligne
