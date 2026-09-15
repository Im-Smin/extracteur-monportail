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

# Delai minimal accorde a l'apparition du bouton du selecteur de sessions.
# Releve en session reelle : /portail/cours (application AngularJS) met 8 a
# 9 secondes a se rendre, largement au-dela du signal reseau "networkidle"
# que Playwright declenche des que les requetes se taisent -- bien avant que
# l'UI ne soit affichee. Attendre ce signal reseau plutot qu'un element
# concret du DOM final a deja fait disparaitre deux sessions entieres d'une
# enumeration reelle, en silence. Ce delai est nettement plus genereux que
# le temps observe, pour absorber les sessions plus lentes.
DELAI_CHARGEMENT_SESSIONS_MS = 30_000

# Delai accorde a la confirmation du changement de session par le bouton,
# une fois son option cliquee. Le clic recharge la liste des cours : le
# bouton du selecteur est detruit puis recree par AngularJS, exactement le
# meme rendu que le chargement initial de /portail/cours -- mesure a 8 a 10
# secondes en session reelle (constat direct sur un plantage en conditions
# reelles : "Hiver 2027" a echoue sur SelecteurSessionsIndisponible avec un
# delai de 5000 ms, coupe court avant la fin du rendu). Aligne donc sur
# DELAI_CHARGEMENT_SESSIONS_MS, avec la meme marge : une valeur reduite sans
# connaitre cette mesure ferait a nouveau disparaitre des sessions en
# silence, exactement le defaut que ce delai corrige.
DELAI_CONFIRMATION_SESSION_MS = DELAI_CHARGEMENT_SESSIONS_MS

# Delai maximal et intervalle de sondage accordes a la stabilisation du
# compte d'options du panneau de sessions, une fois celui-ci ouvert.
# .count() et .all_text_contents() de Playwright n'attendent rien : ils
# interrogent le DOM a l'instant precis ou on les appelle. Si AngularJS
# peuple le panneau en differe, une lecture immediate peut tomber sur un
# compte partiel (par exemple neuf options sur douze) sans qu'aucune
# exception ne le signale -- la liste n'est pas vide. On sonde donc
# plusieurs fois, et on ne lit le contenu final que lorsque le compte se
# maintient sur NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION sondages
# consecutifs, sur une fenetre d'observation d'au moins
# DELAI_OBSERVATION_MINIMALE_STABILISATION_MS -- symetriquement a l'attente
# deja appliquee au bouton et a sa confirmation.
#
# Deux sondages consecutifs egaux (version anterieure) s'est revele
# insuffisant : rejoue contre des sequences de comptes programmees, ce
# critere concluait "stable" sur un palier permanent a neuf options (au lieu
# de douze), en silence, et sur une oscillation entre deux comptes, il
# rendait la derniere valeur sondee -- tronquee -- sans lever la moindre
# exception. Voir la docstring de _stabiliser_options pour ce que ce
# durcissement garantit reellement, et ce qu'il ne peut pas garantir.
DELAI_STABILISATION_OPTIONS_SESSIONS_MS = 2_000
INTERVALLE_SONDAGE_STABILISATION_MS = 100
NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION = 3
DELAI_OBSERVATION_MINIMALE_STABILISATION_MS = 600


class SelecteurSessionsIndisponible(Exception):
    """Le mecanisme du selecteur de sessions n'a pas repondu comme attendu :
    bouton jamais apparu apres un delai genereux, clic sur le bouton en
    echec malgre sa presence, ou session cliquee dont la confirmation n'est
    jamais apparue sur le bouton.

    /portail/cours est une application AngularJS qui met 8 a 9 secondes a se
    rendre en usage reel, bien au-dela du signal reseau "networkidle" que
    Playwright declenche des que les requetes se taisent. Une page lue trop
    tot semble "sans cours", en silence : deux sessions entieres de
    l'historique de l'utilisateur ont ainsi disparu d'une enumeration reelle.
    Une session reellement vide et un echec de chargement sont sinon
    indiscernables ; on leve donc bruyamment plutot que de deviner.
    """


class SelecteurSessionsIllisible(Exception):
    """Le panneau du selecteur de sessions s'est ouvert, mais aucune option
    de la forme <saison> <annee> n'y a ete trouvee.

    Une liste de sessions vide est le pire mode de defaillance du projet :
    l'outil n'archive plus rien du tout, en silence, en laissant croire a une
    enumeration reussie. On leve donc bruyamment plutot que de rendre [].
    Lancer `python -m extracteur --diagnostic` pour un etat des lieux du DOM
    reel avant de deviner une nouvelle correction.
    """


class SelecteurSessionsInstable(Exception):
    """Le compte d'options du panneau de sessions n'a jamais tenu
    NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION sondages consecutifs avant
    l'ecoulement du delai maximal accorde a sa stabilisation.

    Rejoue contre des sequences de comptes programmees, une oscillation
    permanente (par exemple 3, 9, 3, 9, ...) ne se stabilise jamais : rendre
    la derniere valeur sondee produirait une liste de sessions tronquee, sans
    qu'aucun signe ne le trahisse. C'est pire qu'un arret franc. On leve donc
    cette exception plutot que de deviner laquelle des valeurs sondees serait
    la bonne.
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

    def _ouvrir_selecteur_sessions(self) -> None:
        """Attend que le bouton du selecteur de sessions soit present et
        exploitable, puis clique dessus pour faire apparaitre ses options.

        /portail/cours met 8 a 9 secondes a se rendre en usage reel
        (application AngularJS) : le signal reseau "networkidle" se
        declenche bien avant que le bouton n'existe dans le DOM. On attend
        donc explicitement ce bouton, avec un delai genereux
        (DELAI_CHARGEMENT_SESSIONS_MS), plutot que d'esperer qu'un clic
        immediat tombe juste.

        Leve SelecteurSessionsIndisponible si le bouton n'apparait jamais
        dans ce delai, ou si le clic echoue malgre sa presence : ni l'un ni
        l'autre n'est tolere en silence, une session ainsi ratee
        disparaitrait de l'enumeration sans le moindre signe.
        """
        try:
            self.session.page.locator(self.SELECTEUR_SESSIONS).wait_for(
                timeout=DELAI_CHARGEMENT_SESSIONS_MS
            )
            self.session.page.click(self.SELECTEUR_SESSIONS, timeout=5000)
        except (ErreurDelaiPlaywright, ErreurPlaywright) as erreur:
            raise SelecteurSessionsIndisponible(
                "le bouton du selecteur de sessions "
                '(div[role="listbox"].mpo-deroulant-bouton) n\'a pas pu '
                f"etre ouvert apres {DELAI_CHARGEMENT_SESSIONS_MS} ms "
                "d'attente. La page /portail/cours (application AngularJS) "
                "met 8 a 9 secondes a se rendre en usage reel ; ce delai "
                "peut signaler un vrai probleme de chargement, pas une "
                "session sans cours."
            ) from erreur

    def _stabiliser_options(self, obtenir_options):
        """Sonde obtenir_options() a repetition jusqu'a ce que son .count()
        se maintienne sur NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION sondages
        consecutifs, sur une fenetre d'observation d'au moins
        DELAI_OBSERVATION_MINIMALE_STABILISATION_MS ; rend alors le dernier
        Locator sonde.

        .count() et .all_text_contents() de Playwright interrogent le DOM a
        l'instant precis de l'appel, sans le moindre reessai. Un panneau
        AngularJS peuple en differe peut donc etre lu a mi-chemin : le compte
        n'est pas nul, alors qu'il grossit encore. Aucune exception n'est
        levee dans ce cas par Playwright lui-meme -- la liste n'est
        simplement pas complete. On sonde donc a nouveau plutot que de faire
        confiance a la premiere lecture.

        Ce que ce durcissement garantit : un panneau qui se peuple
        progressivement (par exemple trois options, puis douze) est attendu
        jusqu'a ce qu'il cesse reellement de grossir, et non plus jusqu'a la
        premiere coincidence entre deux sondages qui pourrait n'etre qu'une
        pause passagere. Si le compte oscille sans jamais trouver de palier
        avant le delai maximal, on leve SelecteurSessionsInstable plutot que
        de rendre la derniere valeur sondee, tronquee.

        Ce que ce durcissement NE garantit PAS : un panneau qui plafonne
        durablement a un compte incomplet (par exemple neuf options qui ne
        deviennent jamais douze) est indiscernable d'un panneau complet. Rien
        dans le DOM ne distingue les deux cas, et aucun sondage supplementaire
        ne peut trancher a la place de l'utilisateur. C'est pour cette raison
        que le nombre de sessions trouvees est annonce explicitement par
        --lister (voir _afficher_sessions) : seul l'utilisateur, qui connait
        son propre parcours, peut remarquer qu'il en manque.
        """
        options = obtenir_options()
        compte_precedent = options.count()
        sondages_consecutifs_egaux = 1
        temps_ecoule = 0
        while not (
            sondages_consecutifs_egaux >= NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION
            and temps_ecoule >= DELAI_OBSERVATION_MINIMALE_STABILISATION_MS
        ):
            if temps_ecoule >= DELAI_STABILISATION_OPTIONS_SESSIONS_MS:
                raise SelecteurSessionsInstable(
                    "le compte d'options du panneau de sessions n'a jamais "
                    f"tenu {NOMBRE_SONDAGES_CONSECUTIFS_STABILISATION} "
                    "sondages consecutifs apres "
                    f"{DELAI_STABILISATION_OPTIONS_SESSIONS_MS} ms d'attente "
                    f"(dernier compte sonde : {compte_precedent}). Rendre "
                    "cette derniere valeur risquerait de tronquer la liste "
                    "des sessions en silence."
                )
            self.session.page.wait_for_timeout(INTERVALLE_SONDAGE_STABILISATION_MS)
            temps_ecoule += INTERVALLE_SONDAGE_STABILISATION_MS
            options = obtenir_options()
            compte_actuel = options.count()
            if compte_actuel == compte_precedent:
                sondages_consecutifs_egaux += 1
            else:
                sondages_consecutifs_egaux = 1
            compte_precedent = compte_actuel
        return options

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

        Le compte est stabilise (voir _stabiliser_options) avant toute
        decision : AngularJS peut peupler ce panneau en differe, et une
        lecture immediate ne leve aucune exception sur un compte partiel.
        """
        par_classe = self._stabiliser_options(
            lambda: self.session.page.locator(self.SELECTEUR_OPTIONS_SESSIONS_CLASSE)
        )
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

        Le compte est lui aussi stabilise (voir _stabiliser_options) avant
        toute lecture : ce repli est interroge exactement au meme instant,
        juste apres le clic d'ouverture, et court le meme risque de lire un
        panneau AngularJS encore en cours de peuplement.
        """
        candidats = self._stabiliser_options(
            lambda: self.session.page.locator(self.SELECTEUR_OPTIONS_SESSIONS)
        )
        textes_valides = {
            texte
            for texte in candidats.all_text_contents()
            if MOTIF_LIBELLE_SESSION.match(texte.strip())
        }
        if not textes_valides:
            return candidats.filter(has_text=MOTIF_LIBELLE_SESSION)

        motif_valides = re.compile("|".join(re.escape(texte) for texte in textes_valides))
        return candidats.filter(has_text=motif_valides)

    def _selectionner_session(self, session: Session) -> None:
        """Selectionne une session en cliquant son option dans le selecteur,
        puis attend que le bouton confirme reellement ce changement.

        Repere l'option par le meme mecanisme que la lecture :
        classe canonique d'abord, repli par forme de libelle. Tolere les
        espaces parasites autour du libelle en comparant les textes nettoyes
        cote Python, puis cherche le texte brut correspondant dans le DOM pour
        le cliquer.

        Leve SelecteurSessionsIllisible si le selecteur s'est bien ouvert mais
        que la session n'y est introuvable : attendre les cours d'une autre
        session et les archiver sous le nom demande est une corruption de
        donnees pire qu'une liste vide. On garantit donc bruyamment que la
        session demandee existe bien avant de laisser l'extraction se
        poursuivre en silence avec des cours faux.

        Leve SelecteurSessionsIndisponible si, apres le clic, le bouton ne
        confirme jamais le passage a cette session (voir
        _confirmer_session_selectionnee) : un clic tombe dans le vide sur
        une page pas encore prete laisserait sinon lire les cours de la
        session encore affichee, sous le nom de celle demandee.
        """
        options = self._options_sessions()

        # Charger tous les textes bruts des options et construire une
        # correspondance entre libelle nettoye et texte brut. Cela permet
        # de trouver l'option meme si elle porte des espaces parasites.
        textes_bruts = options.all_text_contents()
        correspondance = {}  # libelle_nettoye -> texte_brut
        for texte in textes_bruts:
            libelle_nettoye = texte.strip()
            # Garder seulement la premiere occurrence (bien qu'il ne devrait
            # y en avoir qu'une).
            if libelle_nettoye not in correspondance:
                correspondance[libelle_nettoye] = texte

        # Chercher la session demandee.
        libelle_demande = session.libelle.strip()
        if libelle_demande not in correspondance:
            sessions_trouvees = sorted(correspondance.keys())
            raise SelecteurSessionsIllisible(
                f"session '{session.libelle}' introuvable dans le selecteur. "
                f"Sessions disponibles: {sessions_trouvees}"
            )

        # Construire un motif pour chercher le texte brut correspondant
        # (non ancre, pour tolerer les espaces parasites).
        texte_cible = correspondance[libelle_demande]
        motif_cible = re.compile(re.escape(texte_cible))

        lien = options.filter(has_text=motif_cible).first
        lien.click(timeout=5000)

        self._confirmer_session_selectionnee(session)

    def _confirmer_session_selectionnee(self, session: Session) -> None:
        """Attend que le bouton du selecteur affiche exactement le libelle
        de la session demandee, apres le clic sur son option.

        Verifiee et non esperee : le bouton du selecteur rend sa valeur
        courante (constate en session reelle -- apres avoir clique "Hiver
        2023", le bouton rend exactement "Hiver 2023"). C'est une
        verification gratuite et decisive, qui distingue un vrai changement
        de session d'un clic tombe dans le vide sur une page pas encore
        prete. Le clic recharge la liste (bouton detruit puis recree par
        AngularJS) : le delai accorde ici est donc celui du rendu complet de
        /portail/cours, mesure a 8 a 10 secondes en session reelle, pas celui
        du seul changement de valeur affichee (voir DELAI_CONFIRMATION_SESSION_MS).

        Leve SelecteurSessionsIndisponible si cette confirmation n'apparait
        jamais dans ce delai.
        """
        libelle_attendu = session.libelle.strip()
        motif_exact = re.compile(f"^{re.escape(libelle_attendu)}$")
        bouton_confirme = self.session.page.locator(self.SELECTEUR_SESSIONS).filter(
            has_text=motif_exact
        )
        try:
            bouton_confirme.wait_for(timeout=DELAI_CONFIRMATION_SESSION_MS)
        except (ErreurDelaiPlaywright, ErreurPlaywright) as erreur:
            raise SelecteurSessionsIndisponible(
                f"le bouton du selecteur de sessions n'a jamais confirme le "
                f"passage a '{session.libelle}' apres le clic, dans le "
                f"delai accorde ({DELAI_CONFIRMATION_SESSION_MS} ms). Le "
                "clic est peut-etre tombe dans le vide sur une page pas "
                "encore prete."
            ) from erreur

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
        self._ouvrir_selecteur_sessions()

        libelles = self._options_sessions().all_text_contents()
        if not libelles:
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

        L'ouverture du selecteur (_ouvrir_selecteur_sessions) peut echouer et
        lever SelecteurSessionsIndisponible : le bouton attendu par les
        fonctions d'archivage est precisement le pire scenario que ce mode
        diagnostic doit pouvoir examiner (panne totale, bouton jamais
        apparu). Une exception non geree ici empecherait tout diagnostic --
        on l'intercepte donc, on la consigne dans le rapport, et on
        interroge quand meme tous les selecteurs candidats : des comptes a
        zero sont eux-memes une information utile pour trancher sur des
        faits.

        La lecture par la forme du libelle (_options_sessions, via
        _stabiliser_options) peut de la meme facon lever
        SelecteurSessionsInstable si le compte d'options oscille sans
        jamais se stabiliser. Ce mode existe pour les situations ou plus
        rien ne marche : il doit rapporter ce qu'il observe, jamais
        s'interrompre. On l'intercepte donc elle aussi, on la consigne, et
        le compte de liens idSite= est quand meme interroge ensuite.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()

        echec_ouverture_selecteur = None
        try:
            self._ouvrir_selecteur_sessions()
        except SelecteurSessionsIndisponible as erreur:
            echec_ouverture_selecteur = str(erreur)

        candidats = []
        for selecteur in CANDIDATS_DIAGNOSTIC_SESSIONS:
            textes = self.session.page.locator(selecteur).all_text_contents()
            candidats.append((selecteur, len(textes), textes[:5]))

        echec_stabilisation_options = None
        try:
            textes_forme = self._options_sessions().all_text_contents()
            candidats.append(
                ("forme du libelle (saison + annee)", len(textes_forme), textes_forme[:12])
            )
        except SelecteurSessionsInstable as erreur:
            echec_stabilisation_options = str(erreur)
            candidats.append(("forme du libelle (saison + annee)", 0, []))

        liens_id_site = self.session.page.locator("a[href*='idSite=']").count()

        return {
            "candidats": candidats,
            "liens_id_site": liens_id_site,
            "echec_ouverture_selecteur": echec_ouverture_selecteur,
            "echec_stabilisation_options": echec_stabilisation_options,
        }

    def sites_de_session(self, session: Session) -> list[Cours]:
        """Selectionne une session puis rend ses cours.

        La confirmation du changement (bouton affichant exactement le
        libelle demande, voir _confirmer_session_selectionnee) est attendue
        avant toute lecture du DOM : sans elle, une session reellement vide
        et un echec de chargement (page pas prete, clic tombe dans le vide)
        sont indiscernables, et le second finit par etre lu comme le
        premier -- exactement le defaut qui a fait disparaitre deux sessions
        entieres d'une enumeration reelle, en silence. Une fois la session
        confirmee, une liste de cours vide est un resultat normal (par
        exemple la session courante de l'utilisateur, pas encore remplie) :
        elle ne leve rien.

        Leve SelecteurSessionsIllisible si la session demandee n'est pas
        presente dans le selecteur, ou SelecteurSessionsIndisponible si le
        bouton du selecteur ne confirme jamais le changement : dans les deux
        cas, lire la page reviendrait a archiver les cours d'une autre
        session sous le nom demande, une corruption de donnees pire qu'une
        liste vide.
        """
        self._visiter(URL.cours())
        self._assurer_authentifie()
        self._ouvrir_selecteur_sessions()
        self._selectionner_session(session)

        # Marge de securite apres la confirmation du bouton : la session
        # reelle mesuree affiche a la fois le bouton et les cours en moins
        # de 500 ms, mais rien ne garantit que les deux soient rendus dans
        # la meme micro-tache Angular.
        self.session.page.wait_for_timeout(500)

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
