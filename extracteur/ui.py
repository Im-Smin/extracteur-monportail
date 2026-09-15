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

MESSAGE_NAVIGATEUR = (
    "Une fenetre de navigateur separee va s'ouvrir : connectez-vous DEDANS, "
    "MFA compris. C'est la seule dont le programme peut lire les cookies. "
    "L'authentification est redemandee a chaque lancement -- c'est voulu, "
    "aucun profil de navigateur n'est conserve."
)


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
                "Entrez l'idSite du cours, par exemple : 181216. Il se lit dans "
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

    if annulation is not None and annulation.is_set():
        imprimer(
            "Archivage interrompu : rien n'a ete compresse. Relancez pour reprendre "
            "la ou vous en etiez, le ZIP sera produit a ce moment-la."
        )
        return etat

    cible = nom_du_zip(destination)
    imprimer(f"\nCompression vers {cible} ...")
    try:
        etat["chemin_zip"] = compresser(Path(destination), cible)
        imprimer(f"Archive compressee : {etat['chemin_zip']}")
    except OSError as erreur:
        # L'archivage lui-meme a reussi : un ZIP manquant ne doit pas effacer
        # ce resultat, seulement etre dit clairement. Le dossier reste
        # compressible a part, par le mode --zip.
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

        self._ecrire(MESSAGE_NAVIGATEUR)
        self._ecrire("")

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
        ttk.Label(cadre, text="idSite, ex. 181216").grid(row=2, column=2, sticky="w")

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
        self._ecrire(f"=== Archivage vers {destination} ===")

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
        self.etat.configure(text="Arret demande : fin du cours en cours, patientez...")
        self._ecrire("Arret demande. L'archivage s'arretera a la fin du cours en cours.")

    # --- fil de travail ---

    def _travailler(self, portee: str, argument: str, destination: Path) -> None:
        def publier(texte):
            self.evenements.put(("ligne", str(texte)))

        try:
            etat = executer_archivage(
                portee,
                argument,
                destination,
                imprimer=publier,
                imprimer_erreur=publier,
                annulation=self.annulation,
            )
        except Exception as erreur:  # filet ultime : jamais de fil muet
            publier(f"ECHEC inattendu : {erreur}")
            etat = {"code": 1, "resultat": None, "controle": None, "chemin_zip": None}
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
