"""Navigation dans les anciens sites de cours monPortail.

Principe etabli en phase 0 : les libelles de menu varient d'un site a l'autre
(« Feuille de route » ici, « Contenu et activites » la), mais les URL canoniques
restent valides partout. On navigue donc par URL, et on lit le menu seulement
pour attraper ce qui sort du schema.
"""

import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import TimeoutError as ErreurDelaiPlaywright

from extracteur.auth import est_page_authentifiee
from extracteur.extraction import (
    ids_cours_suivis_depuis_reponse,
    cours_depuis_html,
    depots_depuis_html,
    est_commande_adf,
    fichiers_depuis_html,
    modules_depuis_html,
    onglets_depuis_html,
    onglets_selectionnes_depuis_html,
    resultats_depuis_html,
    ressources_ignorees_depuis_html,
    sections_du_menu,
    session_depuis_libelle,
)
from extracteur.modele import Cours, Evaluation, PageDeModule, Session
from extracteur.telechargement import SessionExpiree

BASE = "https://sitescours.monportail.ulaval.ca"

# Portail principal (tableau de bord), distinct du sous-domaine des sites de
# cours : depuis la refonte constatee le 25 septembre 2026, c'est la que se
# fait l'enumeration des sessions et des cours (voir URL.cours()).
BASE_PORTAIL = "https://monportail.ulaval.ca"

# Marqueur textuel confirmant qu'une page est bien un plan de cours, et non
# une redirection tombee sur l'accueil. Compare sans accents ni casse : voir
# _contient_marqueur_plan_de_cours.
MARQUEUR_PLAN_DE_COURS = "plan de cours"

# Borne dure de securite sur le nombre de pages DISTINCTES retenues par
# Ena.pages_du_module. L'algorithme (parcours en largeur par idPage, indexe
# par la feuille reellement servie) termine de lui-meme des que l'ensemble
# des feuilles vues couvre toute la hierarchie reelle -- fini par
# construction sur un module reel (MNO-5000 : 3 onglets de niveau 1 x
# jusqu'a 6 de niveau 2, soit 18 feuilles). Une structure qui ne se
# stabiliserait jamais (bogue de ce parcours, ou DOM totalement inattendu)
# ne doit pas pour autant bloquer indefiniment tout l'archivage du cours sur
# un seul module : voir TropDePagesDansUnModule.
LIMITE_PAGES_MODULE = 60


class TropDePagesDansUnModule(Exception):
    """Le parcours des onglets d'un module (Ena.pages_du_module) a retenu
    plus de LIMITE_PAGES_MODULE pages distinctes sans jamais se stabiliser.

    En usage normal, le parcours termine de lui-meme : l'ensemble des pages
    deja vues est fini et empeche tout cycle. Au-dela de cette borne, soit la
    structure d'onglets observee est pathologique, soit le DOM ne correspond
    plus a ce que ce parcours attend. Continuer indefiniment bloquerait tout
    l'archivage du cours sur un seul module ; on leve donc bruyamment, charge
    a l'appelant (Archiveur) de consigner un Echec isole sur ce seul module
    et de poursuivre les autres.
    """


class URL:
    """Les URL canoniques relevees en phase 0."""

    @staticmethod
    def accueil(id_site: str) -> str:
        return f"/ena/site/accueil?idSite={id_site}"

    @staticmethod
    def modules(id_site: str) -> str:
        return f"/ena/site/modules?idSite={id_site}"

    @staticmethod
    def module(id_site: str, id_module: str, id_page: str | None = None) -> str:
        """URL de la page d'un module, sur un onglet precis si id_page est fourni.

        Sans idPage, le serveur ADF sert l'onglet imprevisible (le dernier
        consulte dans la session) : voir docs/api-monportail.md, etape 3. Les
        appels existants, sans id_page, produisent l'URL exacte d'avant ce
        parametre.
        """
        base = f"/ena/site/module?idSite={id_site}&idModule={id_module}&editionModule=false"
        if id_page:
            return f"{base}&idPage={id_page}"
        return base

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
    def evaluation(id_site: str, id_evaluation: str) -> str:
        """Onglet Description (par defaut) d'une evaluation."""
        return f"/ena/site/evaluation?idSite={id_site}&idEvaluation={id_evaluation}"

    @staticmethod
    def evaluation_resultats(id_site: str, id_evaluation: str) -> str:
        return (
            f"/ena/site/evaluation?idSite={id_site}"
            f"&idEvaluation={id_evaluation}&onglet=resultats"
        )

    @staticmethod
    def redirection(id_site: str, section: str) -> str:
        """Routeur a URL stables de l'ENA, verifie en phase 0 sur liste_modules."""
        return f"/lieninterne/redirection/{id_site}/{section}"

    @staticmethod
    def cours() -> str:
        """Source d'enumeration des sessions et des cours.

        Depuis la refonte du portail (constatee le 25 septembre 2026),
        l'ancienne page /portail/cours (sur le sous-domaine sitescours)
        renvoie une page-404 : l'enumeration se fait desormais sur le
        tableau de bord du portail principal. URL absolue, jamais relative
        a BASE (voir Ena._visiter) : ce tableau de bord vit sur un
        sous-domaine different de celui des sites de cours
        (monportail.ulaval.ca, sans le prefixe "sitescours")."""
        return f"{BASE_PORTAIL}/portail/"


# Noms de section tentes pour le plan de cours. La phase 0 n'a confirme que
# `liste_modules` ; les autres sont des candidats a valider au premier passage.
SECTIONS_PLAN_DE_COURS = ("plan_de_cours", "plancours", "plan_cours")

# Le tableau de bord se souvient de la derniere session choisie : rouvert, il
# affiche celle-la, jamais une session fixe (constate en inspection reelle,
# viewport Playwright 1280x720). Toute lecture doit donc explicitement
# selectionner puis verifier la session voulue, jamais supposer que l'etat
# courant du filtre correspond deja a ce qui est demande.

# Filtre de session du tableau de bord, depuis la refonte du portail
# constatee le 25 septembre 2026 (l'ancienne page /portail/cours renvoie une
# page-404 -- voir docs/api-monportail.md, section "Portail refondu"). Le
# champ readonly du combobox porte la session affichee dans sa PROPRIETE
# `value`, jamais dans un attribut HTML : elle n'apparait donc PAS dans
# page.content(), seule une lecture Playwright (input_value()) la revele.
# Bloc du tableau de bord qui porte la liste des cours et ses squelettes de
# chargement (voir _attendre_chargement_termine) : restreint le sondage des
# squelettes a ce bloc precis, plutot qu'a toute la page.
BLOC_LISTE_COURS = ".mpo-smart-boite-liste-cours__boite.mpo--sites-lies-session"
# Restreint au bloc des cours : le tableau de bord porte d'autres blocs, et
# rien ne garantit qu'aucun n'aura un jour son propre menu deroulant du meme
# composant. Cliquer le mauvais changerait un autre filtre, et la session
# attendue ne serait jamais confirmee.
SELECTEUR_CHAMP_SESSION = f"{BLOC_LISTE_COURS} input.m-dropdown__input"
SELECTEUR_OPTION_SESSION = 'li[role="option"].m-dropdown-item'
CLASSE_TEXTE_OPTION_SESSION = "m-dropdown-item__element-text"

SELECTEUR_SQUELETTES_CHARGEMENT = (
    f"{BLOC_LISTE_COURS} .mpo--squelette-chargement, "
    f"{BLOC_LISTE_COURS} .mpo-smart-boite-liste-cours__squelette-chargement-sessions"
)

# Chronologie mesuree en inspection reelle (sondage a chaque rendu, 29
# septembre 2026) apres un clic sur « Automne 2022 » alors que « Ete 2023 »
# etait affichee :
#   43 ms     valeur = NOUVELLE session, squelette ABSENT, ANCIENNES cartes
#   152 ms    depart de la requete /services/listecours/cours/?codesession=
#   154 ms    valeur = nouvelle session, squelette present, 0 carte
#   487 ms    reponse de cette requete
#   1755 ms   valeur = nouvelle session, squelette absent, cartes finales
# Pendant environ 110 ms, le couple « valeur du champ == session demandee ET
# aucun squelette » est donc VRAI alors que la liste affichee est celle de la
# session PRECEDENTE. Ce couple, seul signal retenu jusque-la, a fait
# archiver les cours d'Automne 2022 sous « 2023-1 Hiver » lors d'un
# archivage reel. Un releve anterieur (sondage aux 100 ms) etait tombe a
# cote de cette fenetre et l'avait crue inexistante.
#
# Le signal retenu desormais ne depend d'aucun minutage : la reponse du
# serveur pour la session demandee (codesession dans l'URL, codeSession dans
# le corps) donne la liste exacte des idSite attendus, et l'on attend que les
# cartes affichees portent exactement ces idSite -- en plus de la valeur du
# champ et de l'absence de squelette. Verifie sur sept sessions reelles :
# les cartes des cours suivis correspondent toujours aux entrees typeSite
# COURS de cette reponse (les cours heberges hors monPortail n'ont pas
# d'idSite, et les formations institutionnelles n'y sont jamais affichees).
# Piege toujours present : les « Autres activites » (formations, hors
# perimetre) portent elles aussi des liens idSite= ; cours_depuis_html ne lit
# que l'accordeon des cours suivis. Voir _attendre_chargement_termine.
DELAI_CHARGEMENT_SESSION_MS = 30_000
INTERVALLE_SONDAGE_CHARGEMENT_MS = 200

# Service qui alimente la liste des cours du tableau de bord, interroge a
# chaque selection d'une session (y compris quand on reclique la session deja
# affichee, constate en inspection reelle).
CHEMIN_SERVICE_LISTE_COURS = "/services/listecours/cours/"


def _est_reponse_liste_cours(url: str, code_session: str) -> bool:
    parties = urlsplit(url)
    return (
        parties.path.endswith(CHEMIN_SERVICE_LISTE_COURS)
        and parse_qs(parties.query).get("codesession") == [code_session]
    )

# Delai accorde a l'ouverture du filtre de session (clic sur le champ, puis
# apparition d'au moins une option). Ce n'est PAS une simple ouverture de menu
# local : au chargement du tableau de bord, le champ n'existe pas encore -- un
# squelette (.mpo-smart-boite-liste-cours__squelette-chargement-sessions)
# occupe sa place tant que le serveur n'a pas rendu la liste des sessions
# (constate en inspection reelle). Le premier clic attend donc un aller-retour
# reseau. Trop court, un jour de reseau lent, et c'est toute l'enumeration qui
# echoue avant la premiere session. C'est un plafond : il ne coute rien quand
# le portail repond vite.
DELAI_OUVERTURE_FILTRE_MS = 30_000


class SelecteurSessionsIndisponible(Exception):
    """Le filtre de session du tableau de bord n'a pas repondu comme
    attendu : champ jamais ouvrable (ou n'offrant aucune option), ou
    changement de session jamais confirme -- valeur du champ ET absence de
    squelette de chargement, voir _attendre_chargement_termine -- dans le
    delai accorde.

    Une session reellement vide et un echec de chargement sont sinon
    indiscernables : lire le DOM trop tot rendrait une liste de cours vide en
    silence. C'est exactement le defaut deja constate sur l'ancien selecteur
    AngularJS de /portail/cours (deux sessions entieres disparues d'une
    enumeration reelle) ; on leve donc bruyamment plutot que de deviner.
    """


class SelecteurSessionsIllisible(Exception):
    """Le filtre de session s'est bien ouvert, mais aucune option n'y a ete
    trouvee, ou la session demandee n'y figure pas.

    Une liste de sessions vide est le pire mode de defaillance du projet :
    l'outil n'archive plus rien du tout, en silence, en laissant croire a une
    enumeration reussie. On leve donc bruyamment plutot que de rendre [], ou
    d'archiver les cours d'une autre session sous le nom demande -- une
    corruption de donnees pire qu'un arret franc.
    """


class Ena:
    def __init__(self, session, imprimer=print):
        self.session = session
        # Ou raconter une reparation de page. Attribut plutot que parametre
        # obligatoire : les appelants construisent Ena via fabrique_ena(session),
        # a un seul argument. L'interface graphique y pose son propre canal
        # apres construction, sans quoi la trace partirait sur une sortie
        # standard que sa fenetre n'affiche pas -- et une execution qui se
        # repare en silence empecherait de voir que la plateforme faiblit.
        self.imprimer = imprimer

    def _visiter(self, chemin: str) -> str:
        """Navigue vers `chemin` et rend le HTML rendu par le serveur.

        Point de passage UNIQUE de toute navigation du programme : c'est ce
        qui permet a la reparation ci-dessous de couper une cascade a la
        racine, plutot que de la laisser se propager en silence a tous les
        cours suivants. Incident reel qui motive cette reparation : un
        `goto` qui expire (Timeout) laisse la page dans un etat de
        navigation qui ne se resorbe jamais toute seule -- le `goto`
        suivant entre alors en conflit, puis meme la lecture du contenu
        finit par echouer definitivement. 17 cours sur 39 ont ete perdus
        ainsi lors d'un archivage reel : l'isolation par cours attrapait
        bien chaque exception, mais la page cassee, elle, n'etait jamais
        reparee -- cette isolation ne protegeait donc plus rien des le
        premier incident.

        On se repare donc UNE SEULE fois, jamais en boucle : sur
        ErreurPlaywright (delai depasse compris), on demande une page
        neuve au meme contexte (SessionNavigateur.reinitialiser_page, qui
        conserve les temoins d'authentification), puis on rejoue la meme
        navigation. Si cette reprise echoue a son tour, l'exception remonte
        telle quelle : l'isolation par cours (Archiveur.archiver) fait
        alors son travail, et le cours suivant repart sur une page saine
        plutot que d'heriter d'une page cassee.

        self.session.page est relu a chaque etape, jamais capture dans une
        variable locale avant la reparation : reinitialiser_page() remplace
        cet objet par une page neuve, et une reference gardee avant l'appel
        continuerait de pointer sur la page cassee.
        """
        url = chemin if chemin.startswith("http") else BASE + chemin
        try:
            self.session.page.goto(url, wait_until="networkidle")
            return self.session.page.content()
        except ErreurPlaywright:
            self.session.reinitialiser_page(imprimer=self.imprimer)
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

    def _ouvrir_filtre_session(self) -> None:
        """Clique le champ readonly du filtre de session pour faire
        apparaitre ses options, puis attend qu'au moins une soit exploitable.

        Une simple ouverture de menu local au navigateur : aucun aller-retour
        reseau attendu, d'ou un delai (DELAI_OUVERTURE_FILTRE_MS) nettement
        plus court que celui accorde au chargement d'une session.

        Leve SelecteurSessionsIndisponible si le champ n'est jamais cliquable,
        ou si aucune option n'apparait dans ce delai : ni l'un ni l'autre
        n'est tolere en silence, une session ainsi ratee disparaitrait de
        l'enumeration sans le moindre signe.
        """
        try:
            self.session.page.locator(SELECTEUR_CHAMP_SESSION).click(
                timeout=DELAI_OUVERTURE_FILTRE_MS
            )
            self.session.page.locator(SELECTEUR_OPTION_SESSION).first.wait_for(
                timeout=DELAI_OUVERTURE_FILTRE_MS
            )
        except (ErreurDelaiPlaywright, ErreurPlaywright) as erreur:
            raise SelecteurSessionsIndisponible(
                "le filtre de session (input.m-dropdown__input) n'a pas pu "
                "etre ouvert, ou n'a rendu aucune option exploitable, apres "
                f"{DELAI_OUVERTURE_FILTRE_MS} ms d'attente."
            ) from erreur

    def _libelles_options_session(self) -> list[str]:
        """Libelles bruts des options du filtre de session, une fois ouvert
        (voir _ouvrir_filtre_session) : le texte vit dans
        span.m-dropdown-item__element-text, a l'interieur de chaque
        li[role=option].m-dropdown-item."""
        return self.session.page.locator(
            f"{SELECTEUR_OPTION_SESSION} .{CLASSE_TEXTE_OPTION_SESSION}"
        ).all_text_contents()

    def sessions_disponibles(self) -> list[Session]:
        """Liste les sessions offertes par le filtre de session du tableau
        de bord (/portail/).

        Depuis la refonte du portail (constatee le 25 septembre 2026),
        l'ancienne page /portail/cours renvoie une page-404 : l'enumeration
        se fait desormais sur ce tableau de bord (voir URL.cours()).

        Si le filtre s'ouvre mais qu'aucune option n'y est trouvee, c'est que
        le mecanisme de lecture ne colle plus a la structure reelle : on leve
        SelecteurSessionsIllisible plutot que de rendre une liste vide, qui
        ferait croire a une enumeration reussie alors que l'outil
        n'archiverait plus rien.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
        self._ouvrir_filtre_session()

        libelles = self._libelles_options_session()
        if not libelles:
            raise SelecteurSessionsIllisible(
                "le filtre de session s'est ouvert, mais aucune option n'y "
                "a ete trouvee."
            )
        return [session_depuis_libelle(libelle.strip()) for libelle in libelles]

    def _selectionner_session(self, session: Session) -> list[Cours]:
        """Ouvre le filtre de session, clique l'option de la session
        demandee, puis attend la confirmation du chargement (voir
        _attendre_chargement_termine), et rend les cours de la session.

        La reponse du serveur a ce clic est capturee avec lui
        (expect_response) : c'est elle qui dit quels cours la page doit
        afficher. Leve SelecteurSessionsIndisponible si elle ne vient
        pas, SelecteurSessionsIllisible si elle porte une autre session.

        Le tableau de bord se souvient de la derniere session choisie et
        l'affiche a nouveau a l'ouverture : cette selection est donc
        toujours faite explicitement, jamais supposee deja correcte.

        Leve SelecteurSessionsIllisible si la session demandee n'est pas
        parmi les options offertes : archiver les cours d'une autre session
        sous le nom demande serait une corruption de donnees pire qu'un
        arret franc.
        """
        self._ouvrir_filtre_session()

        libelle_demande = _normaliser_espaces(session.libelle)
        textes = [_normaliser_espaces(texte) for texte in self._libelles_options_session()]
        if libelle_demande not in textes:
            raise SelecteurSessionsIllisible(
                f"session '{session.libelle}' introuvable dans le filtre de "
                f"session. Sessions disponibles : {sorted(textes)}"
            )

        options = self.session.page.locator(SELECTEUR_OPTION_SESSION)
        try:
            with self.session.page.expect_response(
                lambda reponse: _est_reponse_liste_cours(reponse.url, session.code),
                timeout=DELAI_CHARGEMENT_SESSION_MS,
            ) as attente:
                options.nth(textes.index(libelle_demande)).click()
            donnees = attente.value.json()
        except (ErreurDelaiPlaywright, ErreurPlaywright) as erreur:
            raise SelecteurSessionsIndisponible(
                f"le serveur n'a jamais rendu la liste des cours de "
                f"'{session.libelle}' (codesession={session.code}) dans le "
                f"delai accorde ({DELAI_CHARGEMENT_SESSION_MS} ms)."
            ) from erreur
        try:
            ids_attendus = ids_cours_suivis_depuis_reponse(donnees, session.code)
        except (ValueError, AttributeError, TypeError) as erreur:
            raise SelecteurSessionsIllisible(
                f"reponse illisible pour la liste des cours de "
                f"'{session.libelle}' : {erreur}"
            ) from erreur

        return self._attendre_chargement_termine(session, ids_attendus)

    def _attendre_chargement_termine(self, session: Session, ids_attendus: set[str]) -> list[Cours]:
        """Attend que la page affiche reellement les cours de `session`, et
        les rend.

        Trois conditions, toutes requises au meme sondage : la valeur du
        champ est la session demandee, aucun squelette de chargement n'est
        present, et les idSite des cartes de cours suivis sont EXACTEMENT
        `ids_attendus` (lus dans la reponse du serveur pour cette session).
        Les deux premieres seules ont ete prises en defaut en conditions
        reelles : voir la chronologie au-dessus de
        DELAI_CHARGEMENT_SESSION_MS. La troisieme est une preuve de contenu,
        pas de minutage : aucune liste d'une autre session ne peut la
        satisfaire, les idSite etant propres a chaque session.

        Une session vide (ids_attendus vide) reste un resultat normal.

        La valeur du champ (input readonly) vit dans sa PROPRIETE `value`,
        jamais dans un attribut HTML : lue via input_value(), jamais dans
        page.content().

        Leve SelecteurSessionsIndisponible si ces conditions ne sont pas
        reunies dans DELAI_CHARGEMENT_SESSION_MS : lire la page dans cet
        etat archiverait les cours d'une autre session sous le nom demande,
        ou en omettrait en silence.
        """
        libelle_attendu = _normaliser_espaces(session.libelle)
        champ = self.session.page.locator(SELECTEUR_CHAMP_SESSION)
        squelettes = self.session.page.locator(SELECTEUR_SQUELETTES_CHARGEMENT)

        ids_affiches: set[str] = set()
        temps_ecoule = 0
        while True:
            valeur = champ.input_value()
            if _normaliser_espaces(valeur) == libelle_attendu and squelettes.count() == 0:
                cours = cours_depuis_html(self.session.page.content(), session)
                ids_affiches = {c.id_site for c in cours if c.id_site}
                if ids_affiches == ids_attendus:
                    return cours
            if temps_ecoule >= DELAI_CHARGEMENT_SESSION_MS:
                break
            self.session.page.wait_for_timeout(INTERVALLE_SONDAGE_CHARGEMENT_MS)
            temps_ecoule += INTERVALLE_SONDAGE_CHARGEMENT_MS

        raise SelecteurSessionsIndisponible(
            f"la page n'a jamais affiche les cours de '{session.libelle}' "
            f"annonces par le serveur dans le delai accorde "
            f"({DELAI_CHARGEMENT_SESSION_MS} ms) : attendus "
            f"{sorted(ids_attendus)}, derniers affiches {sorted(ids_affiches)}."
        )

    def sites_de_session(self, session: Session) -> list[Cours]:
        """Selectionne une session puis rend ses cours.

        La confirmation du changement (_attendre_chargement_termine) est
        attendue avant toute lecture du DOM : sans elle, une session
        reellement vide et un echec de chargement (page pas prete, squelette
        encore affiche) sont indiscernables. Une fois la session confirmee,
        une liste de cours vide est un resultat normal (par exemple la
        session courante de l'utilisateur, pas encore remplie) : elle ne
        leve rien -- voir cours_depuis_html, qui ne lit que l'accordeon des
        cours suivis et ignore les « Autres activites » voisines.

        Leve SelecteurSessionsIllisible si la session demandee n'est pas
        offerte par le filtre, ou SelecteurSessionsIndisponible si le
        changement n'est jamais confirme : dans les deux cas, lire la page
        reviendrait a archiver les cours d'une autre session sous le nom
        demande, une corruption de donnees pire qu'une liste vide.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
        return self._selectionner_session(session)

    def modules(self, cours) -> list:
        html = self._visiter(URL.modules(cours.id_site))
        self._assurer_authentifie()
        return modules_depuis_html(html, cours.id_site)

    def fichiers_du_module(self, module, id_page: str | None = None) -> list:
        """Fichiers d'un module, sur un onglet precis si id_page est fourni.

        L'onglet est adressable par URL (voir docs/api-monportail.md, etape
        3) : aucun clic ADF n'est necessaire. Sans id_page, l'onglet servi
        est celui par defaut du serveur ADF -- comportement d'avant l'ajout
        des onglets, conserve pour les appelants qui ne les distinguent pas.
        """
        html = self._visiter(URL.module(module.id_site, module.id_module, id_page))
        self._assurer_authentifie()
        return fichiers_depuis_html(html)

    def pages_du_module(self, module) -> list[PageDeModule]:
        """Parcours en largeur des onglets d'un module, par identifiant de
        page, et rend une PageDeModule par feuille reellement atteinte.

        Une page de module peut porter plusieurs barres d'onglets empilees
        (voir docs/api-monportail.md, etape 3) : une barre de second niveau
        n'existe dans le DOM que si son onglet parent, au niveau precedent,
        est selectionne. On ne peut donc pas tout enumerer d'une seule
        visite ; ce parcours visite chaque onglet decouvert par son propre
        idPage pour reveler les niveaux plus profonds qu'il ne peut pas
        encore voir.

        Naviguer vers l'idPage d'un onglet PARENT redescend automatiquement
        sur sa premiere feuille (verifie en inspection reelle, regle 3) :
        l'identifiant demande (id_demande) et celui reellement servi
        (id_reel, lu dans le DOM apres navigation via
        onglets_selectionnes_depuis_html) peuvent donc differer. `vus` est
        indexe par id_reel, jamais par id_demande, pour reconnaitre ces
        redescentes vers une feuille deja traitee et ne pas la retraiter :
        sans cette distinction, la file ne se viderait jamais.

        Un module sans aucune barre d'onglets est un montage legitime (voir
        docs/api-monportail.md) : rend une liste a un seul element
        (id_page=None, chemin=()), comportement d'avant l'ajout des onglets,
        conserve tel quel.

        Leve TropDePagesDansUnModule des que LIMITE_PAGES_MODULE pages
        distinctes ont ete retenues sans que la file ne se soit videe : voir
        sa docstring pour ce que cette borne protege.
        """
        a_visiter: list[str | None] = [None]
        # Tout identifiant deja demande, qu'il ait ete servi ou non. Distinct
        # de `vus`, qui ne retient que les identifiants REELLEMENT servis :
        # un onglet parent redescend toujours sur une de ses feuilles (regle 3
        # de docs/api-monportail.md), donc son propre identifiant n'entre
        # jamais dans `vus`. Sans `demandes`, il serait re-enfile a chaque
        # nouvelle page decouverte, comme chaque feuille voisine pas encore
        # visitee -- le nombre de navigations croissant alors avec le carre du
        # nombre d'onglets. Constate en conditions reelles : 22 navigations
        # pour 7 pages sur un module a six feuilles, et des heures sur un
        # cours entier au lieu de quelques minutes, l'operateur voyant defiler
        # indefiniment les memes noms d'en-tetes.
        demandes: set[str | None] = {None}
        vus: set[str | None] = set()
        pages: list[PageDeModule] = []
        visites = 0

        def _suivant() -> "str | None":
            """Retire et rend le prochain onglet a visiter : le plus profond
            en attente, et parmi ceux-la le premier arrive.

            C'est ce qui fait descendre une section jusqu'au bout -- tous les
            sous-onglets de l'en-tete courant -- avant de passer a l'en-tete
            suivant. Un simple defilement de file (parcours en largeur) ferait
            l'inverse : il alternerait entre les en-tetes, ce qui couvre
            pourtant tout, mais donne a l'operateur l'impression que
            l'archivage tourne en rond sans jamais finir une section.
            """
            choisi = 0
            for indice in range(1, len(a_visiter)):
                if profondeurs.get(a_visiter[indice], 1) > profondeurs.get(a_visiter[choisi], 1):
                    choisi = indice
            return a_visiter.pop(choisi)

        profondeurs: dict[str | None, int] = {}

        while a_visiter:
            # Borne sur les NAVIGATIONS, pas sur les pages retenues : c'est le
            # nombre d'allers-retours reseau qui fait le cout, et une page
            # servie en boucle sans jamais etre retenue n'aurait fait monter
            # aucun compteur de pages.
            if visites >= LIMITE_PAGES_MODULE:
                raise TropDePagesDansUnModule(
                    f"module {module.id_module} : plus de "
                    f"{LIMITE_PAGES_MODULE} navigations d'onglets "
                    "-- structure pathologique ou cyclique."
                )

            id_demande = _suivant()
            visites += 1
            html = self._visiter(URL.module(module.id_site, module.id_module, id_demande))
            self._assurer_authentifie()

            chaine = onglets_selectionnes_depuis_html(html)
            id_reel = chaine[-1].id_page if chaine else None

            # L'identifiant servi vaut aussi comme demande : inutile de le
            # redemander plus tard sous son propre nom, on vient de le lire.
            demandes.add(id_reel)

            if id_reel in vus:
                continue
            vus.add(id_reel)

            pages.append(PageDeModule(id_page=id_reel, chemin=tuple(o.titre for o in chaine)))

            for onglet in onglets_depuis_html(html):
                if onglet.id_page not in demandes:
                    demandes.add(onglet.id_page)
                    profondeurs[onglet.id_page] = onglet.profondeur
                    a_visiter.append(onglet.id_page)

        return pages

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

    def fichiers_de_description(self, evaluation) -> list:
        """Pieces jointes de l'onglet Description (par defaut) d'une evaluation.

        Une description d'evaluation peut porter l'enonce d'un travail en
        piece jointe : la plateforme fermant le 1er novembre 2026, ces
        consignes disparaitraient sans laisser de trace si on ne les
        recuperait pas ici, au meme titre que les documents deposes.
        """
        html = self._visiter(URL.evaluation(evaluation.id_site, evaluation.id_evaluation))
        self._assurer_authentifie()
        return fichiers_depuis_html(html)

    def fichiers_de_resultats_evaluation(self, evaluation) -> list:
        """Pieces jointes de l'onglet Resultats d'une evaluation : peut porter
        une retroaction du professeur, au meme titre qu'un enonce en
        description.
        """
        html = self._visiter(URL.evaluation_resultats(evaluation.id_site, evaluation.id_evaluation))
        self._assurer_authentifie()
        return fichiers_depuis_html(html)

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
        self.capturer_pdf_page_courante(destination)

    def capturer_pdf_page_courante(self, destination: Path) -> None:
        """Imprime la page DEJA ouverte, sans navigation.

        Chaque navigation est un aller-retour ADF de plusieurs secondes. Quand
        l'appelant vient de lire une page -- pour ses fichiers, par exemple --
        le navigateur y est encore : la recharger pour l'imprimer doublait le
        cout de chaque module, l'operateur voyant la meme page se charger
        plusieurs fois de suite.

        L'appelant doit donc s'etre assure qu'il est bien sur la page voulue.
        S'il ne peut pas le garantir -- une lecture qui a echoue laisse le
        navigateur ailleurs -- il doit passer par capturer_pdf, qui navigue :
        imprimer la mauvaise page produirait un PDF d'apparence valide au
        contenu faux, exactement le genre de manque invisible que ce projet
        cherche a eviter.
        """
        self._assurer_authentifie()
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.session.page.pdf(path=str(destination), format="A4", print_background=True)

    def ressources_ignorees_page_courante(self) -> list:
        """Ressources deliberement non telechargees (videos, liens externes,
        traceurs inconnus) de la page DEJA ouverte, sans navigation.

        Meme motif que capturer_pdf_page_courante : l'appelant vient de lire
        cette page pour ses fichiers, le navigateur y est encore -- une
        navigation ADF de plus couterait plusieurs secondes pour rien.
        """
        self._assurer_authentifie()
        return ressources_ignorees_depuis_html(self.session.page.content())

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


def _normaliser_espaces(texte: str) -> str:
    """Nettoie un texte lu du DOM pour une comparaison fiable cote Python :
    espaces de tete et de fin retires, espaces internes (y compris sauts de
    ligne et indentation) reduits a un seul espace.

    Utilise partout ou un texte lu du DOM (champ de session, options) est
    compare a un libelle attendu, plutot que de confier cette comparaison,
    ancree, a une expression reguliere evaluee cote navigateur -- voir
    _attendre_chargement_termine et _selectionner_session pour le piege que
    ce nettoyage evite (deja constate sur l'ancien selecteur AngularJS de
    /portail/cours : Playwright ne normalise pas les espaces d'un texte
    compare a un motif ancre).
    """
    return " ".join(texte.split())


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
