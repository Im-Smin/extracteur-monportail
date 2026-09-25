"""Fenetre de controle Tkinter.

Aucun appel reseau ici : la fenetre ne fait qu'appeler le coeur d'archivage
(_tout, _session, _un_seul_cours de extracteur.__main__) dans un fil de
travail, et vider une file de lignes de texte toutes les 100 ms. C'est ce
qui evite l'interface gelee pendant un telechargement de 200 Mo.

Trois contraintes structurent tout ce module :

- Tkinter n'est pas thread-safe. Aucune ligne de ce fichier ne touche a un
  widget depuis un autre fil que celui de mainloop() : le fil de travail
  n'ecrit que dans une queue.Queue, et c'est la boucle after() qui lit.
- Playwright (API synchrone) exige que tous ses appels viennent du fil qui
  l'a demarre. Le coeur appele gere deja ce fil-la lui-meme ; la fenetre ne
  fait que l'appeler depuis SON fil de travail, et ne detient jamais de
  reference a un objet Playwright.
- Une archive incomplete ne doit jamais etre presentee comme terminee. La
  plateforme ferme le 1er novembre 2026 : un manque annonce en vert serait
  decouvert quand la source n'existe plus. D'ou verdict(), qui separe
  explicitement « termine » de « termine avec des manques ».
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from extracteur.verification import creer_zip

PORTEE_TOUT = "tout"
PORTEE_SESSION = "session"
PORTEE_COURS = "cours"

# Cadence de vidage de la file. Assez court pour que le journal defile au fil
# de l'eau, assez long pour ne pas occuper le fil d'affichage a ne rien faire.
INTERVALLE_VIDAGE_MS = 100

# Plafond du journal affiche. Un archivage complet sur 33 cours produit
# plusieurs milliers de lignes ; les garder toutes ferait gonfler le widget
# sans fin, alors que le compte rendu durable est _rapport.html, pas cette
# zone de texte.
LIGNES_JOURNAL_MAX = 5000

# Texte d'accueil du journal. Il occupe la zone de progression tant que rien
# n'a demarre, et sert de mode d'emploi complet : l'outil n'a ni menu ni aide,
# et son utilisateur ne s'en servira qu'une poignee de fois avant la fermeture
# de la plateforme. Il doit donc repondre d'avance aux trois questions qui
# couteraient cher plus tard -- ce que le programme va faire, ce qu'on
# retrouvera sur le disque, et surtout ce qu'il ne recupere PAS, seul moyen
# d'aller chercher ce qui manque a la main pendant que la source existe encore.
TEXTE_ACCUEIL = """ARCHIVEUR MONPORTAIL
====================
Developpe par Im-Smin, en collaboration avec Claude (Anthropic).
Logiciel libre sous licence MIT -- github.com/Im-Smin/extracteur-monportail

Ce programme copie sur votre disque tout ce que vous avez sur monPortail,
avant la fermeture definitive de la plateforme le 1er novembre 2026. Passe
cette date, plus rien n'y sera recuperable.

CE QUI SE PASSE QUAND VOUS CLIQUEZ SUR « COMMENCER »

  1. Une fenetre de navigateur SEPAREE s'ouvre. Connectez-vous DEDANS, MFA
     compris : c'est la seule dont le programme peut lire les cookies. Se
     connecter ailleurs ne sert a rien. L'authentification est redemandee a
     chaque lancement -- c'est voulu, aucun mot de passe et aucun profil de
     navigateur ne sont conserves.
  2. Il dresse la liste de vos sessions et de vos cours.
  3. Il visite chaque cours et telecharge son contenu, en affichant ici ce
     qu'il fait. Comptez environ une minute par cours.
  4. Il recompte ce qui est reellement sur le disque, face a l'inventaire de
     ce qu'il dit avoir ecrit.
  5. Il compresse le tout dans un fichier .zip, pose A COTE du dossier.

CE QUE VOUS OBTIENDREZ  (un dossier par session, puis un par cours)

  Plan de cours\\    le PDF officiel de l'Universite
  Documents\\        les fichiers fournis par le professeur, ranges par module
  Pages\\            une copie PDF de chaque page visitee. C'est la que se
                    trouve le texte ecrit par le professeur, celui qui
                    n'existe sous forme d'aucune piece jointe.
  Évaluations\\      un dossier par evaluation : l'enonce, la boite de depot,
                    VOS travaux remis, la retroaction et la note
  notes.csv         vos resultats pour ce cours

  Et a la racine de l'archive : notes-tous-cours.csv (tous vos resultats
  reunis), manifeste.csv (l'inventaire de ce qui a ete ecrit) et
  _rapport.html (la liste de ce qui a echoue, s'il y a lieu).

CE QUI N'EST PAS ARCHIVE  -- a recuperer a la main si vous y tenez

  - les annonces, les forums de discussion et les videos ;
  - les « Autres activites » du tableau de bord (formations
    institutionnelles) : seuls les cours sont traites ;
  - les sections « Materiel didactique », « Mediagraphie et annexes » et
    « Bibliographie », qui ne listent que des renvois vers des ouvrages.

BON A SAVOIR

  - Relancer ne recommence rien : ce qui est deja sur le disque n'est pas
    retelecharge. Vous pouvez arreter et reprendre plus tard.
  - Rien n'est jamais supprime ni modifie sur monPortail. Le programme ne
    fait que lire.
  - S'il manque quoi que ce soit a la fin, cette fenetre le dira. Elle
    n'affichera jamais « TERMINE » tout court sur une archive incomplete.

Choisissez un emplacement, ce que vous voulez archiver, puis Commencer.
"""


class ChampManquant(ValueError):
    """Un champ obligatoire est vide ou mal rempli.

    Levee avant d'ouvrir quoi que ce soit : une faute de frappe sur l'idSite
    doit couter une seconde, pas un cycle complet de connexion avec MFA.
    """


def valider_les_champs(portee: str, destination, libelle_session: str, id_site: str) -> str:
    """Verifie les champs de la fenetre et rend l'argument du mode choisi.

    Rend le libelle de session pour PORTEE_SESSION, l'idSite pour
    PORTEE_COURS, et une chaine vide pour PORTEE_TOUT (qui n'a pas
    d'argument). Leve ChampManquant, avec un message affichable tel quel,
    des qu'un champ necessaire manque.
    """
    if not str(destination or "").strip():
        raise ChampManquant("Choisissez d'abord un emplacement d'enregistrement.")

    if portee == PORTEE_TOUT:
        return ""

    if portee == PORTEE_SESSION:
        libelle = (libelle_session or "").strip()
        if not libelle:
            raise ChampManquant("Entrez le libelle de la session, par exemple : Automne 2022.")
        return libelle

    if portee == PORTEE_COURS:
        identifiant = (id_site or "").strip()
        if not identifiant:
            raise ChampManquant(
                "Entrez l'idSite du cours, par exemple : 100001. Il se lit dans "
                "l'adresse du site de cours, apres idSite=."
            )
        if not identifiant.isdigit():
            # Verifie ici plutot que sur le site : un idSite non numerique ne
            # correspondra jamais a rien (voir docs/api-monportail.md), et le
            # constater apres la connexion couterait plusieurs minutes.
            raise ChampManquant(
                f"L'idSite doit etre un nombre. Recu : {identifiant!r}. Il se lit "
                "dans l'adresse du site de cours, apres idSite=."
            )
        return identifiant

    raise ChampManquant(f"Portee inconnue : {portee!r}")


def nom_du_zip(destination) -> Path:
    """Le chemin du .zip, ecrit A COTE du dossier archive, jamais dedans.

    creer_zip sait s'exclure de son propre contenu, mais le tenir dehors
    evite aussi de faire grossir le dossier qu'on vient de verifier : le
    manifeste ne connait pas ce fichier, et le mode --verifier n'aurait
    aucune raison de le trouver la.
    """
    destination = Path(destination)
    return destination.parent / f"{destination.name}.zip"


def verdict(code: int, resultat=None, controle=None, chemin_zip=None) -> tuple:
    """Rend (titre, detail, complet) pour l'etat final de la fenetre.

    `complet` n'est vrai que si l'archivage s'est termine sans le moindre
    echec ni la moindre anomalie de verification : c'est lui qui decide si la
    fenetre a le droit d'afficher un simple « TERMINE ». Tout le reste est
    annonce comme incomplet, meme quand des centaines de fichiers ont ete
    recuperes -- voir la docstring du module.
    """
    if code == 0 and resultat is not None:
        detail = (
            f"{resultat.fichiers_ecrits} fichiers archives, "
            f"{resultat.fichiers_sautes} deja presents, aucun echec."
        )
        if chemin_zip is not None:
            detail += f"\nArchive compressee : {chemin_zip}"
        return ("TERMINE", detail, True)

    if code == 2:
        return (
            "ARRETE",
            "Aucun cours ne correspond a cet idSite. Verifiez le numero dans "
            "l'adresse du site de cours, apres idSite=.",
            False,
        )

    if code == 3:
        return (
            "ARRETE - SESSION EXPIREE",
            "La connexion a monPortail a expire en cours de route. Relancez : "
            "la reprise ne retelecharge pas ce qui est deja sur disque.",
            False,
        )

    if resultat is None:
        return (
            "ARRETE",
            "L'archivage n'a pas abouti, et rien n'a ete compresse. Le detail "
            "est dans le journal ci-dessus.",
            False,
        )

    morceaux = [
        f"{resultat.fichiers_ecrits} fichiers archives, "
        f"{resultat.fichiers_sautes} deja presents."
    ]
    if resultat.cours_non_tentes:
        morceaux.append(
            f"{resultat.cours_non_tentes} cours n'ont jamais ete tentes : relancez "
            "pour reprendre la ou l'archivage s'est arrete."
        )
    if resultat.echecs:
        morceaux.append(f"{len(resultat.echecs)} elements manquent -- voir _rapport.html.")
    if controle:
        anomalies = len(controle.get("manquants", [])) + len(controle.get("taille_incorrecte", []))
        if anomalies:
            morceaux.append(f"{anomalies} anomalies relevees a la verification.")
    if chemin_zip is not None:
        morceaux.append(f"Archive compressee : {chemin_zip}")

    return ("TERMINE - ARCHIVE INCOMPLETE", "\n".join(morceaux), False)


def _modes_par_defaut() -> dict:
    """Importe le coeur au dernier moment.

    Import differe volontaire : extracteur/__main__.py importe ce module
    depuis main(), un import de module a module en sens inverse serait
    circulaire.
    """
    from extracteur.__main__ import _session, _tout, _un_seul_cours

    return {
        PORTEE_TOUT: lambda argument, **reste: _tout(**reste),
        PORTEE_SESSION: lambda argument, **reste: _session(argument, **reste),
        PORTEE_COURS: lambda argument, **reste: _un_seul_cours(argument, **reste),
    }


def executer_archivage(
    portee: str,
    argument: str,
    destination,
    imprimer,
    imprimer_erreur,
    annulation=None,
    modes=None,
    compresser=creer_zip,
) -> dict:
    """Archive, puis compresse, et rend l'etat final en un seul dictionnaire.

    La verification n'est pas refaite ici : les trois modes du coeur la font
    deja eux-memes et versent leurs anomalies dans _rapport.html avant de
    rendre la main (voir la docstring de extracteur.__main__). `sur_fin` nous
    en remet les compteurs exacts, ce qui evite d'avoir a les relire dans le
    texte affiche.

    Le ZIP n'est produit que lorsque l'archivage est alle jusqu'au bout de sa
    verification -- donc jamais apres une connexion echouee, un cours
    introuvable, une session expiree ou une interruption. Compresser une
    archive qu'on sait tronquee fabriquerait un fichier d'apparence
    definitive alors qu'il suffit de relancer pour la completer.

    Le ZIP est d'abord tente A COTE du dossier (nom_du_zip). Si cette
    ecriture echoue (OSError), on reessaie A L'INTERIEUR du dossier de
    destination : ecrire a cote demande les droits d'administrateur quand ce
    dossier est directement a la racine d'un disque, cas reel constate
    ("C:\\Archive test" -> "C:\\Archive test.zip", [Errno 13] Permission
    denied). creer_zip sait deja s'exclure de son propre contenu, ce second
    essai est donc sur. Si les deux emplacements echouent, le comportement
    d'avant est conserve : message explicite, archivage tout de meme
    considere reussi, commande de rattrapage donnee.

    Ne leve jamais : toute exception inattendue est rendue dans l'etat, pour
    que la fenetre puisse la montrer au lieu de mourir en silence dans son
    fil de travail.
    """
    if modes is None:
        modes = _modes_par_defaut()

    etat: dict = {
        "code": 1,
        "resultat": None,
        "controle": None,
        "chemin_rapport": None,
        "chemin_zip": None,
    }

    def sur_fin(resultat, controle, chemin_rapport):
        etat["resultat"] = resultat
        etat["controle"] = controle
        etat["chemin_rapport"] = chemin_rapport

    try:
        etat["code"] = modes[portee](
            argument,
            destination=Path(destination),
            imprimer=imprimer,
            imprimer_erreur=imprimer_erreur,
            annulation=annulation,
            sur_fin=sur_fin,
        )
    except Exception as erreur:  # le fil de travail ne doit jamais mourir muet
        imprimer_erreur(f"ECHEC inattendu : {erreur}")
        etat["code"] = 1
        return etat

    if etat["resultat"] is None:
        return etat

    # Sur ce qui a REELLEMENT ete laisse de cote, pas sur l'etat courant du
    # drapeau d'annulation : celui-ci reste pose apres coup, et l'archiveur ne
    # le lit qu'a la frontiere de chaque cours. Un clic sur Arreter pendant le
    # dernier cours le laisse donc finir, l'archive est complete -- et lire le
    # drapeau ferait sauter sa compression en annoncant une interruption qui
    # n'a rien coute.
    if etat["resultat"].cours_non_tentes:
        imprimer(
            f"Archivage interrompu : {etat['resultat'].cours_non_tentes} cours n'ont pas "
            "ete tentes, rien n'a ete compresse. Relancez pour reprendre la ou vous en "
            "etiez, le ZIP sera produit a ce moment-la."
        )
        return etat

    cible = nom_du_zip(destination)
    imprimer(f"\nCompression vers {cible} ...")
    try:
        etat["chemin_zip"] = compresser(Path(destination), cible)
        imprimer(f"Archive compressee : {etat['chemin_zip']}")
    except OSError as erreur_a_cote:
        # Ecrire A COTE du dossier demande les droits d'administrateur quand
        # ce dossier est directement a la racine d'un disque (constate en
        # usage reel : destination "C:\Archive test", ZIP vise a
        # "C:\Archive test.zip" -> [Errno 13] Permission denied). creer_zip
        # sait deja s'exclure de son propre contenu (voir sa docstring) :
        # ecrire DEDANS est donc une deuxieme tentative sure, avant
        # d'abandonner completement la compression.
        cible_dedans = Path(destination) / f"{Path(destination).name}.zip"
        imprimer(
            f"Compression a cote du dossier impossible ({erreur_a_cote}) ; "
            f"nouvel essai a l'interieur du dossier, vers {cible_dedans} ..."
        )
        try:
            etat["chemin_zip"] = compresser(Path(destination), cible_dedans)
            imprimer(f"Archive compressee : {etat['chemin_zip']}")
        except OSError as erreur:
            # L'archivage lui-meme a reussi : un ZIP manquant ne doit pas
            # effacer ce resultat, seulement etre dit clairement. Le dossier
            # reste compressible a part, par le mode --zip.
            commande = f'python -m extracteur --zip --destination "{destination}"'
            imprimer_erreur(
                f"ATTENTION : la compression a echoue ({erreur}). Tous les fichiers sont "
                f"bien dans {destination} ; relancez la compression seule avec :\n  {commande}"
            )
            etat["code"] = etat["code"] or 1

    return etat


class Fenetre:
    """La fenetre elle-meme. Ne fait jamais de travail dans le fil d'affichage.

    Deux seuls objets traversent la frontiere entre les fils : la file
    `evenements`, ou le fil de travail depose des ('ligne', texte) et un
    unique ('fin', etat), et l'Event `annulation`, que le fil d'affichage
    pose et que le coeur lit. Rien d'autre n'est partage.
    """

    def __init__(self, racine, destination):
        self.racine = racine
        self.evenements: queue.Queue = queue.Queue()
        self.annulation = threading.Event()
        self.fil = None
        self.fermeture_demandee = False

        racine.title("Archiveur monPortail")
        racine.geometry("860x640")
        racine.minsize(720, 520)

        self.destination = tk.StringVar(value=str(Path(destination).resolve()))
        self.portee = tk.StringVar(value=PORTEE_TOUT)
        self.libelle_session = tk.StringVar()
        self.id_site = tk.StringVar()

        self._construire_emplacement()
        self._construire_portee()
        self._construire_commandes()
        self._construire_journal()

        self._accueillir()

        racine.protocol("WM_DELETE_WINDOW", self._fermer)
        racine.after(INTERVALLE_VIDAGE_MS, self._vider_la_file)

    # --- construction des zones ---

    def _construire_emplacement(self) -> None:
        cadre = ttk.LabelFrame(self.racine, text="Emplacement d'enregistrement", padding=8)
        cadre.pack(fill="x", padx=10, pady=(10, 4))

        ttk.Entry(cadre, textvariable=self.destination).pack(
            side="left", fill="x", expand=True, padx=(0, 6)
        )
        ttk.Button(cadre, text="Parcourir...", command=self._choisir_dossier).pack(side="left")

    def _construire_portee(self) -> None:
        cadre = ttk.LabelFrame(self.racine, text="Quoi archiver", padding=8)
        cadre.pack(fill="x", padx=10, pady=4)

        ttk.Radiobutton(
            cadre,
            text="Tout (toutes les sessions, de la plus ancienne a la plus recente)",
            value=PORTEE_TOUT,
            variable=self.portee,
            command=self._rafraichir_champs,
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Radiobutton(
            cadre,
            text="Une session :",
            value=PORTEE_SESSION,
            variable=self.portee,
            command=self._rafraichir_champs,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.champ_session = ttk.Entry(cadre, textvariable=self.libelle_session, width=30)
        self.champ_session.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Label(cadre, text="ex. Automne 2022").grid(row=1, column=2, sticky="w")

        ttk.Radiobutton(
            cadre,
            text="Un seul cours :",
            value=PORTEE_COURS,
            variable=self.portee,
            command=self._rafraichir_champs,
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.champ_cours = ttk.Entry(cadre, textvariable=self.id_site, width=30)
        self.champ_cours.grid(row=2, column=1, sticky="w", padx=6, pady=(4, 0))
        ttk.Label(cadre, text="idSite, ex. 100001").grid(row=2, column=2, sticky="w")

        self._rafraichir_champs()

    def _construire_commandes(self) -> None:
        cadre = ttk.Frame(self.racine, padding=(10, 4))
        cadre.pack(fill="x")

        self.bouton_commencer = ttk.Button(cadre, text="Commencer", command=self._commencer)
        self.bouton_commencer.pack(side="left")

        self.bouton_arreter = ttk.Button(
            cadre, text="Arreter", command=self._arreter, state="disabled"
        )
        self.bouton_arreter.pack(side="left", padx=6)

        self.etat = ttk.Label(cadre, text="Pret.", anchor="w", justify="left")
        self.etat.pack(side="left", fill="x", expand=True, padx=10)

    def _construire_journal(self) -> None:
        cadre = ttk.LabelFrame(self.racine, text="Progression", padding=8)
        cadre.pack(fill="both", expand=True, padx=10, pady=(4, 10))

        self.journal = tk.Text(cadre, height=18, wrap="word", state="disabled")
        barre = ttk.Scrollbar(cadre, orient="vertical", command=self.journal.yview)
        self.journal.configure(yscrollcommand=barre.set)
        self.journal.pack(side="left", fill="both", expand=True)
        barre.pack(side="right", fill="y")

    # --- actions du fil d'affichage ---

    def _rafraichir_champs(self) -> None:
        """Grise les deux champs de saisie hors de leur portee.

        Les laisser actifs inviterait a taper un libelle de session tout en
        laissant le bouton radio sur « Tout » -- et a lancer huit sessions
        sans s'en apercevoir.
        """
        portee = self.portee.get()
        self.champ_session.configure(
            state="normal" if portee == PORTEE_SESSION else "disabled"
        )
        self.champ_cours.configure(state="normal" if portee == PORTEE_COURS else "disabled")

    def _choisir_dossier(self) -> None:
        choisi = filedialog.askdirectory(
            title="Choisir l'emplacement de l'archive",
            initialdir=self.destination.get() or ".",
        )
        if choisi:
            self.destination.set(str(Path(choisi)))

    def _accueillir(self) -> None:
        """Remplit le journal du mode d'emploi, cadre sur sa premiere ligne.

        Pas de _ecrire ici : celui-ci fait defiler jusqu'en bas, ce qui
        placerait l'utilisateur devant la derniere ligne d'un texte qui se lit
        depuis le debut.
        """
        self.journal.configure(state="normal")
        self.journal.delete("1.0", "end")
        self.journal.insert("1.0", TEXTE_ACCUEIL)
        self.journal.see("1.0")
        self.journal.configure(state="disabled")

    def _vider_le_journal(self) -> None:
        self.journal.configure(state="normal")
        self.journal.delete("1.0", "end")
        self.journal.configure(state="disabled")

    def _ecrire(self, texte: str) -> None:
        self.journal.configure(state="normal")
        self.journal.insert("end", f"{texte}\n")
        # Elagage par le haut : la zone garde une fenetre glissante des
        # dernieres lignes, pas tout l'historique (voir LIGNES_JOURNAL_MAX).
        trop = int(self.journal.index("end-1c").split(".")[0]) - LIGNES_JOURNAL_MAX
        if trop > 0:
            self.journal.delete("1.0", f"{trop + 1}.0")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def _commencer(self) -> None:
        try:
            argument = valider_les_champs(
                self.portee.get(),
                self.destination.get(),
                self.libelle_session.get(),
                self.id_site.get(),
            )
        except ChampManquant as erreur:
            self.etat.configure(text=str(erreur))
            self._ecrire(f"{erreur}")
            return

        destination = Path(self.destination.get())
        self.annulation.clear()
        self.bouton_commencer.configure(state="disabled")
        self.bouton_arreter.configure(state="normal")
        self.etat.configure(text="Archivage en cours -- connectez-vous dans le navigateur.")

        # Le mode d'emploi cede la place a la progression : sur un archivage
        # complet, la laisser enfouie sous cinquante lignes de texte rendrait
        # illisible la seule chose a suivre pendant l'heure qui vient. Il
        # revient au prochain lancement.
        self._vider_le_journal()
        self._ecrire(f"=== Archivage vers {destination} ===")
        self._ecrire(
            "Une fenetre de navigateur separee va s'ouvrir : connectez-vous DEDANS, "
            "MFA compris. C'est la seule dont le programme peut lire les cookies."
        )
        self._ecrire("")

        self.fil = threading.Thread(
            target=self._travailler,
            args=(self.portee.get(), argument, destination),
            daemon=True,
        )
        self.fil.start()

    def _arreter(self) -> None:
        """Demande l'arret sans jamais toucher au navigateur depuis ce fil.

        Poser l'Event suffit : le coeur le lit a chaque sondage de connexion
        et a chaque frontiere de cours, et s'arrete de lui-meme en fermant
        proprement Playwright dans le fil qui l'a ouvert.
        """
        self.annulation.set()
        self.bouton_arreter.configure(state="disabled")
        self.etat.configure(text="Arret demande : arret a la prochaine etape, patientez...")
        self._ecrire(
            "\nArret demande. Il prend effet a la prochaine etape verifiee : fin du cours "
            "en cours, ou fin de la session en cours d'enumeration. Comptez une minute ou "
            "deux. Rien de ce qui est deja sur le disque n'est perdu, et relancer reprendra "
            "la ou l'archivage s'arrete."
        )

    def _fermer(self) -> None:
        """Ferme la fenetre -- mais jamais au milieu d'un archivage.

        Cliquer le X du systeme est un geste naturel, et Tkinter detruirait la
        racine sur-le-champ : mainloop() rendrait la main, le processus se
        terminerait, et le fil de travail serait tue avant d'avoir ecrit
        _rapport.html. L'utilisateur se retrouverait avec une archive tronquee
        et AUCUNE trace de ce qui manque -- precisement ce que tout ce projet
        cherche a eviter. En console, le meme geste (Ctrl+C) est deja
        intercepte pour attendre le fil et ecrire le rapport ; la fenetre doit
        offrir le meme filet.

        On demande donc l'arret, on laisse le fil finir proprement (il ferme
        Chromium, supprime son profil temporaire et ecrit le rapport), et
        c'est _vider_la_file qui detruira la fenetre une fois le fil mort.
        """
        if self.fil is not None and self.fil.is_alive():
            self.fermeture_demandee = True
            self.annulation.set()
            self.bouton_arreter.configure(state="disabled")
            self.etat.configure(text="Fermeture : fin propre en cours, patientez...")
            self._ecrire(
                "\nFermeture demandee. L'archivage s'arrete proprement : fermeture du "
                "navigateur et ecriture du rapport. Comptez le temps de finir le cours "
                "en cours, une minute ou deux. La fenetre se fermera toute seule."
            )
            return
        self.racine.destroy()

    # --- fil de travail ---

    def _travailler(self, portee: str, argument: str, destination: Path) -> None:
        def publier(texte):
            self.evenements.put(("ligne", str(texte)))

        etat = {"code": 1, "resultat": None, "controle": None, "chemin_zip": None}
        try:
            etat = executer_archivage(
                portee,
                argument,
                destination,
                imprimer=publier,
                imprimer_erreur=publier,
                annulation=self.annulation,
            )
        except BaseException as erreur:  # filet ultime : jamais de fil muet
            publier(f"ECHEC inattendu : {erreur}")
            raise
        finally:
            # Dans un finally, et sur BaseException : sans cet evenement, la
            # fenetre resterait figee pour toujours -- Commencer desactive,
            # Arreter actif mais sans fil vivant pour lui repondre.
            self.evenements.put(("fin", etat))

    # --- boucle d'affichage ---

    def _vider_la_file(self) -> None:
        try:
            while True:
                type_evenement, donnee = self.evenements.get_nowait()
                if type_evenement == "fin":
                    self._conclure(donnee)
                else:
                    self._ecrire(donnee)
        except queue.Empty:
            pass

        if self.fermeture_demandee and (self.fil is None or not self.fil.is_alive()):
            # Le fil est mort : son finally a ferme le navigateur et le rapport
            # est ecrit. La file vient d'etre videe juste au-dessus, donc rien
            # n'est perdu. On peut fermer.
            self.racine.destroy()
            return

        self.racine.after(INTERVALLE_VIDAGE_MS, self._vider_la_file)

    def _conclure(self, etat: dict) -> None:
        titre, detail, complet = verdict(
            etat.get("code", 1),
            etat.get("resultat"),
            etat.get("controle"),
            etat.get("chemin_zip"),
        )
        self._ecrire("")
        self._ecrire(f"=== {titre} ===")
        self._ecrire(detail)
        self.etat.configure(text=f"{titre} -- {detail.splitlines()[0]}" if detail else titre)
        self.bouton_commencer.configure(state="normal")
        self.bouton_arreter.configure(state="disabled")
        # Rien de special a faire de `complet` ici : le titre le dit deja. Il
        # reste expose par verdict() pour un appelant qui voudrait en tirer un
        # code de sortie ou une couleur.


def lancer(destination) -> None:
    racine = tk.Tk()
    Fenetre(racine, destination)
    racine.mainloop()
