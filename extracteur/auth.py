"""Ouverture du navigateur et attente de la connexion manuelle.

L'utilisateur saisit lui-meme son mot de passe et son MFA. Le programme ne lit
jamais ses identifiants : il attend seulement de voir une page authentifiee.

Sur le domaine des sites de cours, les cookies suffisent : aucun jeton porteur
n'est necessaire pour telecharger les fichiers.
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL_DEPART = "https://sitescours.monportail.ulaval.ca/ena/site/accueil"
HOTE_SITESCOURS = "sitescours.monportail.ulaval.ca"


class Reponse:
    """Adaptateur vers l'interface attendue par telechargement.telecharger."""

    def __init__(self, reponse_playwright):
        self.statut = reponse_playwright.status
        self._reponse = reponse_playwright

    def morceaux(self):
        yield self._reponse.body()


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

    def est_connecte(self) -> bool:
        if self.page is None:
            return False
        return HOTE_SITESCOURS in self.page.url

    def attendre_connexion(self, delai: int = 300) -> bool:
        """Attend que l'utilisateur ait termine sa connexion, MFA compris."""
        limite = time.time() + delai
        while time.time() < limite:
            if self.est_connecte():
                return True
            time.sleep(2)
        return False

    def transport(self, url: str) -> Reponse:
        """GET partageant les cookies du navigateur."""
        if not url.startswith("http"):
            url = f"https://{HOTE_SITESCOURS}{url}"
        return Reponse(self.contexte.request.get(url))

    def fermer(self) -> None:
        if self.contexte is not None:
            self.contexte.close()
        if self._playwright is not None:
            self._playwright.stop()
