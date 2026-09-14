"""Navigation dans les anciens sites de cours monPortail.

Principe etabli en phase 0 : les libelles de menu varient d'un site a l'autre
(« Feuille de route » ici, « Contenu et activites » la), mais les URL canoniques
restent valides partout. On navigue donc par URL, et on lit le menu seulement
pour attraper ce qui sort du schema.
"""

import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright

from extracteur.extraction import (
    cours_depuis_html,
    depots_depuis_html,
    est_commande_adf,
    fichiers_depuis_html,
    modules_depuis_html,
    resultats_depuis_html,
    sections_du_menu,
    session_depuis_libelle,
)
from extracteur.modele import Cours, Evaluation, Session

BASE = "https://sitescours.monportail.ulaval.ca"
ONGLET_CONTENU = "text=Contenu du module"

# Marqueur textuel confirmant qu'une page est bien un plan de cours, et non
# une redirection tombee sur l'accueil. Compare sans accents ni casse : voir
# _contient_marqueur_plan_de_cours.
MARQUEUR_PLAN_DE_COURS = "plan de cours"


class URL:
    """Les URL canoniques relevees en phase 0."""

    @staticmethod
    def accueil(id_site: str) -> str:
        return f"/ena/site/accueil?idSite={id_site}"

    @staticmethod
    def modules(id_site: str) -> str:
        return f"/ena/site/modules?idSite={id_site}"

    @staticmethod
    def module(id_site: str, id_module: str) -> str:
        return f"/ena/site/module?idSite={id_site}&idModule={id_module}&editionModule=false"

    @staticmethod
    def evaluations(id_site: str) -> str:
        return f"/ena/site/evaluations?idSite={id_site}"

    @staticmethod
    def boite_depot(id_site: str, id_evaluation: str) -> str:
        return (
            f"/ena/site/evaluation?idSite={id_site}"
            f"&idEvaluation={id_evaluation}&onglet=boiteDepots"
        )

    @staticmethod
    def resultats(id_site: str) -> str:
        return f"/ena/site/resultats?idSite={id_site}"

    @staticmethod
    def redirection(id_site: str, section: str) -> str:
        """Routeur a URL stables de l'ENA, verifie en phase 0 sur liste_modules."""
        return f"/lieninterne/redirection/{id_site}/{section}"

    @staticmethod
    def cours() -> str:
        """Source d'enumeration des sessions et des cours. Aucun identifiant
        de site d'amorcage n'est necessaire : l'URL est stable."""
        return "/portail/cours"


# Noms de section tentes pour le plan de cours. La phase 0 n'a confirme que
# `liste_modules` ; les autres sont des candidats a valider au premier passage.
SECTIONS_PLAN_DE_COURS = ("plan_de_cours", "plancours", "plan_cours")


class Ena:
    # Le selecteur de sessions de /portail/cours n'est pas un <select> natif :
    # c'est un div ARIA dont les options ne sont rendues qu'apres un clic.
    # Exception assumee aux regles de navigation : cette ouverture ne modifie
    # rien cote serveur, contrairement aux liens de modification qu'on evite.
    SELECTEUR_SESSIONS = 'div[role="listbox"].mpo-deroulant-bouton'
    SELECTEUR_OPTIONS_SESSIONS = f'{SELECTEUR_SESSIONS} a'

    def __init__(self, session):
        self.session = session

    def _visiter(self, chemin: str) -> str:
        url = chemin if chemin.startswith("http") else BASE + chemin
        self.session.page.goto(url, wait_until="networkidle")
        return self.session.page.content()

    def _ouvrir_selecteur_sessions(self) -> None:
        """Clique le selecteur pour faire apparaitre ses options dans le DOM.

        Un echec (selecteur absent, pas encore charge) n'est pas fatal : les
        appelants continuent avec ce qui est disponible plutot que d'echouer.
        """
        try:
            self.session.page.click(self.SELECTEUR_SESSIONS, timeout=5000)
        except (ErreurDelaiPlaywright, ErreurPlaywright):
            pass

    def _options_sessions(self):
        """Localisateur des options du selecteur, mecanisme partage par
        sessions_disponibles et sites_de_session.

        Le selecteur de /portail/cours est un div ARIA listbox dont les
        options ne sont pas necessairement des liens accessibles : un <a>
        sans attribut href n'a aucun role accessible "link". Lire les options
        par un selecteur CSS puis les cliquer par role ARIA ferait diverger
        les deux mecanismes, avec un risque de clic silencieusement
        inoperant si le DOM reel ne colle pas au motif suppose. On repere
        donc les options par le meme selecteur CSS dans les deux cas.
        """
        return self.session.page.locator(self.SELECTEUR_OPTIONS_SESSIONS)

    def sessions_disponibles(self) -> list[Session]:
        """Liste les sessions offertes par le selecteur de /portail/cours.

        Sans parametre : aucun identifiant de site d'amorcage n'est requis,
        la page est a une URL stable.
        """
        self._visiter(URL.cours())
        self._ouvrir_selecteur_sessions()

        libelles = self._options_sessions().all_text_contents()
        return [session_depuis_libelle(libelle.strip()) for libelle in libelles]

    def sites_de_session(self, session: Session) -> list[Cours]:
        """Selectionne une session puis rend ses cours.

        Le clic sur l'option recharge la liste de facon asynchrone : on
        attend avant de lire le DOM. Une session sans cours (par exemple la
        session courante de l'utilisateur) est normale : elle rend une liste
        vide, pas une erreur.
        """
        self._visiter(URL.cours())
        self._ouvrir_selecteur_sessions()

        try:
            motif_exact = re.compile(rf"^{re.escape(session.libelle)}$")
            lien = self._options_sessions().filter(has_text=motif_exact).first
            lien.click(timeout=5000)
            self.session.page.wait_for_timeout(1500)
        except (ErreurDelaiPlaywright, ErreurPlaywright):
            pass

        return cours_depuis_html(self.session.page.content(), session)

    def modules(self, cours) -> list:
        html = self._visiter(URL.modules(cours.id_site))
        return modules_depuis_html(html, cours.id_site)

    def fichiers_du_module(self, module) -> list:
        self._visiter(URL.module(module.id_site, module.id_module))

        # L'onglet « Contenu du module » est un lien ADF : les documents ne
        # sont pas dans le DOM avant le clic. Son absence n'est pas une erreur.
        try:
            self.session.page.click(ONGLET_CONTENU, timeout=5000)
            self.session.page.wait_for_timeout(1500)
        except (ErreurDelaiPlaywright, ErreurPlaywright):
            pass

        return fichiers_depuis_html(self.session.page.content())

    def evaluations(self, cours) -> list:
        html = self._visiter(URL.evaluations(cours.id_site))
        return _evaluations_depuis_html(html, cours.id_site)

    def fichiers_de_depot(self, evaluation) -> list:
        """Documents remis dans la boite de depot d'une evaluation.

        Utilise depots_depuis_html, et non fichiers_depuis_html : seule cette
        fonction porte "Depose par" et la date de remise, la seule trace de
        qui a remis quoi sur un travail d'equipe. Elle ne suit par ailleurs
        que les liens de la colonne "Nom du document" du tableau, jamais la
        case a cocher ni le bouton Supprimer voisins.
        """
        html = self._visiter(URL.boite_depot(evaluation.id_site, evaluation.id_evaluation))
        return depots_depuis_html(html)

    def resultats(self, cours) -> list:
        html = self._visiter(URL.resultats(cours.id_site))
        return resultats_depuis_html(html)

    def parcourir_menu(self, cours, action) -> int:
        """Repli pour les sites sans modules ni evaluations.

        Les entrees de menu de l'ENA sont des liens ADF `href="#"` : elles ne
        sont pas atteignables par URL, il faut cliquer. `action(libelle, html)`
        est appele pour chaque section ouverte ; la navigation ne sait rien du
        disque.

        Toute commande ADF `cmd*` est ecartee : `cmdObtenirPlanCours` publie une
        nouvelle version du plan de cours au lieu de le telecharger.
        """
        html = self._visiter(URL.accueil(cours.id_site))
        visitees = 0

        for libelle in sections_du_menu(html):
            try:
                cible = self.session.page.get_by_role("link", name=libelle, exact=True).first
                if est_commande_adf(cible.get_attribute("id")):
                    continue
                cible.click(timeout=5000)
                self.session.page.wait_for_timeout(1500)
            except (ErreurDelaiPlaywright, ErreurPlaywright):
                continue

            action(libelle, self.session.page.content())
            visitees += 1

        return visitees

    def capturer_pdf(self, chemin: str, destination: Path) -> None:
        self._visiter(chemin)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.session.page.pdf(path=str(destination), format="A4", print_background=True)

    def capturer_plan_de_cours(self, cours, destination: Path) -> "str | bool":
        """Imprime le plan de cours en PDF. Retourne le nom de la section qui a
        fonctionne, ou False s'il n'y en a pas.

        Le menu contient un lien a icone PDF qui ressemble a un telechargement :
        c'est `cmdObtenirPlanCours`, une commande ADF qui PUBLIE une nouvelle
        version du plan. On ne la touche jamais. On imprime la page a la place.

        Seul `liste_modules` a ete confirme en phase 0 ; les autres noms de
        section sont des candidats non verifies. Une redirection invalide peut
        retomber, sans erreur HTTP, sur l'accueil du site : le simple fait que
        l'URL ne contienne pas `page_erreur` ne prouve donc rien. Trois
        conditions doivent etre reunies pour accepter une page :
        - l'URL finale est bien sous /ena/site/ ;
        - elle ne correspond pas a l'accueil du site (la redirection a menee
          reellement ailleurs) ;
        - le contenu porte le marqueur textuel du plan de cours.
        """
        chemin_accueil = urlsplit(URL.accueil(cours.id_site)).path

        for section in SECTIONS_PLAN_DE_COURS:
            html = self._visiter(URL.redirection(cours.id_site, section))
            chemin = urlsplit(self.session.page.url).path

            if not chemin.startswith("/ena/site/"):
                continue
            if chemin == chemin_accueil:
                continue
            if not _contient_marqueur_plan_de_cours(html):
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            self.session.page.pdf(path=str(destination), format="A4", print_background=True)
            return section

        return False


def _sans_accents(texte: str) -> str:
    """Retire les accents pour une comparaison de texte fiable."""
    forme = unicodedata.normalize("NFKD", texte)
    return "".join(caractere for caractere in forme if not unicodedata.combining(caractere))


def _contient_marqueur_plan_de_cours(html: str) -> bool:
    """Vrai si la page porte reellement un plan de cours, pas juste l'accueil."""
    from bs4 import BeautifulSoup

    texte = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    return MARQUEUR_PLAN_DE_COURS in _sans_accents(texte).lower()


def _evaluations_depuis_html(html: str, id_site: str) -> list[Evaluation]:
    from urllib.parse import parse_qs, urlsplit

    from bs4 import BeautifulSoup

    trouvees: dict[str, Evaluation] = {}
    for lien in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = lien["href"]
        if "idEvaluation=" not in href:
            continue

        id_evaluation = parse_qs(urlsplit(href).query).get("idEvaluation", [""])[0]
        if not id_evaluation:
            continue

        titre = lien.get_text(strip=True)
        existante = trouvees.get(id_evaluation)
        if existante is None or (titre and not existante.titre):
            trouvees[id_evaluation] = Evaluation(
                id_site=id_site, id_evaluation=id_evaluation, titre=titre
            )

    return list(trouvees.values())
