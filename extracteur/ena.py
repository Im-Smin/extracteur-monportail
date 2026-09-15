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

from extracteur.auth import est_page_authentifiee
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
from extracteur.telechargement import SessionExpiree

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

# Forme stable d'un libelle de session du selecteur de /portail/cours : un
# nom de saison suivi d'une annee sur quatre chiffres ("Hiver 2026",
# "Été 2025", "Automne 2022", ...). Releve en session reelle sur les douze
# options du selecteur. Cette forme ne peut pas entrer en collision avec les
# libelles du menu global du portail (voir LIBELLES_HORS_SITE dans
# extraction.py), qui ne ressemblent a rien de tel.
#
# Les variantes de casse et d'accent d'« Été » sont ecrites explicitement
# ([ée]t[ée]) plutot que confiees a re.IGNORECASE, qui ne garantit pas le
# repliement de casse d'un caractere accentue cote moteur de recherche du
# navigateur.
MOTIF_LIBELLE_SESSION = re.compile(r"^(?:hiver|automne|[ée]t[ée])\s+\d{4}$", re.IGNORECASE)

# Selecteurs candidats explores par --diagnostic : le but est de voir, sur
# des faits, ce que chacun trouve reellement une fois le panneau ouvert,
# plutot que de deviner une nouvelle fois la structure du DOM.
CANDIDATS_DIAGNOSTIC_SESSIONS = ("[role=option]", ".mpo-deroulant-element", "li", "a")


class SelecteurSessionsIllisible(Exception):
    """Le panneau du selecteur de sessions s'est ouvert, mais aucune option
    de la forme <saison> <annee> n'y a ete trouvee.

    Une liste de sessions vide est le pire mode de defaillance du projet :
    l'outil n'archive plus rien du tout, en silence, en laissant croire a une
    enumeration reussie. On leve donc bruyamment plutot que de rendre [].
    Lancer `python -m extracteur --diagnostic` pour un etat des lieux du DOM
    reel avant de deviner une nouvelle correction.
    """


class Ena:
    # Le selecteur de sessions de /portail/cours n'est pas un <select> natif :
    # c'est un menu deroulant AngularJS. Le clic sur ce bouton ouvre bien un
    # panneau (confirme en session reelle), mais ce panneau n'est PAS un
    # descendant du bouton : c'est un conteneur frere, ailleurs dans la page.
    # Chercher les options sous ce selecteur (ancienne approche) ne trouve
    # donc jamais rien ; voir MOTIF_LIBELLE_SESSION et _options_sessions.
    # Exception assumee aux regles de navigation : cette ouverture ne modifie
    # rien cote serveur, contrairement aux liens de modification qu'on evite.
    SELECTEUR_SESSIONS = 'div[role="listbox"].mpo-deroulant-bouton'

    # Les douze options sont des <a> dans un conteneur frere ; le bouton lui-
    # meme ne porte qu'un <span> pour sa valeur courante (jamais un <a>,
    # constate en session reelle). On exclut neanmoins explicitement tout <a>
    # qui serait un jour ajoute a l'interieur du bouton : un futur changement
    # de markup y ferait apparaitre la valeur courante en double, treize
    # sessions au lieu de douze.
    SELECTEUR_OPTIONS_SESSIONS = f'a:not({SELECTEUR_SESSIONS} a)'

    # Classe portee par chaque option, constatee sur le DOM reel d'un panneau
    # ouvert : <a class="mpo-deroulant-elem ng-binding premier" ...>. Marqueur
    # canonique de ce composant, nettement plus stable qu'un motif de
    # libelle -- et qui ne peut pas capter par erreur un lien du menu global
    # du portail. Repere en premier ; MOTIF_LIBELLE_SESSION ne sert plus que
    # de repli si cette classe venait a changer.
    CLASSE_OPTION_SESSION = "mpo-deroulant-elem"
    SELECTEUR_OPTIONS_SESSIONS_CLASSE = f'a.{CLASSE_OPTION_SESSION}:not({SELECTEUR_SESSIONS} a)'

    def __init__(self, session):
        self.session = session

    def _visiter(self, chemin: str) -> str:
        url = chemin if chemin.startswith("http") else BASE + chemin
        self.session.page.goto(url, wait_until="networkidle")
        return self.session.page.content()

    def _assurer_authentifie(self) -> None:
        """Leve SessionExpiree si la page n'est plus authentifiee.

        Si la session Microsoft expire pendant la navigation, la plateforme
        sert une page de connexion : sans ce garde-fou, les fonctions
        d'extraction n'y trouvent rien et rendent silencieusement des listes
        vides, au lieu de mettre la file en pause. Reutilise la meme
        detection que SessionNavigateur.est_connecte, seul point du projet
        qui decide de ce qu'est une page authentifiee.
        """
        if not est_page_authentifiee(self.session.page):
            raise SessionExpiree(self.session.page.url)

    def _ouvrir_selecteur_sessions(self) -> bool:
        """Clique le selecteur pour faire apparaitre ses options dans le DOM.

        Rend Vrai si le clic a reussi, Faux sinon. Un echec (selecteur absent,
        page pas encore chargee) n'est pas fatal en soi : c'est
        sessions_disponibles qui decide, a partir de ce retour, si une liste
        d'options vide est normale (le panneau n'a jamais pu s'ouvrir) ou
        doit lever SelecteurSessionsIllisible (le panneau s'est ouvert, mais
        son contenu ne colle a aucune forme connue).
        """
        try:
            self.session.page.click(self.SELECTEUR_SESSIONS, timeout=5000)
            return True
        except (ErreurDelaiPlaywright, ErreurPlaywright):
            return False

    def _options_sessions(self):
        """Localisateur des options du selecteur, mecanisme partage par
        sessions_disponibles et sites_de_session.

        Le panneau ouvert par le clic sur SELECTEUR_SESSIONS n'est pas un
        descendant du bouton : c'est un conteneur frere, rendu ailleurs dans
        la page (menu deroulant AngularJS). Chercher un descendant du bouton
        ne trouve donc jamais rien ; on repere plutot les options sur toute
        la page.

        Mecanisme repere en premier : la classe `mpo-deroulant-elem`,
        constatee sur le DOM reel d'un panneau ouvert. Elle designe
        canoniquement une option de ce composant -- nettement plus stable
        qu'un motif de libelle, et elle ne peut pas capter par erreur un lien
        du menu global du portail. Si elle ne trouve rien (marquage change),
        _options_sessions_par_forme() prend le relai. Ce meme mecanisme sert
        a lire les options et a les cliquer : aucune divergence entre lecture
        et clic.

        SELECTEUR_OPTIONS_SESSIONS_CLASSE exclut aussi explicitement tout <a>
        interieur au bouton lui-meme : sa valeur courante partage la meme
        forme de libelle que les options (ex. "Automne 2025"), et un <a> qui
        y apparaitrait un jour produirait un doublon.
        """
        par_classe = self.session.page.locator(self.SELECTEUR_OPTIONS_SESSIONS_CLASSE)
        if par_classe.count() > 0:
            return par_classe
        return self._options_sessions_par_forme()

    def _options_sessions_par_forme(self):
        """Repli de _options_sessions() si la classe mpo-deroulant-elem a
        disparu ou change : options reperees par la forme stable de leur
        libelle (MOTIF_LIBELLE_SESSION), un nom de saison suivi d'une annee
        sur quatre chiffres.

        Le texte brut de chaque candidat est lu puis nettoye (espaces de tete
        et de fin retires) cote Python avant d'etre compare a
        MOTIF_LIBELLE_SESSION -- plus sur que de confier cette comparaison,
        ancree, telle quelle a une expression reguliere evaluee par le
        navigateur sur un texte non nettoye, qui echouerait alors purement et
        simplement. Le motif finalement transmis au navigateur pour filtrer
        le Locator n'est lui-meme jamais ancre : construit a partir des
        libelles deja valides cote Python, une simple recherche de
        sous-chaine tolere donc naturellement les espaces autour du texte.
        """
        candidats = self.session.page.locator(self.SELECTEUR_OPTIONS_SESSIONS)
        textes_valides = {
            texte
            for texte in candidats.all_text_contents()
            if MOTIF_LIBELLE_SESSION.match(texte.strip())
        }
        if not textes_valides:
            return candidats.filter(has_text=MOTIF_LIBELLE_SESSION)

        motif_valides = re.compile("|".join(re.escape(texte) for texte in textes_valides))
        return candidats.filter(has_text=motif_valides)

    def sessions_disponibles(self) -> list[Session]:
        """Liste les sessions offertes par le selecteur de /portail/cours.

        Sans parametre : aucun identifiant de site d'amorcage n'est requis,
        la page est a une URL stable.

        Si le panneau s'ouvre (le clic reussit) mais qu'aucune option ne
        correspond a la forme attendue, c'est que le mecanisme de lecture ne
        colle plus a la structure reelle : on leve SelecteurSessionsIllisible
        plutot que de rendre une liste vide, qui ferait croire a une
        enumeration reussie alors que l'outil n'archiverait plus rien.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
        ouvert = self._ouvrir_selecteur_sessions()

        libelles = self._options_sessions().all_text_contents()
        if ouvert and not libelles:
            raise SelecteurSessionsIllisible(
                "le panneau du selecteur de sessions s'est ouvert, mais "
                "aucune option de la forme <saison> <annee> n'y a ete "
                "trouvee. Lancez `python -m extracteur --diagnostic` pour "
                "un etat des lieux du DOM."
            )
        return [session_depuis_libelle(libelle.strip()) for libelle in libelles]

    def diagnostiquer_sessions(self) -> dict:
        """Etat des lieux du DOM du selecteur de sessions, sans rien
        telecharger ni rien ecrire.

        Reservee a --diagnostic : quand sessions_disponibles() ne trouve
        rien (ou leve SelecteurSessionsIllisible), ceci donne, pour une serie
        de selecteurs candidats, le nombre d'elements trouves et leurs
        premiers textes une fois le panneau ouvert -- de quoi trancher sur
        des faits plutot que de deviner une nouvelle fois la structure reelle.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
        self._ouvrir_selecteur_sessions()

        candidats = []
        for selecteur in CANDIDATS_DIAGNOSTIC_SESSIONS:
            textes = self.session.page.locator(selecteur).all_text_contents()
            candidats.append((selecteur, len(textes), textes[:5]))

        textes_forme = self._options_sessions().all_text_contents()
        candidats.append(
            ("forme du libelle (saison + annee)", len(textes_forme), textes_forme[:12])
        )

        liens_id_site = self.session.page.locator("a[href*='idSite=']").count()

        return {"candidats": candidats, "liens_id_site": liens_id_site}

    def sites_de_session(self, session: Session) -> list[Cours]:
        """Selectionne une session puis rend ses cours.

        Le clic sur l'option recharge la liste de facon asynchrone : on
        attend avant de lire le DOM. Une session sans cours (par exemple la
        session courante de l'utilisateur) est normale : elle rend une liste
        vide, pas une erreur.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
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
        self._assurer_authentifie()
        return modules_depuis_html(html, cours.id_site)

    def fichiers_du_module(self, module) -> list:
        self._visiter(URL.module(module.id_site, module.id_module))
        self._assurer_authentifie()

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
        self._assurer_authentifie()
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
        self._assurer_authentifie()
        return depots_depuis_html(html)

    def resultats(self, cours) -> list:
        html = self._visiter(URL.resultats(cours.id_site))
        self._assurer_authentifie()
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
        self._assurer_authentifie()
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
        self._assurer_authentifie()
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
