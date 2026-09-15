import queue
from pathlib import Path

import pytest

from extracteur.__main__ import (
    ConnexionEchouee,
    _afficher_sessions,
    _chercher_cours,
    _code_de_sortie,
    _connecter,
    _construire_analyseur,
    _diagnostic,
    _drainer,
    _drainer_progression,
    _ecrire_rapport_final,
    _fusionner_verification,
    _lister,
    _session,
    _tout,
    _un_seul_cours,
    _verifier_mode,
    _zip_mode,
)
from extracteur.ena import SelecteurSessionsIllisible
from extracteur.manifeste import Manifeste
from extracteur.modele import Cours, Echec, Resultat, Session
from extracteur.telechargement import SessionExpiree

SESSION_HIVER = Session(code="202601", libelle="Hiver 2026")
SESSION_AUTOMNE = Session(code="202509", libelle="Automne 2025")
SESSION_ETE = Session(code="202505", libelle="Été 2025")

COURS_HIVER = Cours(
    id_site="181216",
    sigle="PHI-3900",
    titre="Éthique et professionnalisme",
    session=SESSION_HIVER,
    url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=a",
    url_resultats="/ena/site/resultats?idSite=181216",
)
COURS_ANCIEN = Cours(
    id_site="148734", sigle=None, titre="Nos biais inconscients", session=SESSION_AUTOMNE
)
COURS_ETE = Cours(
    id_site="200000",
    sigle="ABC-1000",
    titre="Cours d'été",
    session=SESSION_ETE,
    url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=c",
)


class EnaDeTest:
    """Doublure d'Ena : sert a la fois l'enumeration (sessions_disponibles,
    sites_de_session) et l'archivage minimal (modules/evaluations vides, repli
    par le menu neutre)."""

    def __init__(self, cours_par_session=None, erreur_sur_modules=None):
        self._cours_par_session = cours_par_session or {}
        self._erreur_sur_modules = erreur_sur_modules

    def sessions_disponibles(self):
        return list(self._cours_par_session.keys())

    def sites_de_session(self, session):
        return self._cours_par_session.get(session, [])

    def modules(self, cours):
        if self._erreur_sur_modules is not None:
            raise self._erreur_sur_modules
        return []

    def evaluations(self, cours):
        return []

    def resultats(self, cours):
        return []

    def parcourir_menu(self, cours, action):
        return 0

    def capturer_plan_de_cours(self, cours, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 plan")
        return True


class SessionFactice:
    """Doublure de SessionNavigateur : ne touche jamais a Playwright."""

    def __init__(self, connectee=True, transport=None, leve_a_l_attente=None):
        self.connectee = connectee
        self.ouverte = False
        self.fermee = False
        self.transport = transport or (lambda url: _ReponseOk())
        self._leve_a_l_attente = leve_a_l_attente

    def ouvrir(self):
        self.ouverte = True

    def attendre_connexion(self, delai=300):
        if self._leve_a_l_attente is not None:
            raise self._leve_a_l_attente
        return self.connectee

    def fermer(self):
        self.fermee = True


class _ReponseOk:
    statut = 200

    def morceaux(self):
        yield b"contenu"


# --- _chercher_cours : selection du cours par identifiant ---


def test_chercher_cours_trouve_par_id_site():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})

    trouve = _chercher_cours(ena, "148734")

    assert trouve is COURS_ANCIEN
    # Le cours retrouve porte ses champs d'enumeration : preuve qu'il n'a pas
    # ete reconstruit a la main.
    assert trouve.session == SESSION_AUTOMNE


def test_chercher_cours_conserve_les_url_de_l_enumeration():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    trouve = _chercher_cours(ena, "181216")

    assert trouve.url_plan_de_cours == COURS_HIVER.url_plan_de_cours
    assert trouve.url_resultats == COURS_HIVER.url_resultats


def test_chercher_cours_introuvable_rend_none():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    assert _chercher_cours(ena, "000000") is None


def test_chercher_cours_sur_enumeration_vide_rend_none():
    ena = EnaDeTest({})

    assert _chercher_cours(ena, "181216") is None


# --- _drainer : vidage de la file d'evenements ---


def test_drainer_affiche_et_vide_la_file():
    evenements = queue.Queue()
    evenements.put(("cours", "PHI-3900 Éthique"))
    evenements.put(("fichier", "plan-de-cours.pdf"))
    lignes = []

    _drainer(evenements, imprimer=lignes.append)

    assert lignes == ["  [cours] PHI-3900 Éthique", "  [fichier] plan-de-cours.pdf"]
    assert evenements.empty()


def test_drainer_sur_file_vide_n_affiche_rien():
    lignes = []

    _drainer(queue.Queue(), imprimer=lignes.append)

    assert lignes == []


def test_drainer_appele_deux_fois_ne_reaffiche_pas():
    # Vidage au fil de l'eau : un evenement deja affiche ne doit jamais
    # ressortir a un second passage.
    evenements = queue.Queue()
    evenements.put(("cours", "PHI-3900"))
    lignes = []

    _drainer(evenements, imprimer=lignes.append)
    evenements.put(("fin", "1 fichier archive"))
    _drainer(evenements, imprimer=lignes.append)

    assert lignes == ["  [cours] PHI-3900", "  [fin] 1 fichier archive"]


# --- _ecrire_rapport_final : ecriture du rapport ---


def test_ecrire_rapport_final_ecrit_le_resume_et_les_echecs(tmp_path):
    resultat = Resultat(
        fichiers_ecrits=3,
        fichiers_sautes=1,
        echecs=[Echec(cours="PHI-3900 Éthique", element="notes.pdf", cause="HTTP 403")],
    )

    chemin = _ecrire_rapport_final(tmp_path, resultat)

    assert chemin == tmp_path / "_rapport.html"
    contenu = chemin.read_text(encoding="utf-8")
    assert "PHI-3900 Éthique" in contenu
    assert "HTTP 403" in contenu
    assert "fichiers ecrits" in contenu


def test_ecrire_rapport_final_sans_echec(tmp_path):
    resultat = Resultat(fichiers_ecrits=5, fichiers_sautes=0, echecs=[])

    chemin = _ecrire_rapport_final(tmp_path, resultat)

    assert "Aucun échec" in chemin.read_text(encoding="utf-8")


# --- _code_de_sortie ---


def test_code_de_sortie_zero_si_rien_ecrit_et_aucun_echec():
    assert _code_de_sortie(Resultat(fichiers_ecrits=0, echecs=[])) == 0


def test_code_de_sortie_non_zero_si_rien_ecrit_et_des_echecs():
    assert _code_de_sortie(Resultat(fichiers_ecrits=0, echecs=[Echec("C", "a", "x")])) == 1


# --- _afficher_sessions ---


def test_afficher_sessions_montre_id_sigle_titre_et_indicateurs():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER, COURS_ANCIEN]})
    lignes = []

    _afficher_sessions(ena, imprimer=lignes.append)

    texte = "\n".join(lignes)
    assert "Hiver 2026" in texte
    assert "181216" in texte
    assert "PHI-3900" in texte
    assert "Éthique et professionnalisme" in texte
    assert "plan de cours officiel : oui" in texte
    assert "sommaire de resultats : oui" in texte
    assert "(sans sigle)" in texte
    assert "plan de cours officiel : non" in texte


def test_afficher_sessions_annonce_le_compte_en_tete():
    # Seule protection reelle contre un panneau qui plafonne durablement sur
    # un compte incomplet (palier indecidable pour le code) : l'utilisateur
    # connait son propre parcours et peut remarquer d'un coup d'oeil qu'il
    # manque des sessions. Doit donc apparaitre en tete, avant la liste.
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})
    lignes = []

    _afficher_sessions(ena, imprimer=lignes.append)

    assert lignes[0] == "2 session(s) detectee(s)."


def test_afficher_sessions_sans_cours_le_dit():
    ena = EnaDeTest({SESSION_HIVER: []})
    lignes = []

    _afficher_sessions(ena, imprimer=lignes.append)

    assert any("aucun cours" in ligne for ligne in lignes)


def test_afficher_sessions_sans_session_le_dit():
    ena = EnaDeTest({})
    lignes = []

    _afficher_sessions(ena, imprimer=lignes.append)

    assert lignes == ["Aucune session trouvee."]


# --- _un_seul_cours : bout en bout avec des doublures, sans navigateur ---


def test_un_seul_cours_archive_le_cours_trouve_par_enumeration(tmp_path, capsys):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _session: ena
    )

    assert code == 0
    assert session.ouverte is True
    assert session.fermee is True
    assert (tmp_path / "_rapport.html").exists()
    sortie = capsys.readouterr().out
    assert "[fin]" in sortie


def test_un_seul_cours_introuvable_rend_code_non_nul_sans_lever(tmp_path, capsys):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _un_seul_cours(
        "000000", tmp_path, session=session, fabrique_ena=lambda _session: ena
    )

    assert code == 2
    assert session.fermee is True
    erreur = capsys.readouterr().err
    assert "000000" in erreur


def test_un_seul_cours_connexion_non_detectee_rend_code_non_nul(tmp_path):
    session = SessionFactice(connectee=False)

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _session: EnaDeTest({})
    )

    assert code == 1
    assert session.fermee is True


def test_un_seul_cours_session_expiree_ecrit_le_rapport_partiel(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]}, erreur_sur_modules=SessionExpiree("expiree"))
    session = SessionFactice()

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _session: ena
    )

    assert code == 3
    assert session.fermee is True
    assert (tmp_path / "_rapport.html").exists()


def test_un_seul_cours_sessions_disponibles_expire_avant_toute_enumeration_ecrit_un_rapport(
    tmp_path,
):
    # Reproduit le defaut critique : le tout premier appel reseau
    # (sessions_disponibles, via _chercher_cours) leve SessionExpiree avant
    # qu'aucun cours ne soit localise -- et, dans l'ancienne version, avant
    # que l'archiveur n'existe. Un rapport doit malgre tout etre ecrit : ce
    # document dit ce qu'il reste a recuperer a la main, et doit exister
    # meme quand rien n'a ete tente.
    class EnaExpireAvantEnumeration(EnaDeTest):
        def sessions_disponibles(self):
            raise SessionExpiree("expiree")

    session = SessionFactice()

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _s: EnaExpireAvantEnumeration()
    )

    assert code == 3
    chemin_rapport = tmp_path / "_rapport.html"
    assert chemin_rapport.exists()
    contenu = chemin_rapport.read_text(encoding="utf-8")
    assert "interrompu" in contenu.lower()
    assert "Tout le contenu visé a été récupéré" not in contenu


def test_un_seul_cours_ferme_toujours_la_session_meme_sur_erreur_inattendue(tmp_path):
    class EnaCassee(EnaDeTest):
        def sessions_disponibles(self):
            raise RuntimeError("site illisible")

    session = SessionFactice()

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _session: EnaCassee({})
    )

    assert code == 1
    assert session.fermee is True


# --- _connecter : fermeture garantie du navigateur (defaut 1) ---


def test_connecter_ferme_la_session_et_relance_sur_interruption_clavier():
    # Reproduit le scenario du relecteur : l'utilisateur interrompt au clavier
    # pendant l'attente du MFA. La session doit etre fermee avant que
    # l'exception ne remonte, sinon le contexte Chromium reste orphelin et son
    # verrou de profil peut bloquer le lancement suivant.
    session = SessionFactice(leve_a_l_attente=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _connecter(session=session)

    assert session.ouverte is True
    assert session.fermee is True


def test_connecter_ferme_la_session_sur_connexion_non_detectee():
    session = SessionFactice(connectee=False)

    with pytest.raises(ConnexionEchouee):
        _connecter(session=session)

    assert session.fermee is True


def test_connecter_rend_la_session_ouverte_sur_connexion_reussie():
    session = SessionFactice(connectee=True)

    resultat = _connecter(session=session)

    assert resultat is session
    assert session.fermee is False


# --- _lister : point d'injection et fermeture garantie (defaut 5) ---


def test_lister_ferme_la_session_sur_interruption_clavier_pendant_l_attente():
    # Meme defaut que ci-dessus, mais observe depuis _lister : c'est le
    # chemin reellement emprunte par `python -m extracteur --lister`.
    session = SessionFactice(leve_a_l_attente=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        _lister(session=session, fabrique_ena=lambda _session: EnaDeTest({}))

    assert session.fermee is True


def test_lister_enumere_avec_une_session_injectee(capsys):
    session = SessionFactice(connectee=True)
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    code = _lister(session=session, fabrique_ena=lambda _session: ena)

    assert code == 0
    assert session.fermee is True
    sortie = capsys.readouterr().out
    assert "181216" in sortie


def test_lister_connexion_non_detectee_rend_code_non_nul():
    session = SessionFactice(connectee=False)

    code = _lister(session=session, fabrique_ena=lambda _session: EnaDeTest({}))

    assert code == 1
    assert session.fermee is True


# --- _chercher_cours : normalisation de l'identifiant (defaut 2) ---


def test_chercher_cours_normalise_les_espaces_et_retours_de_ligne():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    trouve = _chercher_cours(ena, "  181216\n")

    assert trouve is COURS_HIVER


# --- _code_de_sortie : distinction des echecs partiels (defaut 3) ---


def test_code_de_sortie_echecs_partiels_rend_un_code_dedie():
    # Des fichiers ecrits ET des echecs consignes ne sont pas un succes
    # complet : un script qui ne lit que le code de sortie doit pouvoir s'en
    # rendre compte. Scenario du projet : un cours ancien produit 9 fichiers
    # et 3 echecs.
    code = _code_de_sortie(Resultat(fichiers_ecrits=9, echecs=[Echec("C", "a", "x")] * 3))

    assert code != 0


def test_code_de_sortie_zero_uniquement_sans_aucun_echec():
    assert _code_de_sortie(Resultat(fichiers_ecrits=2, echecs=[])) == 0


# --- argparse : --lister et --un-seul-cours mutuellement exclusifs (defaut 4) ---


def test_lister_et_un_seul_cours_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--lister", "--un-seul-cours", "181216"])


def test_lister_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--lister"])

    assert arguments.lister is True
    assert arguments.id_site is None


def test_un_seul_cours_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--un-seul-cours", "181216"])

    assert arguments.id_site == "181216"
    assert arguments.lister is False


# --- argparse : --diagnostic mutuellement exclusif avec les deux autres ---


def test_lister_et_diagnostic_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--lister", "--diagnostic"])


def test_un_seul_cours_et_diagnostic_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--un-seul-cours", "181216", "--diagnostic"])


def test_diagnostic_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--diagnostic"])

    assert arguments.diagnostic is True
    assert arguments.lister is False
    assert arguments.id_site is None


# --- _diagnostic : etat des lieux du DOM, sans rien telecharger ---


class EnaDiagnosticDeTest:
    """Doublure d'Ena : sert uniquement diagnostiquer_sessions()."""

    def __init__(self, rapport):
        self._rapport = rapport

    def diagnostiquer_sessions(self):
        return self._rapport


RAPPORT_DIAGNOSTIC = {
    "candidats": [
        ("[role=option]", 0, []),
        (".mpo-deroulant-element", 0, []),
        ("li", 12, ["Hiver 2027", "Automne 2026"]),
        ("a", 3, ["Hiver 2026", "Automne 2025", "Profil"]),
        ("forme du libelle (saison + annee)", 2, ["Hiver 2026", "Automne 2025"]),
    ],
    "liens_id_site": 9,
}


def test_diagnostic_affiche_le_compte_et_l_echantillon_par_candidat():
    session = SessionFactice(connectee=True)
    lignes = []

    code = _diagnostic(
        session=session,
        fabrique_ena=lambda _session: EnaDiagnosticDeTest(RAPPORT_DIAGNOSTIC),
        imprimer=lignes.append,
    )

    assert code == 0
    assert session.fermee is True
    texte = "\n".join(lignes)
    assert "[role=option] : 0 element(s)" in texte
    assert "a : 3 element(s)" in texte
    assert "Hiver 2026" in texte
    assert "liens portant idSite= dans la page : 9" in texte


def test_diagnostic_connexion_non_detectee_rend_code_non_nul():
    session = SessionFactice(connectee=False)

    code = _diagnostic(
        session=session, fabrique_ena=lambda _session: EnaDiagnosticDeTest(RAPPORT_DIAGNOSTIC)
    )

    assert code == 1
    assert session.fermee is True


# --- SelecteurSessionsIllisible : jamais de trace brute, code non nul ---


def test_lister_selecteur_sessions_illisible_rend_code_non_nul_sans_lever():
    class EnaCassee(EnaDeTest):
        def sessions_disponibles(self):
            raise SelecteurSessionsIllisible("panneau illisible")

    session = SessionFactice(connectee=True)

    code = _lister(session=session, fabrique_ena=lambda _session: EnaCassee({}))

    assert code == 1
    assert session.fermee is True


def test_un_seul_cours_selecteur_sessions_illisible_sans_trace_brute(tmp_path, capsys):
    class EnaCassee(EnaDeTest):
        def sessions_disponibles(self):
            raise SelecteurSessionsIllisible("panneau illisible")

    session = SessionFactice()

    code = _un_seul_cours(
        "181216", tmp_path, session=session, fabrique_ena=lambda _session: EnaCassee({})
    )

    assert code == 1
    assert session.fermee is True
    erreur = capsys.readouterr().err
    assert "panneau illisible" in erreur
    assert "Traceback" not in erreur


# --- _drainer_progression : progression au fil de l'eau sur plusieurs sessions ---


def test_drainer_progression_annonce_la_session_et_le_rang_global():
    # Ordre reel : toute l'enumeration (donc tous les evenements 'session')
    # se termine avant que l'archivage -- et donc les evenements 'cours' --
    # ne commence. La session affichee a cote de chaque cours doit venir du
    # 'plan' pose juste avant, pas d'un pointeur mis a jour par le dernier
    # evenement 'session' vu : celui-ci serait fige sur la derniere session
    # enumeree pour tous les cours, quelle que soit leur session reelle.
    evenements = queue.Queue()
    evenements.put(("session", (1, 2, "Automne 2025", "enumeration en cours...")))
    evenements.put(("session", (1, 2, "Automne 2025", "1 cours")))
    evenements.put(("session", (2, 2, "Hiver 2026", "enumeration en cours...")))
    evenements.put(("session", (2, 2, "Hiver 2026", "1 cours")))
    evenements.put(("plan", ["Automne 2025", "Hiver 2026"]))
    evenements.put(("cours", "AAA-1000 Cours ancien"))
    evenements.put(("fichier", "plan-de-cours.pdf"))
    evenements.put(("cours", "PHI-3900 Éthique"))
    lignes = []
    etat = {}

    _drainer_progression(evenements, etat, imprimer=lignes.append)

    assert "\n=== Session 1/2 : Automne 2025 - 1 cours ===" in lignes
    assert "  [Automne 2025] (1/2) AAA-1000 Cours ancien" in lignes
    assert "  [fichier] plan-de-cours.pdf" in lignes
    assert "\n=== Session 2/2 : Hiver 2026 - 1 cours ===" in lignes
    assert "  [Hiver 2026] (2/2) PHI-3900 Éthique" in lignes


def test_drainer_progression_appele_deux_fois_ne_reaffiche_pas():
    # Vidage au fil de l'eau, comme _drainer : un evenement deja affiche ne
    # doit jamais ressortir a un second passage, meme avec l'etat de
    # progression maintenu entre deux appels.
    evenements = queue.Queue()
    evenements.put(("plan", ["Hiver 2026"]))
    evenements.put(("cours", "PHI-3900"))
    lignes = []
    etat = {}

    _drainer_progression(evenements, etat, imprimer=lignes.append)
    evenements.put(("fin", "1 fichier archive"))
    _drainer_progression(evenements, etat, imprimer=lignes.append)

    assert lignes[-1] == "  [fin] 1 fichier archive"
    # Le cours deja affiche au premier passage ne doit pas ressortir au
    # second, meme avec l'etat de progression maintenu entre deux appels.
    assert lignes.count("  [Hiver 2026] (1/1) PHI-3900") == 1


# --- _session : archive tous les cours d'une session, tolerant sur le libelle ---


def test_session_archive_seulement_les_cours_de_la_session_demandee(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})
    session = SessionFactice()

    code = _session("Hiver 2026", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 0
    assert session.fermee is True
    assert (tmp_path / "_rapport.html").exists()
    # Le cours de la session demandee est bien archive...
    assert (tmp_path / SESSION_HIVER.dossier() / COURS_HIVER.dossier()).exists()
    # ... mais pas celui d'une autre session.
    assert not (tmp_path / SESSION_AUTOMNE.dossier()).exists()


def test_session_tolere_casse_espaces_de_tete_et_de_fin_et_accents(tmp_path):
    # Le libelle sera tape a la main : "  ete 2025  " doit retrouver "Été
    # 2025" sans que l'utilisateur ait a reproduire l'accent ou la casse
    # exacts affiches par le selecteur.
    ena = EnaDeTest({SESSION_ETE: [COURS_ETE], SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _session("  ete 2025  ", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 0
    assert (tmp_path / SESSION_ETE.dossier() / COURS_ETE.dossier()).exists()
    assert not (tmp_path / SESSION_HIVER.dossier()).exists()


def test_session_introuvable_affiche_les_libelles_disponibles_et_rend_code_non_nul(
    tmp_path, capsys
):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})
    session = SessionFactice()

    code = _session("Hiver 2099", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 1
    assert session.fermee is True
    assert not (tmp_path / "_rapport.html").exists()
    erreur = capsys.readouterr().err
    assert "Hiver 2099" in erreur
    assert "Hiver 2026" in erreur
    assert "Automne 2025" in erreur


# --- _tout : enchaine toutes les sessions, de la plus ancienne a la plus recente ---


def test_tout_archive_toutes_les_sessions_de_la_plus_ancienne_a_la_plus_recente(tmp_path, capsys):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 0
    assert session.fermee is True
    assert (tmp_path / SESSION_AUTOMNE.dossier() / COURS_ANCIEN.dossier()).exists()
    assert (tmp_path / SESSION_HIVER.dossier() / COURS_HIVER.dossier()).exists()
    sortie = capsys.readouterr().out
    # Automne 2025 (la plus ancienne) doit etre traitee avant Hiver 2026.
    assert sortie.index("Automne 2025") < sortie.index("Hiver 2026")


class EnaAvecExpirationSurUnCours(EnaDeTest):
    """Doublure d'Ena : n'expire que pour le cours designe, pour simuler une
    session qui echoue au milieu d'un enchainement de plusieurs sessions --
    les cours deja archives avant elle doivent rester comptabilises dans le
    rapport final."""

    def __init__(self, cours_par_session, cours_qui_expire):
        super().__init__(cours_par_session)
        self._cours_qui_expire = cours_qui_expire

    def modules(self, cours):
        if cours == self._cours_qui_expire:
            raise SessionExpiree("expiree")
        return super().modules(cours)


def test_tout_session_expiree_au_milieu_ecrit_le_rapport_de_ce_qui_est_fait(tmp_path):
    cours_automne = Cours(
        id_site="199999",
        sigle="AAA-1000",
        titre="Cours ancien avec plan",
        session=SESSION_AUTOMNE,
        # Identifiant distinct de celui de COURS_HIVER : deux url differentes,
        # sinon le manifeste (cle = url) considererait le second telechargement
        # comme deja fait et le compterait en "saute", pas en "ecrit".
        url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=automne",
    )
    ena = EnaAvecExpirationSurUnCours(
        {SESSION_AUTOMNE: [cours_automne], SESSION_HIVER: [COURS_HIVER]},
        cours_qui_expire=COURS_HIVER,
    )
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 3
    assert session.fermee is True
    chemin_rapport = tmp_path / "_rapport.html"
    assert chemin_rapport.exists()
    contenu = chemin_rapport.read_text(encoding="utf-8")
    # Le cours de la session la plus ancienne (Automne, archivee avant que
    # Hiver n'expire) reste comptabilise : le rapport couvre l'ensemble de
    # ce qui a ete tente, pas seulement la derniere session.
    assert "<li>fichiers ecrits : 2</li>" in contenu


def test_tout_sessions_disponibles_expire_avant_toute_enumeration_ecrit_rapport_interrompu(
    tmp_path,
):
    # Reproduit le defaut critique : sessions_disponibles() (le tout premier
    # appel reseau de resoudre_sessions) leve SessionExpiree avant meme de
    # savoir combien de sessions sont visees. Un rapport doit malgre tout
    # etre ecrit, et dire que le travail a ete interrompu.
    class EnaExpireAvantEnumeration(EnaDeTest):
        def sessions_disponibles(self):
            raise SessionExpiree("expiree")

    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: EnaExpireAvantEnumeration())

    assert code == 3
    chemin_rapport = tmp_path / "_rapport.html"
    assert chemin_rapport.exists()
    contenu = chemin_rapport.read_text(encoding="utf-8")
    assert "interrompu" in contenu.lower()
    assert "Tout le contenu visé a été récupéré" not in contenu


def test_tout_sites_de_session_expire_apres_session_vide_rapport_ne_dit_jamais_tout_recupere(
    tmp_path,
):
    # Reproduit le defaut critique : la session Automne (la plus ancienne,
    # traitee en premier par --tout) est legitimement vide, puis
    # l'enumeration de la session Hiver qui suit leve SessionExpiree.
    # L'archiveur existe deja, mais archiver() n'a jamais ete appele : zero
    # succes, zero echec. Le rapport ne doit jamais affirmer que tout le
    # contenu vise a ete recupere.
    class EnaExpireSurDeuxiemeSession(EnaDeTest):
        def __init__(self):
            super().__init__({SESSION_AUTOMNE: [], SESSION_HIVER: [COURS_HIVER]})

        def sites_de_session(self, session):
            if session == SESSION_HIVER:
                raise SessionExpiree("expiree")
            return super().sites_de_session(session)

    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: EnaExpireSurDeuxiemeSession())

    assert code == 3
    chemin_rapport = tmp_path / "_rapport.html"
    assert chemin_rapport.exists()
    contenu = chemin_rapport.read_text(encoding="utf-8")
    assert "Tout le contenu visé a été récupéré" not in contenu
    assert "interrompu" in contenu.lower()


def test_tout_interruption_apres_echec_partiel_liste_echecs_et_non_tentes(tmp_path):
    # Un cours produit un echec ordinaire (pas une expiration), puis la
    # session expire sur le cours suivant, laissant un troisieme cours
    # jamais tente. Le rapport doit lister l'echec ordinaire ET signaler,
    # dans la meme page, ce qui n'a jamais ete tente.
    cours_echec = Cours(
        id_site="300001", sigle="ECH-1000", titre="Cours en echec", session=SESSION_AUTOMNE
    )
    cours_jamais_tente = Cours(
        id_site="300002", sigle="JAM-1000", titre="Cours jamais tente", session=SESSION_HIVER
    )

    class EnaAvecEchecPuisExpiration(EnaDeTest):
        def __init__(self):
            super().__init__(
                {
                    SESSION_AUTOMNE: [cours_echec],
                    SESSION_HIVER: [COURS_HIVER, cours_jamais_tente],
                }
            )

        def modules(self, cours):
            if cours == cours_echec:
                raise RuntimeError("panne simulee")
            if cours == COURS_HIVER:
                raise SessionExpiree("expiree")
            return super().modules(cours)

    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: EnaAvecEchecPuisExpiration())

    assert code == 3
    contenu = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "panne simulee" in contenu
    assert "interrompu" in contenu.lower()
    assert "jamais ete tentes" in contenu.lower()


def test_message_console_distingue_rien_telecharge_de_reprise_possible(tmp_path, capsys):
    # Le message d'expiration ne doit jamais affirmer une reprise idempotente
    # quand rien n'a encore ete telecharge (expiration pendant l'enumeration).
    class EnaExpireAvantEnumeration(EnaDeTest):
        def sessions_disponibles(self):
            raise SessionExpiree("expiree")

    session_rien_telecharge = SessionFactice()
    _tout(
        tmp_path / "cas-rien-telecharge",
        session=session_rien_telecharge,
        fabrique_ena=lambda _s: EnaExpireAvantEnumeration(),
    )
    erreur_rien_telecharge = capsys.readouterr().err.lower()
    assert "aucun fichier n'a encore ete traite" in erreur_rien_telecharge
    assert "idempotente" not in erreur_rien_telecharge

    ena_reprise_possible = EnaAvecExpirationSurUnCours(
        {SESSION_AUTOMNE: [COURS_ANCIEN], SESSION_HIVER: [COURS_HIVER]},
        cours_qui_expire=COURS_HIVER,
    )
    session_reprise_possible = SessionFactice()
    _tout(
        tmp_path / "cas-reprise-possible",
        session=session_reprise_possible,
        fabrique_ena=lambda _s: ena_reprise_possible,
    )
    erreur_reprise_possible = capsys.readouterr().err.lower()
    assert "idempotente" in erreur_reprise_possible
    assert "aucun fichier n'a encore ete traite" not in erreur_reprise_possible


def test_session_et_tout_connexion_non_detectee_rend_code_non_nul():
    session = SessionFactice(connectee=False)

    code_session = _session(
        "Hiver 2026", Path("."), session=session, fabrique_ena=lambda _s: EnaDeTest({})
    )
    assert code_session == 1

    session2 = SessionFactice(connectee=False)
    code_tout = _tout(Path("."), session=session2, fabrique_ena=lambda _s: EnaDeTest({}))
    assert code_tout == 1


# --- argparse : --session et --tout mutuellement exclusifs avec les autres modes ---


def test_session_et_tout_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--session", "Automne 2022", "--tout"])


def test_lister_et_session_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--lister", "--session", "Automne 2022"])


def test_un_seul_cours_et_tout_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--un-seul-cours", "181216", "--tout"])


def test_diagnostic_et_session_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--diagnostic", "--session", "Automne 2022"])


def test_session_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--session", "Automne 2022"])

    assert arguments.session_cible == "Automne 2022"
    assert arguments.tout is False


def test_tout_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--tout"])

    assert arguments.tout is True
    assert arguments.session_cible is None


# --- argparse : --verifier et --zip mutuellement exclusifs avec les autres modes ---


def test_verifier_et_zip_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--verifier", "--zip"])


def test_lister_et_verifier_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--lister", "--verifier"])


def test_tout_et_zip_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--tout", "--zip"])


def test_verifier_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--verifier"])

    assert arguments.verifier is True
    assert arguments.zip_ is False


def test_zip_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--zip"])

    assert arguments.zip_ is True
    assert arguments.verifier is False


# --- _fusionner_verification : les anomalies deviennent des Echec ---


def test_fusionner_verification_ajoute_un_echec_par_anomalie():
    resultat = Resultat(fichiers_ecrits=2)
    controle = {"inscrits": 3, "manquants": ["a.pdf"], "taille_incorrecte": ["b.pdf"]}

    _fusionner_verification(resultat, controle)

    assert len(resultat.echecs) == 2
    causes = {echec.element: echec.cause for echec in resultat.echecs}
    assert "manquant" in causes["a.pdf"]
    assert "taille" in causes["b.pdf"]


def test_fusionner_verification_sans_anomalie_ne_change_rien():
    resultat = Resultat(fichiers_ecrits=2)

    _fusionner_verification(resultat, {"inscrits": 1, "manquants": [], "taille_incorrecte": []})

    assert resultat.echecs == []


# --- _verifier_mode : verification en console, sans reseau ---


def test_verifier_mode_dossier_introuvable_rend_code_non_nul(tmp_path, capsys):
    code = _verifier_mode(tmp_path / "absent")

    assert code == 1
    assert "introuvable" in capsys.readouterr().err


def test_verifier_mode_archive_coherente_rend_zero(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"abc")
    Manifeste(tmp_path).ajouter("a.pdf", 3, "x", "/contenu/a.pdf")
    lignes = []

    code = _verifier_mode(tmp_path, imprimer=lignes.append)

    assert code == 0
    assert any("0 manquants" in ligne for ligne in lignes)


def test_verifier_mode_signale_les_anomalies_et_rend_code_non_nul(tmp_path):
    Manifeste(tmp_path).ajouter("absent.pdf", 3, "x", "/contenu/absent.pdf")
    lignes = []

    code = _verifier_mode(tmp_path, imprimer=lignes.append)

    assert code == 1
    assert any("absent.pdf" in ligne for ligne in lignes)


# --- _zip_mode : compression en console, sans reseau ---


def test_zip_mode_dossier_introuvable_rend_code_non_nul(tmp_path, capsys):
    code = _zip_mode(tmp_path / "absent")

    assert code == 1
    assert "introuvable" in capsys.readouterr().err


def test_zip_mode_ecrit_le_zip_a_cote_du_dossier(tmp_path):
    archive = tmp_path / "Archive"
    archive.mkdir()
    (archive / "a.pdf").write_bytes(b"abc")
    lignes = []

    code = _zip_mode(archive, imprimer=lignes.append)

    cible = tmp_path / "Archive.zip"
    assert code == 0
    assert cible.exists()
    assert any(str(cible) in ligne for ligne in lignes)


def test_zip_mode_echec_d_ecriture_rend_code_non_nul(tmp_path, monkeypatch, capsys):
    archive = tmp_path / "Archive"
    archive.mkdir()

    def leve(*_args, **_kwargs):
        raise OSError("disque plein")

    monkeypatch.setattr("extracteur.__main__.creer_zip", leve)

    code = _zip_mode(archive)

    assert code == 1
    assert "disque plein" in capsys.readouterr().err


# --- verification finale branchee sur --un-seul-cours, --session et --tout ---


def test_un_seul_cours_anomalie_de_verification_degrade_le_code_de_sortie(
    tmp_path, monkeypatch, capsys
):
    # Un archivage par ailleurs reussi (tous les fichiers prevus ecrits, aucun
    # echec de telechargement) ne doit plus rendre 0 si la verification
    # finale trouve une anomalie : celle-ci doit peser sur le code de sortie
    # exactement comme un echec de telechargement.
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    monkeypatch.setattr(
        "extracteur.__main__.verifier",
        lambda destination: {"inscrits": 1, "manquants": ["a.pdf"], "taille_incorrecte": []},
    )

    code = _un_seul_cours("181216", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 4
    rapport = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "a.pdf" in rapport
    sortie = capsys.readouterr().out
    assert "Verification : 1 inscrits, 1 manquants, 0 de taille incorrecte" in sortie


def test_session_anomalie_de_verification_degrade_le_code_de_sortie(tmp_path, monkeypatch):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    monkeypatch.setattr(
        "extracteur.__main__.verifier",
        lambda destination: {"inscrits": 1, "manquants": [], "taille_incorrecte": ["b.pdf"]},
    )

    code = _session("Hiver 2026", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 4
    rapport = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "b.pdf" in rapport


def test_tout_verification_coherente_laisse_le_code_de_sortie_a_zero(tmp_path, capsys):
    # Contre-epreuve : une verification qui ne trouve rien a signaler ne doit
    # rien degrader, et son resume doit tout de meme etre visible.
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 0
    sortie = capsys.readouterr().out
    assert "Verification :" in sortie
    assert "0 manquants" in sortie
