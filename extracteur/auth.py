"""Ouverture du navigateur et attente de la connexion manuelle.

L'utilisateur saisit lui-meme son mot de passe et son MFA. Le programme ne lit
jamais ses identifiants : il attend seulement de voir une page authentifiee.

Sur le domaine des sites de cours, les cookies suffisent : aucun jeton porteur
n'est necessaire pour telecharger les fichiers. Le navigateur ne sert donc
qu'a l'authentification et a la navigation ; le telechargement passe par
urllib (bibliotheque standard) avec les cookies du navigateur, pour pouvoir
lire le flux par blocs. L'API synchrone de Playwright ne permet pas de lire
un corps de reponse par morceaux : contexte.request.get(...).body() rapatrie
tout en memoire, ce qui violerait l'invariant de stockage.ecrire_flux (jamais
un fichier entier en memoire).
"""

import http.cookiejar
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import sync_playwright

URL_DEPART = "https://sitescours.monportail.ulaval.ca/portail/cours"
HOTE_SITESCOURS = "sitescours.monportail.ulaval.ca"
TAILLE_MORCEAU = 65536


class Reponse:
    """Adaptateur vers l'interface attendue par telechargement.telecharger.

    Enveloppe soit un flux de succes (urlopen), soit le corps d'une erreur
    HTTP (urllib.error.HTTPError, qui expose aussi .read()) : dans les deux
    cas, telechargement.py doit voir le vrai code de statut pour appliquer sa
    politique de reessai (401/403/404/5xx).
    """

    def __init__(self, statut: int, flux):
        self.statut = statut
        self._flux = flux

    def morceaux(self):
        """Lit le flux par blocs : jamais le corps entier charge en memoire."""
        try:
            while True:
                morceau = self._flux.read(TAILLE_MORCEAU)
                if not morceau:
                    break
                yield morceau
        finally:
            self._flux.close()


class SessionNavigateur:
    def __init__(self, dossier_profil: Path, sans_fenetre: bool = False):
        self.dossier_profil = Path(dossier_profil)
        self.sans_fenetre = sans_fenetre
        self._playwright = None
        self.contexte = None
        self.page = None

    def ouvrir(self) -> None:
        self.dossier_profil.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        # Profil persistant : les lancements suivants ne redemandent pas la
        # connexion tant que la session vit.
        self.contexte = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.dossier_profil),
            headless=self.sans_fenetre,
            accept_downloads=True,
        )
        self.page = self.contexte.pages[0] if self.contexte.pages else self.contexte.new_page()
        self.page.goto(URL_DEPART, wait_until="domcontentloaded")

    def _est_page_authentifiee_monportail(self) -> bool:
        """Marqueur de contenu propre a une page authentifiee de monPortail.

        Detecte soit une page de site de cours (lien /ena/site/ ou texte
        "Liste des cours"), soit la page /portail/cours apres connexion
        (texte "Cours suivis" ou plusieurs liens vers le menu authentifie).
        """
        # Signaux d'une page de site de cours authentifiee
        if self.page.locator("a[href*='/ena/site/']").count() > 0:
            return True
        if self.page.get_by_text("Liste des cours").count() > 0:
            return True

        # Signaux de la page /portail/cours apres connexion
        if self.page.get_by_text("Cours suivis").count() > 0:
            return True

        # Menu authentifie apparait avec plusieurs liens vers /portail
        if self.page.locator("a[href*='monportail.ulaval.ca/portail']").count() >= 5:
            return True

        return False

    def est_connecte(self) -> bool:
        if self.page is None:
            return False
        # Un simple test par sous-chaine sur l'URL est insuffisant : l'URL
        # d'autorisation Microsoft place le redirect_uri en clair dans ses
        # parametres (l'encodage pour cent ne touche que ':' et '/'), donc
        # "sitescours.monportail.ulaval.ca" y apparait avant toute connexion.
        # Une page d'erreur sur le bon domaine n'est pas davantage une preuve
        # de connexion. On verifie donc le nom d'hote exact, puis un marqueur
        # tire du contenu reel de la page.
        if urlparse(self.page.url).hostname != HOTE_SITESCOURS:
            return False
        return self._est_page_authentifiee_monportail()

    def attendre_connexion(self, delai: int = 300) -> bool:
        """Attend que l'utilisateur ait termine sa connexion, MFA compris."""
        limite = time.time() + delai
        while time.time() < limite:
            try:
                if self.est_connecte():
                    return True
            except ErreurPlaywright:
                # Fenetre ou contexte ferme par l'utilisateur : fin d'attente
                # propre, pas une exception qui empeche l'appel a fermer().
                return False
            time.sleep(2)
        return False

    def _cookiejar(self) -> http.cookiejar.CookieJar:
        """Convertit les cookies du navigateur en cookiejar pour urllib."""
        cookiejar = http.cookiejar.CookieJar()
        for cookie in self.contexte.cookies():
            expiration = cookie.get("expires")
            cookiejar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=cookie["name"],
                    value=cookie["value"],
                    port=None,
                    port_specified=False,
                    domain=cookie["domain"],
                    domain_specified=True,
                    domain_initial_dot=cookie["domain"].startswith("."),
                    path=cookie.get("path", "/"),
                    path_specified=True,
                    secure=cookie.get("secure", False),
                    expires=expiration if expiration and expiration > 0 else None,
                    discard=False,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": cookie.get("httpOnly", False)},
                )
            )
        return cookiejar

    def transport(self, url: str) -> Reponse:
        """GET HTTP direct (urllib) partageant les cookies du navigateur."""
        if not url.startswith("http"):
            # Toutes les ressources du site sont servies via des chemins
            # absolus ; on le rend explicite plutot que de le supposer.
            chemin = url if url.startswith("/") else f"/{url}"
            url = f"https://{HOTE_SITESCOURS}{chemin}"

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookiejar())
        )
        try:
            flux = opener.open(url)
            return Reponse(flux.status, flux)
        except urllib.error.HTTPError as erreur:
            # urllib leve une exception sur 4xx/5xx au lieu de rendre une
            # reponse : on la transforme pour que telechargement.py voie le
            # vrai statut et applique sa politique de reessai / session
            # expiree.
            return Reponse(erreur.code, erreur)

    def fermer(self) -> None:
        if self.contexte is not None:
            self.contexte.close()
        if self._playwright is not None:
            self._playwright.stop()
