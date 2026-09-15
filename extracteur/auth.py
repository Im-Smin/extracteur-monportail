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

Pas de profil de navigateur persistant. Une version anterieure conservait le
profil Chromium d'un lancement a l'autre (dossier .session a la racine du
projet), pour eviter une reconnexion a chaque execution. Ce profil s'est
corrompu a deux reprises en conditions reelles -- vraisemblablement a la
suite d'executions interrompues (Ctrl+C, plantage) -- avec un symptome
trompeur : le navigateur s'ouvrait et restait indefiniment bloque sur le
domaine de connexion Microsoft, sans jamais aboutir, meme quand une autre
fenetre etait deja authentifiee. Le diagnostic a chaque fois coute du temps,
car rien ne distinguait ce blocage d'un probleme de detection de connexion.
Un drapeau de reinitialisation avait ete ajoute en remede ; il a ete retire a
son tour, au profit d'un choix plus simple : plus aucun profil ne survit a
l'execution qui l'a cree. Chaque lancement de SessionNavigateur.ouvrir() cree
un dossier de profil neuf dans le dossier temporaire du systeme (jamais dans
le depot, jamais dans un dossier synchronise -- voir sa docstring), et
SessionNavigateur.fermer() le supprime toujours, y compris sur interruption
ou erreur. Le cout est une authentification a chaque lancement ; le calcul
tient parce que le mode --tout traite les 33 cours du projet en une seule
execution. Voir docs/api-monportail.md, section "Pieges d'exploitation", pour
le recit complet de l'incident.
"""

import http.cookiejar
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Error as ErreurPlaywright
from playwright.sync_api import sync_playwright

URL_DEPART = "https://sitescours.monportail.ulaval.ca/portail/cours"
HOTE_SITESCOURS = "sitescours.monportail.ulaval.ca"
# Second hote attendu cote portail (sans le sous-domaine sitescours) : une
# authentification federee peut aboutir ici plutot que sur HOTE_SITESCOURS
# selon l'endroit d'ou elle a demarre. Purement informatif : le critere reel
# est plus large, voir est_page_authentifiee.
HOTE_PORTAIL = "monportail.ulaval.ca"
# Domaine dont l'appartenance suffit a considerer une page authentifiee, une
# fois exclus les hotes de connexion (voir HOTES_CONNEXION_MICROSOFT).
DOMAINE_ULAVAL = "ulaval.ca"
TAILLE_MORCEAU = 65536

# Plafond d'attente de la connexion manuelle (MFA compris). L'ancien reglage
# de 300s (5 minutes) coupait court a une authentification a deux facteurs
# lente (telephone hors de portee), sans jamais prevenir l'utilisateur qu'il
# restait peu de temps.
DELAI_ATTENTE_CONNEXION = 600
# Frequence des lignes d'etat pendant l'attente : assez frequent pour
# distinguer une attente normale d'un blocage, assez espace pour rester
# lisible sur un plafond de plusieurs minutes.
INTERVALLE_STATUT_ATTENTE = 5
# Temps passe sur le domaine de connexion Microsoft avant de rappeler que le
# selecteur de compte propose plusieurs comptes : cause de blocage muette,
# l'utilisateur ne peut pas la deviner de lui-meme.
SEUIL_RAPPEL_SELECTEUR_COMPTE = 15
# Temps restant, en secondes, a partir duquel la ligne d'etat annonce
# explicitement combien de temps il reste avant l'abandon de l'attente.
SEUIL_FIN_IMMINENTE = 30
# Sous-chaine suffisante pour reconnaitre un hote de connexion Microsoft
# (login.microsoftonline.com, login.microsoft.com, etc.) sans faire de la
# detection d'authentification (est_page_authentifiee) un lieu de decision
# supplementaire. Sert uniquement au rappel du selecteur de compte
# (attendre_connexion) ; est_page_authentifiee applique son propre critere,
# voir HOTES_CONNEXION_MICROSOFT.
FRAGMENT_HOTE_MICROSOFT = "microsoft"

# Suffixes d'hote d'une connexion federee Microsoft : jamais une page
# authentifiee de monPortail, meme si son URL contient par ailleurs l'adresse
# du portail en clair (voir est_page_authentifiee). En pratique aucun de ces
# hotes n'appartient au domaine ulaval.ca, donc ce test est deja assure par
# la verification de domaine qui precede -- il est garde explicite pour dire
# noir sur blanc ce qu'exclut le critere, plutot que de laisser cette
# exclusion implicite dans une simple difference de domaine.
HOTES_CONNEXION_MICROSOFT = ("microsoftonline.com", "live.com")

# Titre d'onglet distinctif de la fenetre pilotee, et prefixe applique a tout
# titre ulterieur (page de connexion Microsoft, monPortail) pour que la
# fenetre reste reconnaissable meme apres la navigation initiale. Introduits
# a la suite d'un incident de terrain : l'utilisateur s'authentifiait dans
# une fenetre de navigateur qui n'etait pas celle pilotee par le programme
# (capture montrant une barre de favoris et un avatar de compte Google,
# impossible sur le profil temporaire et vierge cree par ouvrir()), pendant
# que la fenetre reellement pilotee restait indefiniment bloquee sur
# login.microsoftonline.com. Rien ne distinguait alors les deux fenetres a
# l'oeil ; ces constantes servent a rendre cette confusion impossible plutot
# que de formuler une nouvelle hypothese de diagnostic.
TITRE_FENETRE_PILOTEE = "ARCHIVEUR MONPORTAIL - CONNECTEZ-VOUS ICI"
PREFIXE_TITRE_FENETRE_PILOTEE = "[Archiveur monPortail] "

# Page affichee par ouvrir() avant toute navigation vers monPortail, le temps
# que l'utilisateur la lise (voir DUREE_AFFICHAGE_PAGE_IDENTIFICATION). Batie
# sans fichier externe ni dependance nouvelle : une simple chaine HTML passee
# a Page.set_content(). Le texte evite les caracteres accentues, comme le
# reste des messages de ce projet.
PAGE_IDENTIFICATION = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{TITRE_FENETRE_PILOTEE}</title>
</head>
<body style="font-family: sans-serif; padding: 3em; font-size: 1.4em; line-height: 1.5;">
<h1>Fenetre ouverte par l'outil d'archivage monPortail</h1>
<p><strong>C'est ICI qu'il faut se connecter, dans cette fenetre.</strong></p>
<p>Cette fenetre est pilotee par le programme d'archivage. Son profil de
navigateur est cree vierge a chaque lancement : ni favoris, ni compte
personnel enregistre, ni historique de navigation.</p>
<p>Se connecter dans une autre fenetre ou un autre navigateur ne servira a
rien : le programme ne peut lire que les cookies de <em>cette</em>
fenetre-ci, celle dont l'onglet porte le titre ci-dessus.</p>
<p>Redirection automatique vers la page de connexion dans quelques
secondes...</p>
</body>
</html>
"""

# Temps d'affichage de PAGE_IDENTIFICATION avant de naviguer vers monPortail :
# assez long pour que l'utilisateur la remarque et la lise, assez court pour
# ne pas retarder inutilement chaque lancement.
DUREE_AFFICHAGE_PAGE_IDENTIFICATION = 3

# Injecte dans chaque document charge par le contexte (page d'identification
# comprise, puis toute navigation ulterieure -- page de connexion Microsoft,
# monPortail) : reecrit le texte de l'element <title> avec le prefixe
# distinctif des qu'il apparait ou change, pour que l'onglet reste
# reconnaissable meme une fois sorti de PAGE_IDENTIFICATION. Un
# MutationObserver sur l'element <title> plutot qu'une simple redefinition de
# la propriete document.title : un titre pose par le HTML statique du serveur
# (cas courant de la page de connexion Microsoft) ne passe jamais par cette
# propriete, seul le contenu de l'element change reellement. add_init_script
# s'execute avant tout script de la page chargee, contrairement a un simple
# page.evaluate() ponctuel qui ne survivrait pas a une navigation.
#
# Non verifie contre le vrai site (hors de portee de cette tache : aucune
# connexion reelle a monPortail). Limite connue et acceptee : une page qui
# afficherait un titre sans jamais passer par l'element <title> du document
# (mecanisme non standard) contournerait ce prefixe.
SCRIPT_PREFIXE_TITRE = (
    """
(() => {
  const prefixe = %r;
  function appliquer(element) {
    if (!element.textContent.startsWith(prefixe)) {
      element.textContent = prefixe + element.textContent;
    }
  }
  function surveiller() {
    const existant = document.querySelector("title");
    if (existant) {
      appliquer(existant);
      new MutationObserver(() => appliquer(existant)).observe(existant, {
        childList: true,
        characterData: true,
        subtree: true,
      });
      return;
    }
    const attenteApparition = new MutationObserver(() => {
      const element = document.querySelector("title");
      if (!element) { return; }
      attenteApparition.disconnect();
      appliquer(element);
      new MutationObserver(() => appliquer(element)).observe(element, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    });
    attenteApparition.observe(document.documentElement || document, {
      childList: true,
      subtree: true,
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", surveiller, { once: true });
  } else {
    surveiller();
  }
})();
"""
    % (PREFIXE_TITRE_FENETRE_PILOTEE,)
)


def _est_hote_ulaval(hote: str) -> bool:
    """Vrai si `hote` est ulaval.ca ou un de ses sous-domaines."""
    return hote == DOMAINE_ULAVAL or hote.endswith("." + DOMAINE_ULAVAL)


def _est_hote_connexion_microsoft(hote: str) -> bool:
    """Vrai si `hote` appartient a un domaine de connexion federee Microsoft."""
    return any(
        hote == suffixe or hote.endswith("." + suffixe) for suffixe in HOTES_CONNEXION_MICROSOFT
    )


def _decrire_page_ouverte(hote, titre) -> str:
    """Description d'une page pour la ligne d'etat d'attendre_connexion.

    Le titre seul ("Sign in to your account" contre "Cours - monPortail")
    rend un ecart visible d'un coup d'oeil, la ou deux noms de domaine ne
    disaient rien. `titre` peut etre None (page pas encore chargee, ou
    lecture du titre inaccessible pendant une navigation) : on retombe alors
    sur le seul nom d'hote, sans faire disparaitre la page de la ligne
    d'etat pour autant.
    """
    if hote is None:
        return "page pas encore chargee"
    if titre:
        return f"{hote} [{titre}]"
    return hote


def est_page_authentifiee(page) -> bool:
    """Vrai si `page` est une page authentifiee de monPortail.

    Seul point du projet qui decide de ce qu'est une page authentifiee :
    reutilise par SessionNavigateur.est_connecte et par extracteur.ena, qui
    verifie ainsi qu'une session n'a pas expire en cours de navigation.

    Ancienne version : verifiait le nom d'hote exact (sitescours.monportail.
    ulaval.ca), puis un marqueur de contenu (lien /ena/site/, texte "Cours
    suivis", etc.) propre a une page connectee. Trois correctifs successifs
    sur ce marqueur n'ont pas suffi en usage reel -- une authentification
    federee n'atterrit pas toujours au meme endroit, et le marqueur choisi
    n'y est alors pas forcement present, quelle que soit la page. Le critere
    est donc assoupli au strict necessaire : on considere authentifiee toute
    page dont l'hote appartient au domaine ulaval.ca et qui n'est pas un hote
    de connexion federee (voir HOTES_CONNEXION_MICROSOFT) ; plus aucun
    marqueur de contenu n'est exige.

    Etre plus permissif ici ne fait courir aucun risque de perte silencieuse :
    la garantie s'est deplacee en aval. Si la page adoptee n'est pas la bonne
    (mauvaise page du domaine), le selecteur de sessions (Ena.
    _ouvrir_selecteur_sessions / sessions_disponibles) ne trouvera rien
    d'exploitable et l'outil echoue bruyamment, plutot que de rendre une
    liste vide en silence.

    Un simple test par sous-chaine sur l'URL complete resterait insuffisant :
    l'URL d'autorisation Microsoft place le redirect_uri en clair dans ses
    parametres (l'encodage pour cent ne touche que ':' et '/'), donc
    "sitescours.monportail.ulaval.ca" y apparait avant toute connexion. On
    verifie donc le nom d'hote seul (urlparse), jamais l'URL entiere.
    """
    hote = urlparse(page.url).hostname
    if hote is None:
        return False
    hote = hote.lower()
    return _est_hote_ulaval(hote) and not _est_hote_connexion_microsoft(hote)


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


# Prefixe des dossiers de profil temporaires : facilite leur identification
# a l'oeil dans le dossier temporaire du systeme (ex. lors d'un nettoyage
# manuel apres un plantage qui aurait empeche fermer() de s'executer).
PREFIXE_DOSSIER_PROFIL_TEMPORAIRE = "extracteur-monportail-"


class SessionNavigateur:
    """Pilote une fenetre de navigateur avec un profil temporaire, cree par
    ouvrir() et supprime par fermer() : voir la docstring du module pour le
    changement de conception qui a abandonne le profil persistant.
    """

    def __init__(self, sans_fenetre: bool = False):
        self.sans_fenetre = sans_fenetre
        self._playwright = None
        self.contexte = None
        self.page = None
        # Cree par ouvrir(), supprime par fermer() : jamais fourni par
        # l'appelant, jamais reutilise d'un lancement a l'autre.
        self.dossier_profil = None
        # Domaine observe au dernier sondage d'attendre_connexion (y compris
        # apres un echec) : sert a batir un message d'echec informatif.
        self.dernier_domaine_observe = None

    def ouvrir(self, sommeil=time.sleep) -> None:
        """Ouvre le navigateur sur un profil neuf, cree pour cette seule
        execution.

        Le dossier vit dans le dossier temporaire du systeme
        (tempfile.mkdtemp), jamais dans le depot ni dans un dossier
        synchronise : le projet vit sous OneDrive, et y ecrire un profil
        Chromium (plusieurs dizaines de megaoctets) a chaque lancement
        declencherait une synchronisation inutile en plus de remplir le
        disque au fil des executions. fermer() est responsable de le
        supprimer, garantie qui doit tenir meme sur interruption ou erreur --
        voir sa docstring.

        Affiche d'abord PAGE_IDENTIFICATION, le temps que l'utilisateur la
        remarque, avant de naviguer vers monPortail : voir la docstring de
        cette constante pour l'incident de terrain qui motive cette etape
        (une fenetre non pilotee prise a tort pour celle du programme). La
        fenetre est amenee au premier plan pendant que cette page est
        affichee -- c'est le moment ou l'on invite a se connecter.

        `sommeil` est injectable pour les tests, comme dans
        attendre_connexion : aucun test ne doit faire dormir la suite pour de
        vrai en attendant que l'utilisateur ait le temps de lire la page.
        """
        self.dossier_profil = Path(
            tempfile.mkdtemp(prefix=PREFIXE_DOSSIER_PROFIL_TEMPORAIRE)
        )
        self._playwright = sync_playwright().start()
        self.contexte = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.dossier_profil),
            headless=self.sans_fenetre,
            accept_downloads=True,
        )
        self.contexte.add_init_script(SCRIPT_PREFIXE_TITRE)
        self.page = self.contexte.pages[0] if self.contexte.pages else self.contexte.new_page()
        self.page.set_content(PAGE_IDENTIFICATION)
        self._amener_au_premier_plan()
        sommeil(DUREE_AFFICHAGE_PAGE_IDENTIFICATION)
        self.page.goto(URL_DEPART, wait_until="domcontentloaded")

    def _amener_au_premier_plan(self) -> None:
        """Demande le focus sur la fenetre pilotee, au moment ou l'on invite
        a se connecter.

        Page.bring_to_front() n'est pas garanti de reussir a voler le focus
        selon le gestionnaire de fenetres du systeme -- Playwright ne le
        promet pas. Non verifie sur cette plateforme (hors de portee de
        cette tache : aucune connexion reelle a monPortail, et aucun test ne
        peut ouvrir un vrai navigateur). Une erreur ici ne doit jamais
        empecher la suite : au pire, l'utilisateur doit cliquer lui-meme sur
        la fenetre, ce que le titre d'onglet distinctif et le contenu de
        PAGE_IDENTIFICATION permettent de reconnaitre sans ambiguite.
        """
        try:
            self.page.bring_to_front()
        except ErreurPlaywright:
            pass

    def _pages_du_contexte(self) -> list:
        """Pages a examiner : toutes celles connues du contexte, ou a
        defaut la seule page capturee (repli pour les doublures de test qui
        ne simulent pas de contexte).

        Rien ne garantit qu'une authentification federee aboutisse dans la
        page ou elle a commence : le contexte peut ouvrir un nouvel onglet,
        remplacer la page, ou faire d'une popup la page principale. On
        n'interroge donc jamais une seule reference figee, mais toutes les
        pages que le contexte connait au moment du sondage.
        """
        if self.contexte is not None:
            return list(self.contexte.pages)
        if self.page is not None:
            return [self.page]
        return []

    def _trouver_page_authentifiee(self):
        """Cherche, parmi toutes les pages ouvertes, celle qui est
        authentifiee.

        Une page peut etre fermee ou en pleine navigation au moment du
        sondage : ni l'une ni l'autre ne doit interrompre l'examen des
        autres pages. Une erreur de navigation est un etat normal pendant
        une authentification federee (voir attendre_connexion), jamais une
        raison d'abandonner l'examen des pages restantes.
        """
        for page in self._pages_du_contexte():
            try:
                if page.is_closed():
                    continue
                if est_page_authentifiee(page):
                    return page
            except ErreurPlaywright:
                continue
        return None

    def _informations_pages_ouvertes(self) -> list:
        """Nom d'hote et titre de chaque page actuellement ouverte et
        exploitable : un couple (hote, titre) par page.

        `hote` est None pour une page ouverte mais pas encore chargee (about:
        blank), comme dans l'ancienne version de cette methode qui ne portait
        que le nom d'hote. Une page fermee, ou dont meme le nom d'hote est
        illisible (navigation en cours), est omise sans interrompre l'examen
        des autres -- meme garantie que _trouver_page_authentifiee.

        `titre` est lu separement, et vaut None des que page.title() echoue :
        une page en pleine navigation peut repondre a l'un et pas a l'autre.
        Cet echec ne doit jamais faire disparaitre la page elle-meme de la
        ligne d'etat, seulement le titre affiche a cote de son nom d'hote.
        """
        informations = []
        for page in self._pages_du_contexte():
            try:
                if page.is_closed():
                    continue
                hote = urlparse(page.url).hostname
            except ErreurPlaywright:
                continue
            try:
                titre = page.title()
            except ErreurPlaywright:
                titre = None
            informations.append((hote, titre))
        return informations

    def _toutes_pages_fermees(self) -> bool:
        """Vrai si plus aucune page exploitable ne subsiste (contexte
        ferme, ou chaque page connue est fermee). Seul ce constat doit
        mettre fin a attendre_connexion avant l'echeance du delai."""
        if self.contexte is not None and self.contexte.is_closed():
            return True
        pages = self._pages_du_contexte()
        if not pages:
            return True
        for page in pages:
            try:
                if not page.is_closed():
                    return False
            except ErreurPlaywright:
                continue
        return True

    def est_connecte(self) -> bool:
        """Vrai si une des pages ouvertes est authentifiee.

        Des qu'une page authentifiee est trouvee, elle devient la page de
        travail (`self.page`) : toute la navigation ulterieure (extracteur
        et ena.py, qui lisent tous deux `session.page` sans jamais en
        garder de copie figee) se propage donc automatiquement sur la bonne
        page, plutot que sur celle capturee a l'ouverture du navigateur.

        Le critere d'authentification (est_page_authentifiee) ne verifie
        plus que le nom d'hote : la page adoptee peut donc se trouver
        n'importe ou sur le domaine ulaval.ca (accueil, mon-compte, etc.),
        pas forcement sur la page des cours. On n'a plus le choix de
        supposer qu'elle y est deja -- on y navigue nous-memes.
        """
        page = self._trouver_page_authentifiee()
        if page is None:
            return False
        self.page = page
        self.page.goto(URL_DEPART, wait_until="domcontentloaded")
        return True

    def attendre_connexion(
        self,
        delai: int = DELAI_ATTENTE_CONNEXION,
        imprimer=print,
        horloge=time.time,
        sommeil=time.sleep,
        annulation=None,
    ) -> bool:
        """Attend que l'utilisateur ait termine sa connexion, MFA compris.

        Ne reste jamais muette : une ligne d'etat toutes les
        INTERVALLE_STATUT_ATTENTE secondes dit depuis combien de temps on
        attend et sur quels domaines se trouvent les pages actuellement
        ouvertes, pour que l'utilisateur distingue une attente normale d'un
        programme bloque. Cette ligne rend compte de tout ce qui est
        reellement observe -- toutes les pages du contexte, pas une seule
        reference figee -- car rien ne garantit qu'une authentification
        federee aboutisse dans la page ou elle a commence (nouvel onglet,
        page remplacee, popup devenue page principale). Si une page
        s'attarde sur le domaine de connexion Microsoft, un rappel invite a
        choisir le bon compte parmi ceux que propose le selecteur de comptes
        -- un blocage que l'utilisateur ne peut pas deviner de lui-meme.

        `dernier_domaine_observe` est pose a chaque sondage (y compris en cas
        d'echec final) : l'appelant s'en sert pour batir un message d'echec
        qui dit ou la page en etait, plutot qu'un simple delai ecoule. Il
        retient le premier domaine connu parmi les pages ouvertes (souvent
        la seule page en usage normal).

        `imprimer`, `horloge` et `sommeil` sont injectables pour les tests :
        aucun test ne doit faire dormir la suite pour de vrai.

        `annulation`, quand fourni, est un threading.Event verifie a chaque
        sondage (toutes les 2 secondes) : le poser fait rendre False
        immediatement, sans attendre l'echeance du delai. Sert l'interface
        graphique, dont le bouton d'interruption doit pouvoir ecourter une
        attente de connexion qui peut durer jusqu'a cinq minutes -- sans
        jamais toucher aux objets Playwright depuis un autre fil que celui
        qui les a crees.
        """
        self.dernier_domaine_observe = None
        debut = horloge()
        limite = debut + delai
        prochain_statut = debut + INTERVALLE_STATUT_ATTENTE
        debut_sur_microsoft = None
        rappel_selecteur_affiche = False

        while True:
            if annulation is not None and annulation.is_set():
                return False

            maintenant = horloge()
            if maintenant >= limite:
                break

            try:
                if self.est_connecte():
                    return True
            except ErreurPlaywright:
                # ErreurPlaywright est la classe d'erreur generale de
                # Playwright. est_connecte() n'en leve plus normalement --
                # elle absorbe deja les erreurs de navigation page par page
                # -- mais les doublures de test historiques la simulent
                # encore pour figer le comportement documente ci-dessous :
                # confondre une fenetre reellement fermee avec une erreur
                # transitoire de navigation ("Execution context was
                # destroyed, most likely because of a navigation", courante
                # et normale en pleine chaine de redirections OAuth) ferait
                # conclure a tort a une fermeture. On absorbe donc ici aussi
                # et on tranche juste apres sur l'etat reel des pages.
                pass

            # Seule la disparition de toutes les pages met fin a l'attente
            # avant l'echeance du delai : une page individuelle fermee ou en
            # echec d'examen ne doit jamais suffire, d'autres pages du
            # contexte peuvent porter la session authentifiee.
            if self._toutes_pages_fermees():
                return False

            informations = self._informations_pages_ouvertes()
            hotes = [hote for hote, _titre in informations]
            domaines_connus = [nom for nom in hotes if nom is not None]
            self.dernier_domaine_observe = domaines_connus[0] if domaines_connus else None

            sur_microsoft = any(FRAGMENT_HOTE_MICROSOFT in nom for nom in domaines_connus)
            if sur_microsoft:
                if debut_sur_microsoft is None:
                    debut_sur_microsoft = maintenant
            else:
                debut_sur_microsoft = None
                rappel_selecteur_affiche = False

            if maintenant >= prochain_statut:
                ecoule = int(maintenant - debut)
                restant = int(limite - maintenant)
                if not informations:
                    imprimer(f"en attente ({ecoule}s) - page pas encore chargee")
                else:
                    descriptif = ", ".join(
                        _decrire_page_ouverte(hote, titre) for hote, titre in informations
                    )
                    imprimer(
                        f"en attente ({ecoule}s) - {len(informations)} page(s) ouverte(s) : "
                        f"{descriptif}"
                    )

                if (
                    sur_microsoft
                    and not rappel_selecteur_affiche
                    and (maintenant - debut_sur_microsoft) >= SEUIL_RAPPEL_SELECTEUR_COMPTE
                ):
                    imprimer(
                        "Si un selecteur de compte est affiche, plusieurs comptes "
                        "peuvent etre proposes : choisissez celui de l'Universite Laval."
                    )
                    rappel_selecteur_affiche = True

                if restant <= SEUIL_FIN_IMMINENTE:
                    imprimer(f"encore environ {restant}s avant l'abandon de l'attente")

                prochain_statut += INTERVALLE_STATUT_ATTENTE

            sommeil(2)
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
        """Ferme le navigateur, puis supprime le profil temporaire cree par
        ouvrir().

        Chaque etape s'execute meme si la precedente a leve une exception :
        fermer() est deja appelee depuis un try/finally partout dans le
        projet (voir _connecter, _lister, _un_seul_cours, etc., dans
        __main__.py), y compris sur interruption clavier, session expiree ou
        erreur inattendue. C'est cette meme garantie qui doit couvrir la
        suppression du profil temporaire -- sinon un plantage en cours de
        route laisserait un dossier de plusieurs dizaines de megaoctets
        derriere lui, lancement apres lancement.

        La suppression ignore ses propres erreurs (fichier verrouille par un
        processus Chromium pas encore tout a fait termine, par exemple) :
        mieux vaut un profil orphelin occasionnel qu'une exception de
        nettoyage qui remplacerait, ou masquerait, l'erreur reelle que
        fermer() est en train de nettoyer derriere.
        """
        try:
            if self.contexte is not None:
                self.contexte.close()
        finally:
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            finally:
                if self.dossier_profil is not None:
                    dossier = self.dossier_profil
                    self.dossier_profil = None
                    shutil.rmtree(dossier, ignore_errors=True)
