"""HTML vers objets du modele. Aucune dependance navigateur, donc testable.

Les liens de fichiers de monPortail passent tous par un traceur d'analytique
dont le parametre `url` contient, en double encodage, l'URL reelle. On la decode
pour telecharger a la source : cela evite d'alimenter les statistiques de
consultation et donne le vrai nom de fichier, que le texte du lien tronque.
"""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from extracteur.modele import Cours, Fichier, Module, Note, Session

# Deux traceurs d'analytique observes : les fichiers de module, et le plan de
# cours (releve sur /portail/cours). Les deux encodent l'URL reelle dans le
# meme parametre `url`, decodee identiquement plus bas.
PREFIXES_TRACEUR = ("/analytique/evenement/fichier", "/analytique/evenement/plancours")
PREFIXE_CONTENU = "/contenu/sitescours/"


def _soupe(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _est_traceur(href: str) -> bool:
    """Vrai si le chemin (domaine ignore) commence par un des traceurs connus.

    Le lien du plan de cours sur /portail/cours porte le domaine complet
    (https://sitescours.monportail.ulaval.ca/analytique/...), alors que les
    liens de fichiers de module sont relatifs. Un simple `str.startswith` sur
    le href entier rate donc systematiquement la forme absolue.
    """
    return urlsplit(href).path.startswith(PREFIXES_TRACEUR)


def url_reelle(href: str) -> str | None:
    """Extrait l'URL de contenu d'un href, ou None si ce n'est pas une ressource interne."""
    if not href or href.startswith("#"):
        return None

    if _est_traceur(href):
        # Extraire manuellement le parametre url du query string pour eviter que
        # parse_qs ne pre-decodifie la valeur via unquote_plus. On besoin un seul
        # niveau de decodage. Le parametre porte parfois un chemin relatif
        # (fichiers de module), parfois une URL absolue (plan de cours) : les
        # deux se decodent de la meme facon.
        match = re.search(r"[?&]url=([^&]+)", href)
        if not match:
            return None
        encoded_url = match.group(1)
        # Un seul unquote : le second niveau d'encodage est celui de l'URL elle-meme.
        return unquote(encoded_url)

    if PREFIXE_CONTENU in href:
        return href

    return None


def nom_depuis_url(url: str) -> str:
    """Nom de fichier lisible, tire du chemin et non du texte du lien."""
    chemin = urlsplit(url).path
    return unquote(chemin.rsplit("/", 1)[-1])


def est_commande_adf(identifiant: str | None) -> bool:
    """Vrai si l'element est une commande ADF, donc potentiellement une ecriture.

    Le menu Plan de cours contient `cmdObtenirPlanCours`, qui publie une nouvelle
    version au lieu de telecharger. On ne suit jamais ces elements.
    """
    return bool(identifiant) and "cmd" in identifiant


def modules_depuis_html(html: str, id_site: str) -> list[Module]:
    modules: dict[str, Module] = {}

    for lien in _soupe(html).find_all("a", href=True):
        href = lien["href"]
        if "idModule=" not in href:
            continue

        parametres = parse_qs(urlsplit(href).query)
        id_module = parametres.get("idModule", [""])[0]
        if not id_module:
            continue

        titre = lien.get_text(strip=True)
        existant = modules.get(id_module)
        # Un module apparait souvent deux fois : une icone sans texte, puis le titre.
        if existant is None:
            modules[id_module] = Module(
                id_site=id_site, id_module=id_module, titre=titre, rang=len(modules)
            )
        elif titre and not existant.titre:
            modules[id_module] = Module(
                id_site=id_site,
                id_module=id_module,
                titre=titre,
                rang=existant.rang,
            )

    return list(modules.values())


def fichiers_depuis_html(html: str) -> list[Fichier]:
    soupe = _soupe(html)
    fichiers: dict[str, Fichier] = {}

    # Premiere passe : collecter les URLs des commandes ADF
    urls_adf: set[str] = set()
    for lien in soupe.find_all("a", href=True):
        if est_commande_adf(lien.get("id")):
            url = url_reelle(lien["href"])
            if url:
                urls_adf.add(url)

    # Deuxieme passe : collecter les fichiers, en ignorant les URLs ADF
    for lien in soupe.find_all("a", href=True):
        url = url_reelle(lien["href"])
        if url is None or url in urls_adf:
            continue

        if url not in fichiers:
            fichiers[url] = Fichier(nom=nom_depuis_url(url), url=url)

    return list(fichiers.values())


def _scinder_note(texte: str) -> tuple[str, str]:
    """'18 / 20' devient ('18', '20'). Un texte libre reste entier."""
    if "/" in texte:
        gauche, droite = texte.split("/", 1)
        return gauche.strip(), droite.strip()
    return texte.strip(), ""


MOTIF_SIGLE = re.compile(r"^([A-Z]{3}-\d{4})\s*:\s*(.+)$")

# Correspondance saison -> mois pour construire le code AAAASS attendu par
# Session, a partir du libelle textuel affiche par le selecteur de sessions.
MOIS_SAISON = {"Hiver": "01", "Été": "05", "Automne": "09"}

# Code rendu quand le libelle ne correspond a aucune saison connue. Explicite
# plutot que silencieux : un code invente (ex. tronque a "0000") passerait
# inapercu et fausserait le tri chronologique de l'archive.
CODE_SESSION_INCONNUE = "INCONNU"


def session_depuis_libelle(libelle: str) -> Session:
    """Convertit un libelle du selecteur ('Automne 2022') en Session.

    Fonction pure, testable sans navigateur : le selecteur de sessions ne
    rend que du texte, jamais de code machine. Un libelle inattendu (nouvelle
    saison, format different) ne doit jamais faire planter l'extraction : on
    rend un code explicitement marque comme inconnu plutot que de deviner.
    """
    mots = libelle.split()
    if len(mots) == 2:
        saison, annee = mots
        mois = MOIS_SAISON.get(saison)
        if mois and annee.isdigit():
            return Session(code=f"{annee}{mois}", libelle=libelle)

    return Session(code=CODE_SESSION_INCONNUE, libelle=libelle)


def _id_site_depuis_href(href: str, marqueur: str) -> str | None:
    """Id de site porte par un href contenant `marqueur` (ex. /ena/site/accueil)."""
    if marqueur not in href or "idSite=" not in href:
        return None
    id_site = parse_qs(urlsplit(href).query).get("idSite", [""])[0]
    return id_site or None


def cours_depuis_html(html: str, session: Session) -> list[Cours]:
    """Cours listes sur /portail/cours pour une session, avec les liens
    directs vers le plan de cours et le sommaire des resultats quand la page
    les porte. Ces liens peuvent apparaitre avant ou apres le lien du site
    dans le DOM : on les collecte d'abord, independamment de l'ordre.
    """
    soupe = _soupe(html)
    liens = soupe.find_all("a", href=True)

    plans: dict[str, str] = {}
    resultats: dict[str, str] = {}
    for lien in liens:
        href = lien["href"]

        id_resultats = _id_site_depuis_href(href, "/ena/site/resultats")
        if id_resultats:
            resultats[id_resultats] = href
            continue

        if _est_traceur(href):
            id_plan = parse_qs(urlsplit(href).query).get("idSite", [""])[0]
            url = url_reelle(href)
            if id_plan and url:
                plans[id_plan] = url

    trouves: dict[str, Cours] = {}
    for lien in liens:
        href = lien["href"]
        id_site = _id_site_depuis_href(href, "/ena/site/accueil")
        if not id_site:
            continue

        texte = lien.get_text(strip=True)
        correspondance = MOTIF_SIGLE.match(texte)
        sigle = correspondance.group(1) if correspondance else None
        titre = correspondance.group(2) if correspondance else texte

        existant = trouves.get(id_site)
        if existant is None or (titre and not existant.titre):
            trouves[id_site] = Cours(
                id_site=id_site,
                sigle=sigle,
                titre=titre,
                session=session,
                url_plan_de_cours=plans.get(id_site),
                url_resultats=resultats.get(id_site),
            )

    return list(trouves.values())


# Marqueur textuel accolle au titre d'une ligne de regroupement, jamais suivi
# d'un pourcentage obtenu (colonne 2 vide) contrairement a une evaluation.
MARQUEUR_REGROUPEMENT = "(Somme des évaluations de ce regroupement)"


def resultats_depuis_html(html: str) -> list[Note]:
    """Notes du Sommaire des resultats (/ena/site/resultats?idSite=<id>).

    Quatre colonnes : titre en lien, pourcentage obtenu, ponderation, points
    obtenus sur points possibles. Trois formes de lignes cohabitent dans le
    meme tableau :
    - evaluation : les quatre colonnes sont remplies ;
    - regroupement : titre marque par MARQUEUR_REGROUPEMENT, pas de
      pourcentage en colonne 2 ;
    - total final, en fin de tableau, sans titre.
    La derniere colonne (points/possibles) est le seul repere fiable : les
    autres varient en nombre de cellules selon la forme de la ligne (colspan
    sur la ligne de total).
    """
    notes: list[Note] = []

    for tableau in _soupe(html).find_all("table"):
        for ligne in tableau.find_all("tr"):
            cellules = [c.get_text(" ", strip=True) for c in ligne.find_all("td")]
            if not cellules:
                continue  # ligne d'entete, cellules th uniquement

            texte_points = cellules[-1]
            if "/" not in texte_points:
                continue  # pas une ligne de resultat (texte de politique, etc.)

            note, sur = _scinder_note(texte_points)
            titre = cellules[0] if len(cellules) >= 4 else ""
            pourcentage = cellules[1] if len(cellules) >= 4 else ""
            ponderation = cellules[2] if len(cellules) >= 4 else ""

            est_regroupement = MARQUEUR_REGROUPEMENT in titre
            if est_regroupement:
                titre = titre.split(MARQUEUR_REGROUPEMENT, 1)[0].strip()

            notes.append(
                Note(
                    evaluation=titre,
                    pourcentage=pourcentage,
                    ponderation=ponderation,
                    note=note,
                    sur=sur,
                    est_regroupement=est_regroupement,
                )
            )

    return notes


# Le menu global de monPortail est present sur toutes les pages de site de
# cours. Ces libelles n'appartiennent pas au site et ne doivent jamais etre
# parcourus. Releves en phase 0 sur trois sites.
LIBELLES_HORS_SITE = frozenset(
    {
        "menu", "Profil", "Tableau de bord", "Études", "Admission",
        "Inscription aux cours", "Accommodement", "Cours", "Sites de cours Brio",
        "Cheminement", "Appréciation de l'enseignement", "Relevé de notes",
        "Collation des grades", "Documents officiels", "Carte d'identité",
        "Attestation d'inscription", "Documents légaux en renouvellement",
        "Emplois et stages", "Profil professionnel", "Grille de stages",
        "Services", "Application monPortail", "Impression",
        "Laissez-passer universitaire", "Séjour mobilité", "Finances",
        "Bourses Perspective Québec", "Droits de scolarité",
        "Passer au contenu", "Liste des cours", "Envoi de courriel",
        "Conditions d'utilisation", "Confidentialité", "Accessibilité",
        "Nouveautés", "Contactez-nous",
    }
)

LONGUEUR_MAX_LIBELLE = 60


def sections_du_menu(html: str) -> list[str]:
    """Libelles propres au site, pour parcourir les sections hors schema canonique.

    Le repli par le menu est indispensable : certains sites n'ont ni modules ni
    evaluations, et leurs sections ne sont atteignables que par leur libelle.
    """
    libelles: list[str] = []
    for lien in _soupe(html).find_all("a"):
        texte = lien.get_text(strip=True)
        if not texte or len(texte) >= LONGUEUR_MAX_LIBELLE:
            continue
        if texte in LIBELLES_HORS_SITE or texte in libelles:
            continue
        libelles.append(texte)
    return libelles
