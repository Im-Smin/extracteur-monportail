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
class Evaluation:
    id_site: str
    id_evaluation: str
    titre: str


@dataclass(frozen=True)
class Note:
    evaluation: str
    note: str = ""
    sur: str = ""
    ponderation: str = ""
    moyenne_groupe: str = ""


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
