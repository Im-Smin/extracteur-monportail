"""Manifeste de l'archive, export des notes et rapport d'echecs."""

import csv
from datetime import datetime
from html import escape
from pathlib import Path

from extracteur.extraction import COLONNES_DEPOT

COLONNES = ["chemin", "taille", "sha256", "url", "horodatage", "statut"]
ENCODAGE_CSV = "utf-8-sig"  # Excel lit mal l'UTF-8 sans BOM.


class Manifeste:
    """Une ligne par fichier archive. Sert a la verification et a la reprise."""

    def __init__(self, racine: Path):
        self.chemin = Path(racine) / "manifeste.csv"
        self._entrees: dict[str, dict] | None = None
        self._entete_presente: bool | None = None

    def charger(self) -> dict[str, dict]:
        if self._entrees is not None:
            return self._entrees

        self._entrees = {}
        self._entete_presente = False
        if self.chemin.exists():
            with open(self.chemin, encoding=ENCODAGE_CSV, newline="") as source:
                lecteur = csv.DictReader(source)
                if lecteur.fieldnames and "chemin" in lecteur.fieldnames:
                    self._entete_presente = True
                    for ligne in lecteur:
                        self._entrees[ligne["chemin"]] = ligne
        return self._entrees

    def deja_archive(self, chemin_relatif: str) -> bool:
        return chemin_relatif in self.charger()

    def ajouter(self, chemin_relatif, taille, sha256, url, statut="ok") -> None:
        entrees = self.charger()
        nouveau = not self._entete_presente

        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        with open(self.chemin, "a", encoding=ENCODAGE_CSV, newline="") as sortie:
            redacteur = csv.DictWriter(sortie, fieldnames=COLONNES)
            if nouveau:
                redacteur.writeheader()
                self._entete_presente = True
            ligne = {
                "chemin": chemin_relatif,
                "taille": str(taille),
                "sha256": sha256,
                "url": url,
                "horodatage": datetime.now().isoformat(timespec="seconds"),
                "statut": statut,
            }
            redacteur.writerow(ligne)

        entrees[chemin_relatif] = ligne


COLONNES_NOTE = ["Évaluation", "Pourcentage", "Pondération", "Note", "Sur", "Regroupement"]


def _ligne_note(note) -> list:
    return [
        note.evaluation,
        note.pourcentage,
        note.ponderation,
        note.note,
        note.sur,
        "Oui" if note.est_regroupement else "",
    ]


def ecrire_notes(destination: Path, notes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding=ENCODAGE_CSV, newline="") as sortie:
        redacteur = csv.writer(sortie)
        redacteur.writerow(COLONNES_NOTE)
        for note in notes:
            redacteur.writerow(_ligne_note(note))


def ecrire_notes_consolidees(destination: Path, lignes) -> None:
    """Toutes les notes de tous les cours dans un seul CSV, a la racine.

    `lignes` est une suite de tuples (dossier_session, dossier_cours, Note).
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding=ENCODAGE_CSV, newline="") as sortie:
        redacteur = csv.writer(sortie)
        redacteur.writerow(["Session", "Cours"] + COLONNES_NOTE)
        for session, cours, note in lignes:
            redacteur.writerow([session, cours] + _ligne_note(note))


def ecrire_depots(destination: Path, depots) -> None:
    """CSV pose a cote des fichiers d'une boite de depot.

    Conserve les quatre colonnes de "Liste des documents deposes" : "Depose
    par" n'est pas necessairement l'utilisateur sur un travail d'equipe, et
    c'est la seule trace de qui a remis quoi. Ne touche jamais la case a
    cocher ni le bouton Supprimer : ce CSV ne fait que consigner ce que
    depots_depuis_html a deja extrait des liens de telechargement.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding=ENCODAGE_CSV, newline="") as sortie:
        redacteur = csv.writer(sortie)
        redacteur.writerow(list(COLONNES_DEPOT))
        for depot in depots:
            redacteur.writerow([depot.nom, depot.taille, depot.depose_par, depot.date_remise])


def ecrire_rapport(destination: Path, echecs, resume: dict) -> None:
    """Le document qui dit ce qu'il reste a recuperer a la main."""
    lignes = []
    for echec in echecs:
        if echec.url and (echec.url.startswith("http://") or echec.url.startswith("https://")):
            lien = f'<a href="{escape(echec.url)}">{escape(echec.url)}</a>'
        elif echec.url:
            lien = escape(echec.url)
        else:
            lien = ""
        lignes.append(
            "<tr>"
            f"<td>{escape(echec.cours)}</td>"
            f"<td>{escape(echec.element)}</td>"
            f"<td>{escape(echec.cause)}</td>"
            f"<td>{lien}</td>"
            "</tr>"
        )

    corps = (
        "<p><strong>Aucun échec.</strong> Tout le contenu visé a été récupéré.</p>"
        if not echecs
        else "<table><tr><th>Cours</th><th>Élément</th><th>Cause</th><th>URL</th></tr>"
        + "".join(lignes)
        + "</table>"
    )

    resume_html = "".join(f"<li>{escape(str(k))} : {escape(str(v))}</li>" for k, v in resume.items())

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<title>Rapport d'archivage monPortail</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:.4rem;text-align:left;font-size:.9rem}"
        "th{background:#f0f0f0}</style></head><body>"
        "<h1>Rapport d'archivage monPortail</h1>"
        f"<ul>{resume_html}</ul>"
        f"<h2>Éléments non récupérés</h2>{corps}"
        "</body></html>",
        encoding="utf-8",
    )
