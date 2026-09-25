"""Orchestration de l'archivage.

Deux promesses tenues ici : un cours qui casse n'emporte jamais les autres, et
une session expiree met la file en pause plutot que de bruler les cours
restants en erreurs d'authentification.
"""

from pathlib import Path

from playwright.sync_api import Error as ErreurPlaywright

from extracteur.manifeste import Manifeste, consolider_notes, ecrire_depots, ecrire_notes
from extracteur.modele import Echec, Fichier, Resultat
from extracteur.nommage import nom_sur, nom_unique, tronquer
from extracteur.stockage import deja_present
from extracteur.telechargement import ErreurPermanente, SessionExpiree, telecharger

NOM_PLAN_DE_COURS = "plan-de-cours.pdf"

# Voir la fin de Archiveur._archiver_un_cours.
MESSAGE_COURS_SANS_CONTENU = (
    "aucun contenu trouve : ni module, ni evaluation, ni section de menu. Le "
    "site n'a vraisemblablement pas ete lu correctement. Relancez ce cours, "
    "puis verifiez-le a la main sur monPortail si le probleme persiste."
)

# Pages de synthese de la section Evaluations et resultats, capturees une
# seule fois par cours dans Pages/, au meme titre que les pages de module.
NOM_PAGE_EVALUATIONS = "evaluations.pdf"
NOM_PAGE_RESULTATS = "sommaire-des-resultats.pdf"

# PDF d'onglet d'une evaluation, ranges dans son propre sous-dossier sous
# Évaluations/ (voir chemin_du_cours et la boucle des evaluations) : la
# description porte parfois l'enonce d'un travail, l'onglet Resultats une
# retroaction, et la boite de depot les dates et conditions de remise.
NOM_DESCRIPTION_EVALUATION = "description.pdf"
NOM_RESULTATS_EVALUATION = "resultats.pdf"
NOM_BOITE_DE_DEPOT = "boite-de-depot.pdf"


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

    def archiver(self, cours_choisis, annulation=None) -> Resultat:
        """Archive chaque cours de la liste, un par un.

        `annulation`, quand fourni, est un threading.Event verifie a la
        frontiere de chaque cours (jamais au milieu d'un telechargement) :
        le poser interrompt la boucle proprement, comme le ferait une
        SessionExpiree, mais sans lever d'exception -- la synthese finale
        (notes-tous-cours.csv) et l'evenement "fin" sont tout de meme
        produits sur ce qui a ete accompli. Sert l'interface graphique, dont
        le bouton d'interruption ne doit jamais toucher aux objets Playwright
        depuis un autre fil que celui qui les a crees (voir la docstring de
        SessionNavigateur.attendre_connexion pour la meme contrainte).
        """
        total = len(cours_choisis)

        for indice, cours in enumerate(cours_choisis):
            if annulation is not None and annulation.is_set():
                self.resultat.cours_non_tentes = total - indice
                self._emettre("pause", "Interrompu par l'utilisateur.")
                break

            self._emettre("cours", cours.dossier())
            try:
                self._archiver_un_cours(cours)
            except SessionExpiree:
                # Le cours en cours (indice inclus) et tous ceux qui le
                # suivaient dans cette liste n'ont jamais ete tentes : ni
                # succes, ni echec consigne. Le rapport final doit pouvoir le
                # dire, sans quoi une archive tronquee semble complete.
                self.resultat.cours_non_tentes = total - indice
                self._emettre("pause", "Session expiree - reconnectez-vous puis reprenez.")
                raise
            except Exception as erreur:  # isolation stricte par cours
                self.resultat.echecs.append(
                    Echec(cours=cours.dossier(), element="(cours entier)", cause=str(erreur))
                )
                self._emettre("echec", f"{cours.dossier()} : {erreur}")

        # Reconstruction depuis le disque, pas fusion d'une liste locale a cet
        # appel : voir consolider_notes pour pourquoi c'est le fichier de
        # notes.csv par cours, deja ecrits sur disque, qui fait foi. Isole
        # comme les autres fichiers de synthese : voir _ecrire_synthese.
        self._ecrire_synthese(
            "(archive)",
            "notes-tous-cours.csv",
            lambda: consolider_notes(self.racine, self.racine / "notes-tous-cours.csv"),
        )
        self._emettre("fin", f"{self.resultat.fichiers_ecrits} fichiers archives")
        return self.resultat

    def _ecrire_synthese(self, cours_dossier: str, element: str, ecrire) -> None:
        """Isole l'ecriture d'un fichier de synthese (notes consolidees,
        notes par cours, metadonnees de depot) : ces fichiers peuvent
        toujours etre verrouilles par un tableur ouvert (Excel...), sans
        aucun rapport avec la sante du contenu deja telecharge -- parfois
        plusieurs centaines de fichiers. Un echec ici est consigne comme un
        Echec ordinaire, avec la piste la plus probable dans sa cause,
        jamais comme un plantage qui ferait perdre le benefice de tout ce
        qui a deja ete recupere.

        Isolation etroite a OSError (dont PermissionError, le cas reel
        constate) : aucune de ces ecritures ne touche le reseau, une
        SessionExpiree ne peut jamais en sortir. Une erreur d'ecriture sur un
        fichier de CONTENU (via _telecharger) ne passe pas par ici : elle
        garde son traitement d'avant, absorbee par l'isolation par cours de
        archiver() en un Echec « (cours entier) ».
        """
        try:
            ecrire()
        except OSError as erreur:
            cause = f"{erreur} (probablement ouvert dans un tableur comme Excel)"
            self.resultat.echecs.append(Echec(cours=cours_dossier, element=element, cause=cause))
            self._emettre("echec", f"{element} : {erreur}")

    def _archiver_un_cours(self, cours) -> None:
        from extracteur.ena import URL

        base = self.chemin_du_cours(cours)

        self._archiver_plan_de_cours(cours, base)

        modules = self.ena.modules(cours)
        for module in modules:
            self._archiver_module(cours, module, base)

        evaluations = self.ena.evaluations(cours)
        if evaluations:
            self._capturer_page_section(
                cours, URL.evaluations(cours.id_site), base / "Pages" / NOM_PAGE_EVALUATIONS
            )

        for evaluation in evaluations:
            nom_evaluation = evaluation.titre or evaluation.id_evaluation
            dossier = base / "Évaluations" / _segment(nom_evaluation)

            # La plateforme ferme le 1er novembre 2026 : les consignes d'un
            # travail, redigees dans la description de son evaluation, les
            # conditions de remise de sa boite de depot, et une eventuelle
            # retroaction dans l'onglet Resultats disparaitraient sans
            # laisser de trace si on ne les capturait pas ici. La sortie
            # console annonce chaque visite (voir _emettre "visite"), au
            # meme titre que les fichiers telecharges : sans quoi rien ne
            # distingue une consigne reellement absente d'une simplement non
            # visitee. Ordre aligne sur celui des onglets reels de la page
            # (Description, ..., Boite de depot, ..., Resultats).
            self._emettre("visite", f"{nom_evaluation} : Description")
            self._archiver_page_evaluation(
                cours,
                evaluation,
                dossier,
                self.ena.fichiers_de_description,
                URL.evaluation(evaluation.id_site, evaluation.id_evaluation),
                NOM_DESCRIPTION_EVALUATION,
            )

            self._emettre("visite", f"{nom_evaluation} : Boîte de dépôt")
            depots = self.ena.fichiers_de_depot(evaluation)
            for depot in depots:
                self._recuperer(cours, Fichier(nom=depot.nom, url=depot.url), dossier)
            if depots:
                dossier.mkdir(parents=True, exist_ok=True)
                self._ecrire_synthese(
                    cours.dossier(), "depots.csv", lambda: ecrire_depots(dossier / "depots.csv", depots)
                )
            self._capturer_page_section(
                cours,
                URL.boite_depot(evaluation.id_site, evaluation.id_evaluation),
                dossier / NOM_BOITE_DE_DEPOT,
            )

            self._archiver_page_evaluation(
                cours,
                evaluation,
                dossier,
                self.ena.fichiers_de_resultats_evaluation,
                URL.evaluation_resultats(evaluation.id_site, evaluation.id_evaluation),
                NOM_RESULTATS_EVALUATION,
            )

        notes = self.ena.resultats(cours)
        if notes:
            self._ecrire_synthese(
                cours.dossier(), "notes.csv", lambda: ecrire_notes(base / "notes.csv", notes)
            )
            self._capturer_page_section(
                cours, URL.resultats(cours.id_site), base / "Pages" / NOM_PAGE_RESULTATS
            )

        # Repli. Certains sites n'ont ni modules ni evaluations : leur contenu
        # n'est atteignable qu'en cliquant les entrees de leur propre menu. Sans
        # cette branche, ces cours produiraient un dossier vide.
        if not modules and not evaluations:
            sections = self._parcourir_le_menu(cours, base)
            if not sections and not notes:
                # Un site de cours reel porte toujours au moins un module,
                # une evaluation ou une section de menu. N'en trouver aucun
                # trahit une lecture ratee (page pas encore rendue, site
                # inattendu), pas un cours vide. Constate en usage reel :
                # quatre cours ressortis avec leur seul plan de cours, et
                # pas une ligne au rapport. Mieux vaut un echec a verifier
                # qu'un vide qui passe pour normal.
                self.resultat.echecs.append(
                    Echec(
                        cours=cours.dossier(),
                        element="(cours entier)",
                        cause=MESSAGE_COURS_SANS_CONTENU,
                    )
                )
                self._emettre("echec", f"{cours.dossier()} : aucun contenu trouve")

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
        from extracteur.extraction import fichiers_depuis_html, ressources_ignorees_depuis_html

        def traiter(libelle, html):
            dossier = base / "Documents" / _segment(libelle)
            for fichier in fichiers_depuis_html(html):
                self._recuperer(cours, fichier, dossier)
            # Le HTML de la section est deja en main : aucune navigation
            # supplementaire n'est necessaire pour en tirer les ressources
            # ignorees, contrairement au repere par idPage des modules.
            self._consigner_ignorees(cours, ressources_ignorees_depuis_html(html), dossier)

            cible = base / "Pages" / _segment(f"{libelle}.pdf")
            self._capturer_page_courante(cours, cible)

        return self.ena.parcourir_menu(cours, traiter)

    def _consigner_page_courante(self, cours, dossier: Path) -> None:
        """Ressources ignorees (videos, liens externes, traceurs inconnus) de
        la page DEJA affichee, consignees sans navigation supplementaire ni
        telechargement.

        Isolee comme _capturer_page_courante : une lecture ratee ne coute que
        cette page, jamais le module ni le cours. SessionExpiree n'est jamais
        isolee ici : elle doit remonter jusqu'a archiver(), qui met la file en
        pause plutot que de la traiter comme un echec ordinaire.
        """
        try:
            ressources = self.ena.ressources_ignorees_page_courante()
        except SessionExpiree:
            raise
        except ErreurPlaywright as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=dossier.name, cause=str(erreur))
            )
            return
        self._consigner_ignorees(cours, ressources, dossier)

    def _consigner_ignorees(self, cours, ressources, dossier: Path) -> None:
        """Inscrit au manifeste les ressources deliberement non telechargees,
        sans jamais rien ecrire sur disque pour elles.

        Le chemin consigne est celui ou le fichier SERAIT alle -- le dossier
        de la page, plus son nom -- pour que l'utilisateur sache a quel cours
        et a quel onglet une ressource ignoree se rattache, une fois la
        plateforme fermee. Taille et empreinte restent vides : rien n'a ete
        telecharge, il n'y a rien a mesurer.

        Isolee comme le reste de l'archivage : une ressource qui ne peut pas
        etre consignee (ecriture du manifeste bloquee, par exemple) ne doit
        jamais couter le module ni le cours.
        """
        for ressource in ressources:
            nom = tronquer(nom_sur(ressource.nom))
            relatif = self._relatif(dossier / nom)
            try:
                self.manifeste.ajouter(relatif, "", "", ressource.url, statut=ressource.genre)
            except OSError as erreur:
                self.resultat.echecs.append(
                    Echec(cours=cours.dossier(), element=nom, cause=str(erreur), url=ressource.url)
                )

    def _capturer_page_courante(self, cours, destination: Path) -> None:
        """Imprime la page telle qu'elle est deja affichee, sans renaviguer.

        Deux appelants, une seule raison : le navigateur est deja sur la bonne
        page et la recharger serait un aller-retour ADF de plusieurs secondes
        pour rien.
        - Le repli par le menu n'a de toute facon aucun identifiant vers
          lequel renaviguer : l'etat courant du navigateur est la seule
          capture possible.
        - Une page d'onglet de module vient d'etre lue pour ses fichiers, donc
          le navigateur y est encore (voir _archiver_page_de_module).

        Isolee comme _capturer : ena.parcourir_menu n'intercepte que le clic,
        pas l'appel a cette fonction de traitement. Sans cette isolation, une
        impression ratee pour une section fait echouer tout le cours en un
        seul Echec generique, et perd en silence le contenu recuperable des
        sections suivantes.
        """
        if destination.exists():
            return
        try:
            self.ena.capturer_pdf_page_courante(destination)
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _archiver_module(self, cours, module, base: Path) -> None:
        """Toutes les pages d'un module : la racine seule s'il ne porte aucun
        onglet, sinon chaque feuille de sa hierarchie d'onglets, eventuellement
        imbriquee (voir Ena.pages_du_module et docs/api-monportail.md,
        etape 3).

        Isolee comme _archiver_page_evaluation : une structure d'onglets
        illisible ou pathologique (TropDePagesDansUnModule) ne coute que ce
        module, jamais les autres modules ni le reste du cours. SessionExpiree
        n'est jamais isolee ici : elle doit remonter intacte jusqu'a
        archiver(), qui met la file en pause plutot que de traiter une
        session expiree comme un module en echec ordinaire.
        """
        from extracteur.ena import TropDePagesDansUnModule

        nom_module = module.titre or module.id_module
        try:
            pages = self.ena.pages_du_module(module)
        except SessionExpiree:
            raise
        except (ErreurPlaywright, TropDePagesDansUnModule) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=nom_module, cause=str(erreur))
            )
            return

        for page in pages:
            self._archiver_page_de_module(cours, module, page, base)

    def _archiver_page_de_module(self, cours, module, page, base: Path) -> None:
        """Fichiers et capture PDF d'une page reelle du module : la racine
        (page.chemin vide) si le module ne porte aucun onglet, sinon une
        feuille de sa hierarchie -- page.chemin porte les titres des onglets
        traverses, du niveau 1 vers le plus profond.

        Isolee comme _archiver_page_evaluation : un echec ici (navigation
        Playwright) ne coute que cette page, jamais les autres pages du
        module ni le reste du cours.

        Appelle fichiers_du_module avec un seul argument (module) quand
        page.id_page est None -- exactement l'appel d'avant l'ajout des
        onglets -- plutot que de toujours passer id_page=None explicitement :
        preserve la compatibilite d'implementations de Ena qui ne
        distinguent pas encore les onglets.
        """
        nom_module = module.titre or module.id_module
        nom_complet = " - ".join([nom_module, *page.chemin])

        dossier = base / "Documents" / _segment(nom_module)
        for titre in page.chemin:
            dossier = dossier / _segment(titre)

        cible_pdf = base / "Pages" / _segment(f"{nom_complet}.pdf")
        # La lecture des fichiers amene deja le navigateur sur cette page. On
        # l'imprime donc AVANT de telecharger quoi que ce soit, tant qu'elle
        # est encore affichee, plutot que de la recharger apres : une
        # navigation ADF coute plusieurs secondes, et il y en a une par page
        # d'onglet. `sur_la_page` dit si cette garantie tient encore.
        sur_la_page = False
        fichiers: list = []
        try:
            if page.id_page is None:
                fichiers = self.ena.fichiers_du_module(module)
            else:
                fichiers = self.ena.fichiers_du_module(module, page.id_page)
            sur_la_page = True
        except SessionExpiree:
            raise
        except ErreurPlaywright as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=nom_complet, cause=str(erreur))
            )

        if sur_la_page:
            self._capturer_page_courante(cours, cible_pdf)
            self._consigner_page_courante(cours, dossier)
        else:
            # Lecture echouee : le navigateur n'est PAS sur la bonne page.
            # Imprimer la page courante produirait un PDF d'apparence valide
            # au contenu faux -- on navigue explicitement.
            self._capturer(cours, module, cible_pdf, id_page=page.id_page)

        for fichier in fichiers:
            self._recuperer(cours, fichier, dossier)

    def _capturer(self, cours, module, destination: Path, id_page: str | None = None) -> None:
        if destination.exists():
            return
        try:
            from extracteur.ena import URL

            self.ena.capturer_pdf(URL.module(module.id_site, module.id_module, id_page), destination)
        # Meme isolation que pour le plan de cours : une capture de page ratee
        # ne doit pas emporter les autres modules ni le reste du cours.
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _capturer_page_section(self, cours, chemin: str, destination: Path) -> None:
        """Capture en PDF une page de synthese de la section evaluations
        (liste des evaluations, ou sommaire des resultats).

        Isolee comme _capturer : une impression ratee ne doit couter que
        cette page, jamais le cours.
        """
        if destination.exists():
            return
        try:
            self.ena.capturer_pdf(chemin, destination)
        except (ErreurPlaywright, OSError) as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _archiver_page_evaluation(
        self, cours, evaluation, dossier: Path, obtenir_fichiers, chemin: str, nom_pdf: str
    ) -> None:
        """Piece jointe et capture PDF d'un onglet d'evaluation (description
        ou resultats).

        Isolee comme _capturer et _archiver_plan_de_cours : un echec ici
        (navigation Playwright, ecriture disque du PDF) ne coute jamais que ce
        fichier, ni l'evaluation ni le cours. Le telechargement des pieces
        jointes deja trouvees est lui-meme isole fichier par fichier, via
        _recuperer/_telecharger.

        obtenir_fichiers vient de naviguer vers `chemin` : le navigateur y
        est encore juste apres cet appel, ce qui permet de consigner les
        ressources ignorees de cette meme page sans navigation
        supplementaire -- avant que capturer_pdf, plus bas, ne navigue a
        nouveau pour l'impression.
        """
        try:
            fichiers = obtenir_fichiers(evaluation)
        except SessionExpiree:
            raise
        except ErreurPlaywright as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=nom_pdf, cause=str(erreur))
            )
        else:
            for fichier in fichiers:
                self._recuperer(cours, fichier, dossier)
            self._consigner_page_courante(cours, dossier)

        destination = dossier / nom_pdf
        if destination.exists():
            return
        try:
            self.ena.capturer_pdf(chemin, destination)
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
