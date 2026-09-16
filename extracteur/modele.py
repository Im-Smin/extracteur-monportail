"""Objets echanges entre l'extraction, la navigation et l'archiveur."""

from dataclasses import dataclass, field

# Les codes de session de l'Universite Laval se terminent par le mois de debut.
RANG_SESSION = {"01": ("1", "Hiver"), "05": ("2", "Été"), "09": ("3", "Automne")}


@dataclass(frozen=True)
class Session:
    code: str  # AAAASS, par exemple "202601"
    libelle: str  # "Hiver 2026"

    def dossier(self) -> str:
        annee, suffixe = self.code[:4], self.code[4:]
        if not suffixe:
            return annee
        rang, _ = RANG_SESSION.get(suffixe, (suffixe[-1], ""))
        mots = self.libelle.split() if self.libelle else []
        nom = mots[0] if mots else ""
        return f"{annee}-{rang} {nom}".strip()


@dataclass(frozen=True)
class Cours:
    id_site: str
    sigle: str | None
    titre: str
    session: Session
    # Releves directement sur /portail/cours ; absents si la page ne les porte pas.
    url_plan_de_cours: str | None = None
    url_resultats: str | None = None

    def dossier(self) -> str:
        return f"{self.sigle} {self.titre}".strip() if self.sigle else self.titre.strip()


@dataclass(frozen=True)
class Module:
    id_site: str
    id_module: str
    titre: str
    rang: int = 0


@dataclass(frozen=True)
class Onglet:
    """Un onglet de la barre d'une page de module (voir docs/api-monportail.md,
    etape 3 : « Général », « Contenu du module », etc.).

    id_page est l'identifiant qu'il faut passer en parametre idPage de l'URL
    du module pour visiter cet onglet precis : sans lui, le serveur ADF sert
    un onglet imprevisible (le dernier consulte dans la session).
    """

    id_page: str
    titre: str
    rang: int = 0
    # Niveau dans la hierarchie d'onglets, deduit du nombre de segments du
    # _ulitemid : 1 pour un en-tete, 2 pour un sous-onglet, etc. Sert a
    # parcourir un module section par section -- finir tous les onglets de
    # l'en-tete courant avant de passer au suivant -- plutot qu'en largeur,
    # qui alterne entre les en-tetes et donne l'impression de tourner en rond.
    profondeur: int = 1


@dataclass(frozen=True)
class PageDeModule:
    """Une page reellement servie par le parcours en largeur des onglets
    d'un module (voir Ena.pages_du_module et docs/api-monportail.md, etape 3).

    chemin est la suite des titres d'onglets selectionnes menant a cette
    page, du niveau 1 vers le plus profond -- tuple vide pour un module qui
    ne porte aucune barre d'onglets (comportement d'avant l'ajout des
    onglets, conserve tel quel : une seule page, sans sous-dossier).
    id_page est None dans ce meme cas ; sinon l'identifiant de la feuille
    reellement servie, a repasser tel quel a Ena.fichiers_du_module et a
    URL.module pour revisiter exactement cette page.
    """

    id_page: str | None
    chemin: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fichier:
    nom: str
    url: str

    def est_interne(self) -> bool:
        """Vrai si la ressource est hebergee par monPortail et doit etre telechargee."""
        return "/contenu/sitescours/" in self.url


@dataclass(frozen=True)
class Depot:
    """Un document remis dans la boite de depot d'une evaluation.

    Sur un travail d'equipe, `depose_par` est parfois un coequipier et non
    l'utilisateur : c'est la seule trace de qui a remis quoi, elle doit
    survivre a l'archivage au meme titre que le fichier lui-meme.
    """

    nom: str
    url: str
    taille: str = ""
    depose_par: str = ""
    date_remise: str = ""


@dataclass(frozen=True)
class Evaluation:
    id_site: str
    id_evaluation: str
    titre: str


@dataclass(frozen=True)
class Note:
    """Une ligne du Sommaire des resultats (/ena/site/resultats?idSite=<id>).

    Les nombres restent des chaines, dans leur notation francaise d'origine
    (virgule decimale) : les convertir en flottants risquerait de fausser une
    note pour un separateur mal interprete. Il n'existe aucune moyenne de
    groupe sur cette page ; `est_regroupement` porte la seule notion reelle
    de regroupement (ex. "Examen final" qui somme plusieurs evaluations).
    """

    evaluation: str
    pourcentage: str = ""
    ponderation: str = ""
    note: str = ""
    sur: str = ""
    est_regroupement: bool = False


@dataclass
class Echec:
    cours: str
    element: str
    cause: str
    url: str = ""


@dataclass
class Resultat:
    fichiers_ecrits: int = 0
    fichiers_sautes: int = 0
    echecs: list[Echec] = field(default_factory=list)
    # Nombre de cours soumis a Archiveur.archiver() qui n'ont jamais ete
    # tentes du tout, parce qu'une SessionExpiree a interrompu la boucle
    # avant de les atteindre (le cours en cours au moment de l'interruption
    # est compte lui aussi : ni succes ni echec consigne, son sort est
    # indetermine). Zero dans tous les cas ou archiver() se termine sans
    # etre interrompu.
    cours_non_tentes: int = 0
