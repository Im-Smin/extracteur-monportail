"""Archive a course with only the content types the user picked.

ContentArchiver reuses the original Archiveur and its safeguards (per-page,
per-module and per-course isolation, no ADF command ever clicked, resume
through the manifest). Videos are recorded by VideoAwareEna during the same
page visits, so no page is opened twice.
"""

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from extracteur.archiveur import (
    MESSAGE_COURS_HORS_MONPORTAIL,
    MESSAGE_COURS_SANS_CONTENU,
    NOM_BOITE_DE_DEPOT,
    NOM_DESCRIPTION_EVALUATION,
    NOM_PAGE_EVALUATIONS,
    NOM_PAGE_RESULTATS,
    NOM_RESULTATS_EVALUATION,
    Archiveur,
    _segment,
)
from extracteur.ena import URL
from extracteur.manifeste import ecrire_depots, ecrire_notes
from extracteur.modele import Echec, Fichier
from extracteur.telechargement import SessionExpiree

CONTENT_TYPES = [
    ("videos", "Vidéos (capsules du lecteur)"),
    ("documents", "Documents déposés par le prof (PDF, PPTX, fichiers Maya…)"),
    ("pages", "Pages des modules en PDF (consignes écrites dans la page)"),
    ("assessments", "Évaluations : énoncés, rétroactions, liste des évaluations"),
    ("submissions", "Mes travaux remis (boîte de dépôt)"),
    ("grades", "Notes (sommaire des résultats)"),
    ("outline", "Plan de cours"),
]


class ContentArchiver(Archiveur):
    def __init__(self, ena, transport, racine: Path, evenements):
        super().__init__(ena, transport, racine, evenements)
        self.types: set[str] = {key for key, _ in CONTENT_TYPES}

    def _fail(self, course, element: str, cause: str, url: str = "") -> None:
        self.resultat.echecs.append(Echec(cours=course.dossier(), element=element, cause=cause, url=url))

    def _capturer_page_courante(self, cours, destination: Path) -> None:
        if "pages" in self.types:
            super()._capturer_page_courante(cours, destination)

    def _capturer(self, cours, module, destination: Path, id_page=None) -> None:
        if "pages" in self.types:
            super()._capturer(cours, module, destination, id_page)

    def _capturer_page_section(self, cours, chemin: str, destination: Path) -> None:
        if self.types & {"pages", "assessments"}:
            super()._capturer_page_section(cours, chemin, destination)

    def _print_section(self, course, path: str, destination: Path) -> None:
        Archiveur._capturer_page_section(self, course, path, destination)

    def _archiver_page_de_module(self, cours, module, page, base: Path) -> None:
        if "documents" in self.types:
            super()._archiver_page_de_module(cours, module, page, base)
            return
        # Still visit the page: the visit is what records its videos.
        name = " - ".join([module.titre or module.id_module, *page.chemin])
        try:
            if page.id_page is None:
                self.ena.fichiers_du_module(module)
            else:
                self.ena.fichiers_du_module(module, page.id_page)
        except SessionExpiree:
            raise
        except PlaywrightError as error:
            self._fail(cours, name, str(error))
            return
        self._capturer_page_courante(cours, base / "Pages" / _segment(f"{name}.pdf"))

    def _parcourir_le_menu(self, cours, base: Path):
        from extracteur.extraction import fichiers_depuis_html, ressources_ignorees_depuis_html

        ena = self.ena

        def visit_section(label, html):
            folder = base / "Documents" / _segment(label)
            if "documents" in self.types:
                for file in fichiers_depuis_html(html):
                    self._recuperer(cours, file, folder)
                self._consigner_ignorees(cours, ressources_ignorees_depuis_html(html), folder)
            self._capturer_page_courante(cours, base / "Pages" / _segment(f"{label}.pdf"))
            if getattr(ena, "captures", None) is not None:
                ena.captures.append((html, ena.videos_on_page(html), list(ena.missing), label))

        return ena.parcourir_menu(cours, visit_section)

    def archive_course(self, course, types: set[str]) -> None:
        self.types = set(types)
        if course.url_externe:
            self._fail(course, "(cours entier)", MESSAGE_COURS_HORS_MONPORTAIL, course.url_externe)
            return

        ena = self.ena
        base = self.chemin_du_cours(course)
        recording = getattr(ena, "captures", None) is not None

        if recording:
            ena.context = None
            self._emettre("visite", "Accueil")
            try:
                ena._visiter(URL.accueil(course.id_site))
                ena._assurer_authentifie()
            except SessionExpiree:
                raise
            except PlaywrightError as error:
                self._fail(course, "Accueil", str(error))

        if "outline" in self.types:
            self._emettre("visite", "Plan de cours")
            self._archiver_plan_de_cours(course, base)

        modules = []
        if self.types & {"documents", "pages", "videos"}:
            modules = ena.modules(course)
            for module in modules:
                self._emettre("visite", f"Module : {module.titre or module.id_module}")
                self._archiver_module(course, module, base)

        assessments = []
        if self.types & {"assessments", "submissions"}:
            if recording:
                ena.context = "Évaluations"
            assessments = ena.evaluations(course)
            self._archive_assessments(course, assessments, base)

        grades = []
        if "grades" in self.types:
            self._emettre("visite", "Sommaire des résultats")
            if recording:
                ena.context = "Résultats"
            grades = ena.resultats(course)
            if grades:
                self._ecrire_synthese(course.dossier(), "notes.csv", lambda: ecrire_notes(base / "notes.csv", grades))
                self._print_section(course, URL.resultats(course.id_site), base / "Pages" / NOM_PAGE_RESULTATS)

        if not modules and not assessments and self.types & {"documents", "pages", "videos"}:
            sections = self._parcourir_le_menu(course, base)
            if not sections and not grades:
                self._fail(course, "(cours entier)", MESSAGE_COURS_SANS_CONTENU)
                self._emettre("echec", f"{course.dossier()} : aucun contenu trouvé")

    def _archive_assessments(self, course, assessments, base: Path) -> None:
        ena = self.ena
        if assessments and "assessments" in self.types:
            self._capturer_page_section(course, URL.evaluations(course.id_site), base / "Pages" / NOM_PAGE_EVALUATIONS)
        for assessment in assessments:
            name = assessment.titre or assessment.id_evaluation
            folder = base / "Évaluations" / _segment(name)
            site, assessment_id = assessment.id_site, assessment.id_evaluation
            if "assessments" in self.types:
                self._emettre("visite", f"{name} : Description")
                self._archiver_page_evaluation(course, assessment, folder, ena.fichiers_de_description,
                                               URL.evaluation(site, assessment_id), NOM_DESCRIPTION_EVALUATION)
            if "submissions" in self.types:
                self._emettre("visite", f"{name} : Boîte de dépôt")
                submitted = ena.fichiers_de_depot(assessment)
                for item in submitted:
                    self._recuperer(course, Fichier(nom=item.nom, url=item.url), folder)
                if submitted:
                    folder.mkdir(parents=True, exist_ok=True)
                    self._ecrire_synthese(course.dossier(), "depots.csv",
                                          lambda f=folder, s=submitted: ecrire_depots(f / "depots.csv", s))
                self._print_section(course, URL.boite_depot(site, assessment_id), folder / NOM_BOITE_DE_DEPOT)
            if "assessments" in self.types:
                self._archiver_page_evaluation(course, assessment, folder, ena.fichiers_de_resultats_evaluation,
                                               URL.evaluation_resultats(site, assessment_id), NOM_RESULTATS_EVALUATION)
