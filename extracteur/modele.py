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
