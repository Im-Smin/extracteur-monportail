"""Orchestration de l'archivage.

Deux promesses tenues ici : un cours qui casse n'emporte jamais les autres, et
une session expiree met la file en pause plutot que de bruler les cours
restants en erreurs d'authentification.
"""

from pathlib import Path

from playwright.sync_api import Error as ErreurPlaywright

from extracteur.manifeste import Manifeste, ecrire_depots, ecrire_notes, ecrire_notes_consolidees
from extracteur.modele import Echec, Fichier, Resultat
from extracteur.nommage import nom_sur, nom_unique, tronquer
from extracteur.stockage import deja_present
from extracteur.telechargement import ErreurPermanente, SessionExpiree, telecharger

NOM_PLAN_DE_COURS = "plan-de-cours.pdf"


def _segment(texte: str) -> str:
    return tronquer(nom_sur(texte))


def _taille_manifeste(entree: dict) -> int | None:
    """Parse la colonne taille d'une entree de manifeste, ou None si illisible.

    Le manifeste est toujours ecrit par Manifeste.ajouter en usage normal,
    mais reste un fichier CSV ouvrable et modifiable a la main. Une colonne
    vide ou non numerique ne doit pas faire planter la reprise : elle est
    traitee comme une taille inconnue, au meme titre qu'une absence de
    verification de taille (voir stockage.deja_present).
    """
    try:
        return int(entree["taille"])
    except (KeyError, TypeError, ValueError):
        return None


class Archiveur:
    def __init__(self, ena, transport, racine: Path, evenements):
        self.ena = ena
        self.transport = transport
        self.racine = Path(racine)
        self.evenements = evenements
        self.manifeste = Manifeste(self.racine)
        self.resultat = Resultat()

    def _emettre(self, type_evenement: str, texte: str) -> None:
        self.evenements.put((type_evenement, texte))

    def chemin_du_cours(self, cours) -> Path:
        return self.racine / _segment(cours.session.dossier()) / _segment(cours.dossier())

    def _relatif(self, destination: Path) -> str:
        return str(destination.relative_to(self.racine)).replace("\\", "/")

    def archiver(self, cours_choisis) -> Resultat:
        notes_consolidees: list[tuple[str, str, object]] = []

        for cours in cours_choisis:
            self._emettre("cours", cours.dossier())
            try:
                self._archiver_un_cours(cours, notes_consolidees)
            except SessionExpiree:
                self._emettre("pause", "Session expiree - reconnectez-vous puis reprenez.")
                raise
            except Exception as erreur:  # isolation stricte par cours
                self.resultat.echecs.append(
                    Echec(cours=cours.dossier(), element="(cours entier)", cause=str(erreur))
                )
                self._emettre("echec", f"{cours.dossier()} : {erreur}")

        ecrire_notes_consolidees(self.racine / "notes-tous-cours.csv", notes_consolidees)
        self._emettre("fin", f"{self.resultat.fichiers_ecrits} fichiers archives")
        return self.resultat

    def _archiver_un_cours(self, cours, notes_consolidees) -> None:
        base = self.chemin_du_cours(cours)

        self._archiver_plan_de_cours(cours, base)

        modules = self.ena.modules(cours)
        for module in modules:
            dossier = base / "Documents" / _segment(module.titre or module.id_module)
            for fichier in self.ena.fichiers_du_module(module):
                self._recuperer(cours, fichier, dossier)

            cible_pdf = base / "Pages" / _segment(f"{module.titre or module.id_module}.pdf")
            self._capturer(cours, module, cible_pdf)

        evaluations = self.ena.evaluations(cours)
        for evaluation in evaluations:
            dossier = base / "Mes dépôts" / _segment(evaluation.titre or evaluation.id_evaluation)
            depots = self.ena.fichiers_de_depot(evaluation)
            for depot in depots:
                self._recuperer(cours, Fichier(nom=depot.nom, url=depot.url), dossier)
            if depots:
                dossier.mkdir(parents=True, exist_ok=True)
                ecrire_depots(dossier / "depots.csv", depots)

        notes = self.ena.resultats(cours)
        if notes:
            ecrire_notes(base / "notes.csv", notes)
            for note in notes:
                notes_consolidees.append((cours.session.dossier(), cours.dossier(), note))

        # Repli. Certains sites n'ont ni modules ni evaluations : leur contenu
        # n'est atteignable qu'en cliquant les entrees de leur propre menu. Sans
        # cette branche, ces cours produiraient un dossier vide.
        if not modules and not evaluations:
            self._parcourir_le_menu(cours, base)

    def _archiver_plan_de_cours(self, cours, base: Path) -> None:
        """Telecharge le PDF officiel s'il est connu, sinon imprime la page en
        filet de secours.

        L'url_plan_de_cours vient directement de /portail/cours, une source de
        confiance : contrairement aux liens du contenu, on ne la filtre pas
        par est_interne avant de la suivre.
        """
        dossier = base / "Plan de cours"

        if cours.url_plan_de_cours:
            self._telecharger(cours, NOM_PLAN_DE_COURS, cours.url_plan_de_cours, dossier, desambiguer=False)
            return

        destination = dossier / NOM_PLAN_DE_COURS
        if destination.exists():
            return
        try:
            self.ena.capturer_plan_de_cours(cours, destination)
        # Capture isolee : navigation Playwright ou ecriture disque du PDF.
        # Une impression manquee ne doit couter que ce fichier, jamais le cours.
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=NOM_PLAN_DE_COURS, cause=str(erreur))
            )

    def _parcourir_le_menu(self, cours, base: Path) -> None:
        from extracteur.extraction import fichiers_depuis_html

        def traiter(libelle, html):
            dossier = base / "Documents" / _segment(libelle)
            for fichier in fichiers_depuis_html(html):
                self._recuperer(cours, fichier, dossier)

            cible = base / "Pages" / _segment(f"{libelle}.pdf")
            self._capturer_page_courante(cours, cible)

        self.ena.parcourir_menu(cours, traiter)

    def _capturer_page_courante(self, cours, destination: Path) -> None:
        """Imprime la page telle qu'elle est deja affichee, sans renaviguer.

        Le repli par le menu n'a pas d'identifiant de module ou d'evaluation
        vers lequel renaviguer : la seule capture possible est celle de l'etat
        courant du navigateur, deja porte par ena.session.page.

        Isolee comme _capturer : ena.parcourir_menu n'intercepte que le clic,
        pas l'appel a cette fonction de traitement. Sans cette isolation, une
        impression ratee pour une section fait echouer tout le cours en un
        seul Echec generique, et perd en silence le contenu recuperable des
        sections suivantes.
        """
        if destination.exists():
            return
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.ena.session.page.pdf(path=str(destination), format="A4", print_background=True)
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _capturer(self, cours, module, destination: Path) -> None:
        if destination.exists():
            return
        try:
            from extracteur.ena import URL

            self.ena.capturer_pdf(URL.module(module.id_site, module.id_module), destination)
        # Meme isolation que pour le plan de cours : une capture de page ratee
        # ne doit pas emporter les autres modules ni le reste du cours.
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _recuperer(self, cours, fichier, dossier: Path) -> None:
        if not fichier.est_interne():
            return

        nom = tronquer(nom_sur(fichier.nom))
        self._telecharger(cours, nom, fichier.url, dossier)

    def _telecharger(self, cours, nom: str, url: str, dossier: Path, desambiguer: bool = True) -> None:
        dossier.mkdir(parents=True, exist_ok=True)

        # Reprise. La cle est l'url, stable d'une execution a l'autre : le nom
        # sur disque, lui, peut changer a chaque execution des qu'il y a une
        # collision, a cause de la desambiguisation. Interroger le manifeste
        # par le chemin AVANT desambiguisation faisait echouer la comparaison
        # d'url des le deuxieme fichier en collision, provoquant un nouveau
        # suffixe a chaque relance.
        entree = self.manifeste.par_url(url)
        if entree is not None:
            destination_connue = self.racine / entree["chemin"]
            if deja_present(destination_connue, _taille_manifeste(entree)):
                self.resultat.fichiers_sautes += 1
                self._emettre("saute", nom)
                return

        destination = dossier / nom
        # Collision reelle : deux ressources distinctes portent le meme nom.
        if desambiguer and destination.exists():
            nom = nom_unique(dossier, nom)
            destination = dossier / nom
        relatif = self._relatif(destination)

        try:
            taille, empreinte = telecharger(self.transport, url, destination)
        except SessionExpiree:
            raise
        except ErreurPermanente as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=nom, cause=str(erreur), url=url)
            )
            self._emettre("echec", f"{nom} : {erreur}")
            return

        self.manifeste.ajouter(relatif, taille, empreinte, url)
        self.resultat.fichiers_ecrits += 1
        self._emettre("fichier", nom)

    # Note sur le debit : l'archiveur telecharge en serie, donc un seul transfert
    # a la fois. La spec plafonne a trois simultanes ; rester en dessous est
    # conforme, et evite toute limitation de debit cote Universite.
