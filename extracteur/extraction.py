"""HTML vers objets du modele. Aucune dependance navigateur, donc testable.

Les liens de fichiers de monPortail passent tous par un traceur d'analytique
dont le parametre `url` contient, en double encodage, l'URL reelle. On la decode
pour telecharger a la source : cela evite d'alimenter les statistiques de
consultation et donne le vrai nom de fichier, que le texte du lien tronque.
"""

import re
from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from extracteur.modele import Cours, Depot, Fichier, Module, Note, Session

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


# En-tetes du tableau "Liste des documents deposes" d'une boite de depot.
# L'ordre des colonnes n'est pas suppose fixe (une case a cocher precede
# parfois "Nom du document") : on les localise par leur libelle.
COLONNES_DEPOT = ("Nom du document", "Taille", "Déposé par", "Date de remise")


def depots_depuis_html(html: str) -> list[Depot]:
    """Documents rendus dans la boite de depot d'une evaluation.

    DANGER : la page porte aussi une case a cocher par document et un bouton
    Supprimer, dans un formulaire. On ne rend jamais ces elements : seuls les
    liens de telechargement trouves dans la colonne "Nom du document" du
    tableau "Liste des documents deposes" sont retenus. Un clic malencontreux
    sur Supprimer detruirait un travail remis.
    """
    depots: list[Depot] = []

    for tableau in _soupe(html).find_all("table"):
        entetes = [c.get_text(strip=True) for c in tableau.find_all("th")]
        if "Nom du document" not in entetes:
            continue

        index = {colonne: entetes.index(colonne) for colonne in COLONNES_DEPOT if colonne in entetes}
        idx_nom = index["Nom du document"]

        for ligne in tableau.find_all("tr"):
            cellules = ligne.find_all("td")
            if len(cellules) <= idx_nom:
                continue

            lien = cellules[idx_nom].find("a", href=True)
            if lien is None:
                continue

            url = url_reelle(lien["href"])
            if url is None:
                continue

            def _texte(colonne: str) -> str:
                position = index.get(colonne)
                if position is None or position >= len(cellules):
                    return ""
                return cellules[position].get_text(strip=True)

            depots.append(
                Depot(
                    nom=lien.get_text(strip=True) or nom_depuis_url(url),
                    url=url,
                    taille=_texte("Taille"),
                    depose_par=_texte("Déposé par"),
                    date_remise=_texte("Date de remise"),
                )
            )

    return depots


def _scinder_note(texte: str) -> tuple[str, str]:
    """'18 / 20' devient ('18', '20'). Un texte libre reste entier."""
    if "/" in texte:
        gauche, droite = texte.split("/", 1)
        return gauche.strip(), droite.strip()
    return texte.strip(), ""


# Sigle d'un cours tel qu'affiche sur la carte (jamais dans le texte du
# lien) : "MQT-2101, NRC : 86582 (sect. H1)". Chercher "trois lettres, un
# tiret, quatre chiffres" n'importe ou dans le texte produit des faux
# positifs : une carte mentionnant "Reference dossier NRC-4567 pour ce
# cours" donnerait un sigle invente, sans la moindre alerte (releve par
# relecture). Le sigle reel est toujours immediatement suivi d'une virgule,
# generalement avant la mention NRC : on ancre dessus. Un repli tolerant
# accepte la seule virgule si la mention NRC venait a manquer.
MOTIF_SIGLE_CARTE = re.compile(r"[A-Z]{3}-\d{4}(?=\s*,\s*NRC\b)")
MOTIF_SIGLE_CARTE_REPLI = re.compile(r"[A-Z]{3}-\d{4}(?=\s*,)")

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


def _accueils_dans(ancetre, liens_accueil: list) -> list:
    """Sous-ensemble de liens_accueil physiquement contenu dans ancetre.

    Comparaison par identite (`is`), jamais par egalite structurelle : deux
    liens de site distincts peuvent avoir le meme texte, et bs4 compare les
    Tag par leur contenu, pas par identite d'objet.
    """
    presents = ancetre.find_all("a", href=True)
    return [lien for lien in liens_accueil if any(lien is present for present in presents)]


def _ids_site_distincts(liens: list) -> set[str]:
    """Identifiants de site distincts portes par une liste de liens d'accueil.

    Un meme cours porte parfois deux liens vers son propre site (icone puis
    titre, motif deja documente pour les modules -- voir
    modules_depuis_html) : compter les balises plutot que les identifiants
    ferait croire a _carte_du_lien qu'un ancetre contient deja "plus d'un"
    cours des qu'il en contient deux liens du meme, et la remontee
    s'arreterait aussitot, la carte se reduisant au lien lui-meme.
    """
    return {
        id_site
        for lien in liens
        if (id_site := _id_site_depuis_href(lien["href"], "/ena/site/accueil"))
    }


def _carte_du_lien(lien, liens_accueil: list):
    """Remonte du lien de site vers son conteneur de carte, tolerant a la
    classe CSS : le plus grand ancetre qui ne contient QUE l'identifiant de
    site de ce lien, parmi tous ceux de la page. Un ancetre qui en
    contiendrait un second fusionnerait deux cartes voisines -- on s'arrete
    juste avant. Deux liens du meme cours (icone puis titre) ne comptent que
    pour un seul identifiant : ils ne doivent jamais faire s'arreter la
    remontee prematurement.

    Si aucun ancetre distinct n'existe (les liens de site sont freres, sans
    balise propre a chacun), la carte se limite au lien lui-meme : mieux vaut
    un cours sans sigle ni plan de cours qu'un lien attribue au mauvais cours.
    """
    candidat = lien
    ancetre = lien.parent
    while ancetre is not None:
        if len(_ids_site_distincts(_accueils_dans(ancetre, liens_accueil))) != 1:
            break
        candidat = ancetre
        ancetre = ancetre.parent
    return candidat


def cours_depuis_html(html: str, session: Session) -> list[Cours]:
    """Cours listes sur /portail/cours pour une session.

    Chaque cours est rendu par une carte (article, div, li selon la
    generation de page) : le lien de site n'y porte que le titre, jamais le
    sigle -- celui-ci est un texte de la carte, sous la forme "SIGLE-0000,
    NRC : xxxxx (sect. yy)". Le lien du plan de cours et celui du sommaire
    des resultats, quand ils existent, sont eux aussi dans la carte.

    L'extraction se fait donc carte par carte, jamais par un balayage global
    de la page : associer ces liens par ordre d'apparition romprait des
    qu'un cours n'a pas de plan de cours, decalant silencieusement
    l'attribution pour tous les cours suivants.
    """
    soupe = _soupe(html)
    liens_accueil = [
        lien
        for lien in soupe.find_all("a", href=True)
        if _id_site_depuis_href(lien["href"], "/ena/site/accueil")
    ]

    trouves: dict[str, Cours] = {}
    for lien in liens_accueil:
        id_site = _id_site_depuis_href(lien["href"], "/ena/site/accueil")
        carte = _carte_du_lien(lien, liens_accueil)

        titre = lien.get_text(strip=True)
        texte_carte = carte.get_text(" ", strip=True)
        correspondance_sigle = (
            MOTIF_SIGLE_CARTE.search(texte_carte) or MOTIF_SIGLE_CARTE_REPLI.search(texte_carte)
        )
        sigle = correspondance_sigle.group(0) if correspondance_sigle else None

        url_plan_de_cours = None
        url_resultats = None
        for autre in carte.find_all("a", href=True):
            if autre is lien:
                continue
            href = autre["href"]
            if url_resultats is None and _id_site_depuis_href(href, "/ena/site/resultats"):
                url_resultats = href
            elif url_plan_de_cours is None and _est_traceur(href):
                url_plan_de_cours = url_reelle(href)

        existant = trouves.get(id_site)
        if existant is None or (titre and not existant.titre):
            trouves[id_site] = Cours(
                id_site=id_site,
                sigle=sigle,
                titre=titre,
                session=session,
                url_plan_de_cours=url_plan_de_cours,
                url_resultats=url_resultats,
            )

    return list(trouves.values())


# Marqueur textuel accolle au titre d'une ligne de regroupement, jamais suivi
# d'un pourcentage obtenu (colonne 2 vide) contrairement a une evaluation.
MARQUEUR_REGROUPEMENT = "(Somme des évaluations de ce regroupement)"

# Le tableau des notes porte cette classe explicite sur MAT-1900 (classe
# complete : "ul_table_data TableauAvecRegroupements"). La page Oracle ADF
# /ena/site/resultats en contient par ailleurs une quarantaine d'autres
# (boites de dialogue de fin de session, menus de navigation) qu'un balayage
# de tous les <table> ramasse a tort. On cible ce tableau par sa classe ;
# repli sur un balayage complet si aucune classe ne correspond (page
# modifiee) : mieux vaut risquer un faux positif occasionnel que ne plus
# jamais rien extraire.
CLASSE_TABLEAU_RESULTATS = "TableauAvecRegroupements"

# Classe de la premiere ligne d'un regroupement, releve reel sur MAT-1900.
# Confirme le marqueur textuel du titre ; sert aussi de repere si le
# marqueur venait a changer de formulation.
CLASSE_LIGNE_REGROUPEMENT = "regroupement-first"

# Note finale et cote du cours vivent hors du tableau, en texte libre sur la
# page (ex. "Note finale : 63,49 %" et "Cote : C+"). La cote est la note
# litterale officielle du cours : elle n'apparait nulle part ailleurs dans
# l'archive et vaut plus que n'importe quelle ligne du tableau.
MOTIF_NOTE_FINALE = re.compile(r"Note finale\s*:\s*([\d,]+\s*%)")
MOTIF_COTE = re.compile(r"\bCote\s*:\s*(\S+)")


def _tableau_resultats(soupe: BeautifulSoup) -> list:
    """Tableau(x) a inspecter pour en tirer les notes.

    Ne balaie jamais tous les <table> de la page en presence d'une classe
    correspondante : les boites de dialogue et menus de navigation d'Oracle
    ADF y sont trop nombreux, et rien ne garantit qu'aucun ne ressemble par
    coincidence a une ligne de resultat.
    """
    tous = soupe.find_all("table")
    cibles = [
        tableau for tableau in tous if CLASSE_TABLEAU_RESULTATS in (tableau.get("class") or [])
    ]
    return cibles or tous


def _note_finale_et_cote(soupe: BeautifulSoup) -> list[Note]:
    """Note finale et cote, en deux lignes dediees plutot qu'un champ de plus
    sur Note.

    Les deux valeurs sont deja des chaines libres, fideles a la source : la
    note finale est un pourcentage comme les autres lignes ("pourcentage"),
    et la cote EST la note officielle du cours ("note"). Reutiliser ces deux
    champs evite d'ajouter un champ a Note et de faire bouger le CSV, le
    manifeste et tout ce qui consomme le modele, pour deux valeurs qui n'ont
    besoin que d'un intitule et d'une valeur.
    """
    texte = soupe.get_text(" ", strip=True)
    extra: list[Note] = []

    correspondance_finale = MOTIF_NOTE_FINALE.search(texte)
    if correspondance_finale:
        extra.append(Note(evaluation="Note finale", pourcentage=correspondance_finale.group(1)))

    correspondance_cote = MOTIF_COTE.search(texte)
    if correspondance_cote:
        extra.append(Note(evaluation="Cote", note=correspondance_cote.group(1)))

    return extra


def resultats_depuis_html(html: str) -> list[Note]:
    """Notes du Sommaire des resultats (/ena/site/resultats?idSite=<id>).

    Quatre colonnes : titre en lien, pourcentage obtenu, ponderation, points
    obtenus sur points possibles. Trois formes de lignes cohabitent dans le
    meme tableau :
    - evaluation : les quatre colonnes sont remplies ;
    - regroupement : titre marque par MARQUEUR_REGROUPEMENT (et par la
      classe CLASSE_LIGNE_REGROUPEMENT), TROIS colonnes seulement -- il n'y
      a pas de colonne de pourcentage du tout, pas seulement une colonne
      vide ;
    - total final, en fin de tableau, sans titre.
    La derniere colonne (points/possibles) est le seul repere fiable pour
    savoir si la ligne porte un resultat ; le nombre de cellules distingue
    ensuite les trois formes (colspan sur la ligne de total).

    Note finale et cote, qui ne sont pas dans le tableau, sont ajoutees en
    fin de liste (voir _note_finale_et_cote).
    """
    soupe = _soupe(html)
    notes: list[Note] = []

    for tableau in _tableau_resultats(soupe):
        for ligne in tableau.find_all("tr"):
            cellules = [c.get_text(" ", strip=True) for c in ligne.find_all("td")]
            if not cellules:
                continue  # ligne d'entete, cellules th uniquement

            texte_points = cellules[-1]
            if "/" not in texte_points:
                continue  # pas une ligne de resultat (texte de politique, etc.)

            note, sur = _scinder_note(texte_points)

            if len(cellules) >= 4:
                titre, pourcentage, ponderation = cellules[0], cellules[1], cellules[2]
            elif len(cellules) == 3:
                # Regroupement : pas de colonne de pourcentage, la
                # ponderation occupe la position 1 et non 2.
                titre, pourcentage, ponderation = cellules[0], "", cellules[1]
            else:
                titre = pourcentage = ponderation = ""

            classes_ligne = ligne.get("class") or []
            est_regroupement = (
                MARQUEUR_REGROUPEMENT in titre or CLASSE_LIGNE_REGROUPEMENT in classes_ligne
            )
            if MARQUEUR_REGROUPEMENT in titre:
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

    notes.extend(_note_finale_et_cote(soupe))
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
