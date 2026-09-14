"""HTML vers objets du modele. Aucune dependance navigateur, donc testable.

Les liens de fichiers de monPortail passent tous par un traceur d'analytique
dont le parametre `url` contient, en double encodage, l'URL reelle. On la decode
pour telecharger a la source : cela evite d'alimenter les statistiques de
consultation et donne le vrai nom de fichier, que le texte du lien tronque.
"""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from extracteur.modele import Fichier, Module, Note

PREFIXE_TRACEUR = "/analytique/evenement/fichier"
PREFIXE_CONTENU = "/contenu/sitescours/"


def _soupe(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def url_reelle(href: str) -> str | None:
    """Extrait l'URL de contenu d'un href, ou None si ce n'est pas une ressource interne."""
    if not href or href.startswith("#"):
        return None

    if href.startswith(PREFIXE_TRACEUR):
        # Extraire manuellement le parametre url du query string pour eviter que
        # parse_qs ne pre-decodifie la valeur via unquote_plus. On besoin un seul
        # niveau de decodage.
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


def resultats_depuis_html(html: str) -> list[Note]:
    notes: list[Note] = []

    for tableau in _soupe(html).find_all("table"):
        for ligne in tableau.find_all("tr"):
            cellules = [c.get_text(strip=True) for c in ligne.find_all("td")]
            if len(cellules) < 2:
                continue

            note, sur = _scinder_note(cellules[1])
            notes.append(
                Note(
                    evaluation=cellules[0],
                    note=note,
                    sur=sur,
                    ponderation=cellules[2] if len(cellules) > 2 else "",
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
