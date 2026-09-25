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
    _message_echec_connexion,
    _session,
    _tout,
    _un_seul_cours,
    _verifier_mode,
    _zip_mode,
)
from extracteur.ena import SelecteurSessionsIllisible, SelecteurSessionsIndisponible
from extracteur.manifeste import Manifeste
from extracteur.modele import Cours, Echec, Resultat, Session
from extracteur.telechargement import SessionExpiree

SESSION_HIVER = Session(code="202601", libelle="Hiver 2026")
SESSION_AUTOMNE = Session(code="202509", libelle="Automne 2025")
SESSION_ETE = Session(code="202505", libelle="Été 2025")
# Session future et vide, exactement celle qui a fait perdre l'inventaire
# complet de douze sessions en conditions reelles (voir le rapport de bogue :
# SelecteurSessionsIndisponible sur "Hiver 2027", premiere session traitee).
SESSION_HIVER_2027 = Session(code="202701", libelle="Hiver 2027")

COURS_HIVER = Cours(
    id_site="100001",
    sigle="ABC-1000",
    titre="Éthique et professionnalisme",
    session=SESSION_HIVER,
    url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=a",
    url_resultats="/ena/site/resultats?idSite=100001",
)
COURS_ANCIEN = Cours(
    id_site="100007", sigle=None, titre="Nos biais inconscients", session=SESSION_AUTOMNE
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

    def __init__(
        self,
        connectee=True,
        transport=None,
        leve_a_l_attente=None,
        dernier_domaine_observe=None,
    ):
        self.connectee = connectee
        self.ouverte = False
        self.fermee = False
        self.transport = transport or (lambda url: _ReponseOk())
        self._leve_a_l_attente = leve_a_l_attente
        # Pose par la vraie attendre_connexion a chaque sondage ; injectable
        # ici pour verifier que le message d'echec le reprend.
        self.dernier_domaine_observe = dernier_domaine_observe

    def ouvrir(self):
        self.ouverte = True

    def attendre_connexion(self, delai=300, annulation=None):
        if self._leve_a_l_attente is not None:
            raise self._leve_a_l_attente
        if annulation is not None and annulation.is_set():
            return False
        return self.connectee

    def fermer(self):
        self.fermee = True


class _ReponseOk:
    statut = 200

    def morceaux(self):
        yield b"contenu"


class EnaAvecEchecSurUneSession(EnaDeTest):
    """Doublure d'Ena : sites_de_session leve une exception pour la session
    designee, comme SelecteurSessionsIndisponible en conditions reelles quand
    le bouton du selecteur ne confirme jamais le changement de session --
    sans emporter le reste de l'enumeration."""

    def __init__(self, cours_par_session, session_qui_echoue, erreur=None):
        super().__init__(cours_par_session)
        self._session_qui_echoue = session_qui_echoue
        self._erreur = erreur or SelecteurSessionsIndisponible(
            "le bouton du selecteur de sessions n'a jamais confirme le passage"
        )

    def sites_de_session(self, session):
        if session == self._session_qui_echoue:
            raise self._erreur
        return super().sites_de_session(session)


class EnaExpireSurUneSession(EnaDeTest):
    """Doublure d'Ena : sites_de_session leve SessionExpiree pour la session
    designee -- a la difference de EnaAvecEchecSurUneSession, cette exception
    ne doit jamais etre absorbee par l'isolation par session."""

    def __init__(self, cours_par_session, session_qui_expire):
        super().__init__(cours_par_session)
        self._session_qui_expire = session_qui_expire

    def sites_de_session(self, session):
        if session == self._session_qui_expire:
            raise SessionExpiree("expiree")
        return super().sites_de_session(session)


# --- _chercher_cours : selection du cours par identifiant ---


def test_chercher_cours_trouve_par_id_site():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})

    trouve = _chercher_cours(ena, "100007")

    assert trouve is COURS_ANCIEN
    # Le cours retrouve porte ses champs d'enumeration : preuve qu'il n'a pas
    # ete reconstruit a la main.
    assert trouve.session == SESSION_AUTOMNE


def test_chercher_cours_conserve_les_url_de_l_enumeration():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    trouve = _chercher_cours(ena, "100001")

    assert trouve.url_plan_de_cours == COURS_HIVER.url_plan_de_cours
    assert trouve.url_resultats == COURS_HIVER.url_resultats


def test_chercher_cours_introuvable_rend_none():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    assert _chercher_cours(ena, "000000") is None


def test_chercher_cours_sur_enumeration_vide_rend_none():
    ena = EnaDeTest({})

    assert _chercher_cours(ena, "100001") is None


# --- _drainer : vidage de la file d'evenements ---


def test_drainer_affiche_et_vide_la_file():
    evenements = queue.Queue()
    evenements.put(("cours", "ABC-1000 Éthique"))
    evenements.put(("fichier", "plan-de-cours.pdf"))
    lignes = []

    _drainer(evenements, imprimer=lignes.append)

    assert lignes == ["  [cours] ABC-1000 Éthique", "  [fichier] plan-de-cours.pdf"]
    assert evenements.empty()


def test_drainer_sur_file_vide_n_affiche_rien():
    lignes = []

    _drainer(queue.Queue(), imprimer=lignes.append)

    assert lignes == []


def test_drainer_appele_deux_fois_ne_reaffiche_pas():
    # Vidage au fil de l'eau : un evenement deja affiche ne doit jamais
    # ressortir a un second passage.
    evenements = queue.Queue()
    evenements.put(("cours", "ABC-1000"))
    lignes = []

    _drainer(evenements, imprimer=lignes.append)
    evenements.put(("fin", "1 fichier archive"))
    _drainer(evenements, imprimer=lignes.append)

    assert lignes == ["  [cours] ABC-1000", "  [fin] 1 fichier archive"]


# --- _ecrire_rapport_final : ecriture du rapport ---


def test_ecrire_rapport_final_ecrit_le_resume_et_les_echecs(tmp_path):
    resultat = Resultat(
        fichiers_ecrits=3,
        fichiers_sautes=1,
        echecs=[Echec(cours="ABC-1000 Éthique", element="notes.pdf", cause="HTTP 403")],
    )

    chemin = _ecrire_rapport_final(tmp_path, resultat)

    assert chemin == tmp_path / "_rapport.html"
    contenu = chemin.read_text(encoding="utf-8")
    assert "ABC-1000 Éthique" in contenu
    assert "HTTP 403" in contenu
    assert "fichiers ecrits" in contenu


def test_ecrire_rapport_final_sans_echec(tmp_path):
    resultat = Resultat(fichiers_ecrits=5, fichiers_sautes=0, echecs=[])

    chemin = _ecrire_rapport_final(tmp_path, resultat)

    assert "Aucun échec" in chemin.read_text(encoding="utf-8")


def test_ecrire_rapport_final_fichier_verrouille_retourne_none(tmp_path, capsys):
    """Un fichier verrouille ne doit pas cracher, mais afficher un message et retourner None."""
    from unittest.mock import patch

    resultat = Resultat(fichiers_ecrits=5, fichiers_sautes=0, echecs=[])

    with patch("extracteur.__main__.ecrire_rapport", side_effect=OSError("Permission denied")):
        chemin = _ecrire_rapport_final(tmp_path, resultat)

    assert chemin is None
    stderr = capsys.readouterr().err
    assert "ATTENTION" in stderr
    assert "Permission denied" in stderr
    assert "_rapport.html" in stderr


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
    assert "100001" in texte
    assert "ABC-1000" in texte
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


def test_afficher_sessions_isole_une_session_en_echec_et_continue():
    # Reproduit le defaut critique constate en conditions reelles :
    # "Hiver 2027" (future, vraisemblablement vide) leve
    # SelecteurSessionsIndisponible et ne doit pas emporter les onze autres
    # sessions de l'historique.
    ena = EnaAvecEchecSurUneSession(
        {
            SESSION_HIVER_2027: [],
            SESSION_HIVER: [COURS_HIVER],
            SESSION_AUTOMNE: [COURS_ANCIEN],
        },
        session_qui_echoue=SESSION_HIVER_2027,
    )
    lignes = []

    sessions_en_echec = _afficher_sessions(ena, imprimer=lignes.append)

    assert sessions_en_echec == ["Hiver 2027"]
    texte = "\n".join(lignes)
    assert "ECHEC" in texte
    # Les deux sessions suivantes sont bien traitees malgre l'echec de la
    # premiere.
    assert "100001" in texte
    assert "100007" in texte


def test_afficher_sessions_ne_capture_jamais_sessionexpiree():
    # SessionExpiree doit rester fatale : une session d'authentification
    # morte ne se repare pas en passant a la session suivante.
    ena = EnaExpireSurUneSession(
        {SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]},
        session_qui_expire=SESSION_HIVER,
    )

    with pytest.raises(SessionExpiree):
        _afficher_sessions(ena)


# --- _un_seul_cours : bout en bout avec des doublures, sans navigateur ---


def test_un_seul_cours_archive_le_cours_trouve_par_enumeration(tmp_path, capsys):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _un_seul_cours(
        "100001", tmp_path, session=session, fabrique_ena=lambda _session: ena
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
        "100001", tmp_path, session=session, fabrique_ena=lambda _session: EnaDeTest({})
    )

    assert code == 1
    assert session.fermee is True


def test_un_seul_cours_session_expiree_ecrit_le_rapport_partiel(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]}, erreur_sur_modules=SessionExpiree("expiree"))
    session = SessionFactice()

    code = _un_seul_cours(
        "100001", tmp_path, session=session, fabrique_ena=lambda _session: ena
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
        "100001", tmp_path, session=session, fabrique_ena=lambda _s: EnaExpireAvantEnumeration()
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
        "100001", tmp_path, session=session, fabrique_ena=lambda _session: EnaCassee({})
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


# --- _message_echec_connexion : message d'echec informatif (defaut 4) ---


def test_message_echec_mentionne_le_domaine_microsoft_observe():
    """Une page restee sur le domaine de connexion Microsoft suggere que la
    connexion n'a jamais abouti : le message doit le dire, pas se limiter au
    delai ecoule."""
    session = SessionFactice(dernier_domaine_observe="login.microsoftonline.com")

    message = _message_echec_connexion(session)

    assert "login.microsoftonline.com" in message
    assert "jamais aboutie" in message or "jamais abouti" in message


def test_message_echec_mentionne_le_bon_domaine_sans_marqueur():
    """Une page deja sur sitescours.monportail.ulaval.ca sans marqueur
    suggere un chargement incomplet ou un acces refuse, pas une connexion
    manquee : le message doit distinguer ce cas du precedent."""
    session = SessionFactice(dernier_domaine_observe="sitescours.monportail.ulaval.ca")

    message = _message_echec_connexion(session)

    assert "sitescours.monportail.ulaval.ca" in message
    assert "charger" in message or "refuse" in message


def test_message_echec_sans_domaine_observe_reste_generique():
    """Une doublure qui ne pose jamais dernier_domaine_observe (page jamais
    chargee) ne doit pas faire planter le message, seulement rester generique."""
    session = SessionFactice()
    session.dernier_domaine_observe = None

    message = _message_echec_connexion(session)

    assert "connexion non detectee" in message


def test_connecter_leve_avec_le_domaine_observe_dans_le_message():
    """Integration : _connecter propage bien le message enrichi dans
    ConnexionEchouee, pas le texte muet d'origine."""
    session = SessionFactice(connectee=False, dernier_domaine_observe="login.microsoftonline.com")

    with pytest.raises(ConnexionEchouee) as info:
        _connecter(session=session)

    assert "login.microsoftonline.com" in str(info.value)
    assert session.fermee is True


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
    assert "100001" in sortie


def test_lister_connexion_non_detectee_rend_code_non_nul():
    session = SessionFactice(connectee=False)

    code = _lister(session=session, fabrique_ena=lambda _session: EnaDeTest({}))

    assert code == 1
    assert session.fermee is True


def test_lister_rend_code_non_nul_si_une_session_echoue_mais_liste_le_reste(capsys):
    # Le code de sortie doit refleter un inventaire partiel : --lister n'ecrit
    # rien sur disque, mais un inventaire incomplet n'est pas un succes.
    ena = EnaAvecEchecSurUneSession(
        {SESSION_HIVER_2027: [], SESSION_HIVER: [COURS_HIVER]},
        session_qui_echoue=SESSION_HIVER_2027,
    )
    session = SessionFactice()

    code = _lister(session=session, fabrique_ena=lambda _s: ena)

    assert code == 1
    assert session.fermee is True
    erreur = capsys.readouterr().err
    assert "Hiver 2027" in erreur


def test_lister_session_expiree_sur_une_session_interrompt_tout():
    ena = EnaExpireSurUneSession(
        {SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]},
        session_qui_expire=SESSION_HIVER,
    )
    session = SessionFactice()

    code = _lister(session=session, fabrique_ena=lambda _s: ena)

    assert code == 3
    assert session.fermee is True


# --- _chercher_cours : normalisation de l'identifiant (defaut 2) ---


def test_chercher_cours_normalise_les_espaces_et_retours_de_ligne():
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})

    trouve = _chercher_cours(ena, "  100001\n")

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
        analyseur.parse_args(["--lister", "--un-seul-cours", "100001"])


def test_lister_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--lister"])

    assert arguments.lister is True
    assert arguments.id_site is None


def test_un_seul_cours_seul_est_accepte():
    analyseur = _construire_analyseur()

    arguments = analyseur.parse_args(["--un-seul-cours", "100001"])

    assert arguments.id_site == "100001"
    assert arguments.lister is False


# --- argparse : --diagnostic mutuellement exclusif avec les deux autres ---


def test_lister_et_diagnostic_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--lister", "--diagnostic"])


def test_un_seul_cours_et_diagnostic_ensemble_sont_rejetes():
    analyseur = _construire_analyseur()

    with pytest.raises(SystemExit):
        analyseur.parse_args(["--un-seul-cours", "100001", "--diagnostic"])


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
        "100001", tmp_path, session=session, fabrique_ena=lambda _session: EnaCassee({})
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
    evenements.put(("cours", "ABC-1000 Éthique"))
    lignes = []
    etat = {}

    _drainer_progression(evenements, etat, imprimer=lignes.append)

    assert "\n=== Session 1/2 : Automne 2025 - 1 cours ===" in lignes
    assert "  [Automne 2025] (1/2) AAA-1000 Cours ancien" in lignes
    assert "  [fichier] plan-de-cours.pdf" in lignes
    assert "\n=== Session 2/2 : Hiver 2026 - 1 cours ===" in lignes
    assert "  [Hiver 2026] (2/2) ABC-1000 Éthique" in lignes


def test_drainer_progression_appele_deux_fois_ne_reaffiche_pas():
    # Vidage au fil de l'eau, comme _drainer : un evenement deja affiche ne
    # doit jamais ressortir a un second passage, meme avec l'etat de
    # progression maintenu entre deux appels.
    evenements = queue.Queue()
    evenements.put(("plan", ["Hiver 2026"]))
    evenements.put(("cours", "ABC-1000"))
    lignes = []
    etat = {}

    _drainer_progression(evenements, etat, imprimer=lignes.append)
    evenements.put(("fin", "1 fichier archive"))
    _drainer_progression(evenements, etat, imprimer=lignes.append)

    assert lignes[-1] == "  [fin] 1 fichier archive"
    # Le cours deja affiche au premier passage ne doit pas ressortir au
    # second, meme avec l'etat de progression maintenu entre deux appels.
    assert lignes.count("  [Hiver 2026] (1/1) ABC-1000") == 1


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


# --- _tout / _session : isolation d'une session en echec (defaut critique) ---


def test_tout_isole_une_session_en_echec_et_archive_les_autres(tmp_path):
    # Reproduit le defaut critique constate en conditions reelles : une
    # session (future, vraisemblablement vide) leve
    # SelecteurSessionsIndisponible au moment de sa selection, et ne doit
    # pas emporter les autres sessions de l'enchainement --tout.
    cours_automne = Cours(
        id_site="199999",
        sigle="AAA-1000",
        titre="Cours ancien avec plan",
        session=SESSION_AUTOMNE,
        url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=automne",
    )
    ena = EnaAvecEchecSurUneSession(
        {SESSION_AUTOMNE: [cours_automne], SESSION_HIVER_2027: []},
        session_qui_echoue=SESSION_HIVER_2027,
    )
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    # Archivage partiel : Automne 2025 a bien ete archivee, Hiver 2027 est en
    # echec -- ni succes complet, ni echec total.
    assert code == 4
    assert session.fermee is True
    assert (tmp_path / SESSION_AUTOMNE.dossier() / cours_automne.dossier()).exists()
    assert not (tmp_path / SESSION_HIVER_2027.dossier()).exists()


def test_tout_rapport_mentionne_le_libelle_de_la_session_en_echec(tmp_path):
    ena = EnaAvecEchecSurUneSession(
        {SESSION_HIVER_2027: [], SESSION_HIVER: [COURS_HIVER]},
        session_qui_echoue=SESSION_HIVER_2027,
    )
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 4
    contenu = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "Hiver 2027" in contenu
    assert "jamais confirme" in contenu


def test_session_en_echec_seule_rend_code_non_nul_sans_rien_ecrire(tmp_path):
    # Une seule session visee (--session), et elle echoue : aucun fichier
    # n'est ecrit, le code de sortie doit le refleter (echec, pas succes).
    ena = EnaAvecEchecSurUneSession(
        {SESSION_HIVER_2027: []}, session_qui_echoue=SESSION_HIVER_2027
    )
    session = SessionFactice()

    code = _session("Hiver 2027", tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 1
    assert (tmp_path / "_rapport.html").exists()
    contenu = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "Hiver 2027" in contenu


def test_tout_session_expiree_apres_echec_d_une_autre_session_interrompt_tout(tmp_path):
    # SessionExpiree doit rester fatale, meme apres qu'une session
    # precedente ait deja ete isolee en echec dans le meme enchainement :
    # elle ne doit jamais etre absorbee par la nouvelle isolation.
    class EnaAvecEchecPuisExpiration(EnaDeTest):
        def __init__(self, cours_par_session, session_en_echec, session_qui_expire):
            super().__init__(cours_par_session)
            self._session_en_echec = session_en_echec
            self._session_qui_expire = session_qui_expire

        def sites_de_session(self, session):
            if session == self._session_en_echec:
                raise SelecteurSessionsIndisponible("jamais confirme")
            if session == self._session_qui_expire:
                raise SessionExpiree("expiree")
            return super().sites_de_session(session)

    # L'ordre chronologique de --tout place Automne 2025 avant Hiver 2026 :
    # la session en echec (Automne) est donc bien traitee, et isolee, avant
    # que la suivante (Hiver) n'expire.
    ena = EnaAvecEchecPuisExpiration(
        {SESSION_AUTOMNE: [], SESSION_HIVER: [COURS_HIVER]},
        session_en_echec=SESSION_AUTOMNE,
        session_qui_expire=SESSION_HIVER,
    )
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 3
    assert session.fermee is True
    contenu = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "Automne 2025" in contenu
    assert "interrompu" in contenu.lower()


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
        analyseur.parse_args(["--un-seul-cours", "100001", "--tout"])


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


def test_zip_mode_reessaie_dans_le_dossier_si_a_cote_echoue(tmp_path, monkeypatch):
    # Ecrire A COTE du dossier demande les droits d'administrateur quand ce
    # dossier est directement a la racine d'un disque (constate en usage
    # reel : destination "C:\\Archive test" -> ZIP vise a
    # "C:\\Archive test.zip", [Errno 13] Permission denied). creer_zip sait
    # deja s'exclure de son propre contenu : ecrire DEDANS est donc une
    # deuxieme tentative sure.
    archive = tmp_path / "Archive"
    archive.mkdir()
    tentatives: list = []

    def creer_zip_qui_echoue_a_cote(racine, cible, **_options):
        tentatives.append(cible)
        if cible.parent == racine:
            return cible
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr("extracteur.__main__.creer_zip", creer_zip_qui_echoue_a_cote)
    lignes = []

    code = _zip_mode(archive, imprimer=lignes.append)

    cible_a_cote = tmp_path / "Archive.zip"
    cible_dedans = archive / "Archive.zip"
    assert tentatives == [cible_a_cote, cible_dedans]
    assert code == 0
    assert any(str(cible_dedans) in ligne for ligne in lignes)


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

    code = _un_seul_cours("100001", tmp_path, session=session, fabrique_ena=lambda _s: ena)

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


# --- imprimer/imprimer_erreur/annulation/sur_fin : coeur reutilisable par
# --- l'interface graphique (tache 12), sans rien changer au comportement
# --- console par defaut (deja couvert par tous les tests ci-dessus) ---


def test_un_seul_cours_route_tout_vers_imprimer_rien_sur_stdout(tmp_path, capsys):
    # L'interface graphique n'a aucun terminal a lire : toute la narration
    # doit pouvoir etre captee par un imprimer/imprimer_erreur injecte, sans
    # qu'un seul print() ne passe outre sur le vrai stdout/stderr.
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    lignes: list = []

    code = _un_seul_cours(
        "100001",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: ena,
        imprimer=lignes.append,
        imprimer_erreur=lignes.append,
    )

    assert code == 0
    assert any("[fin]" in ligne for ligne in lignes)
    assert any("Verification :" in ligne for ligne in lignes)
    sortie = capsys.readouterr()
    assert sortie.out == ""
    assert sortie.err == ""


def test_un_seul_cours_erreurs_routees_vers_imprimer_erreur(tmp_path):
    session = SessionFactice()
    erreurs: list = []

    code = _un_seul_cours(
        "000000",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        imprimer=lambda _t: None,
        imprimer_erreur=erreurs.append,
    )

    assert code == 2
    assert any("000000" in ligne for ligne in erreurs)


def test_un_seul_cours_sur_fin_recoit_le_resultat_le_controle_et_le_rapport(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    recu: list = []

    code = _un_seul_cours(
        "100001",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: ena,
        sur_fin=lambda *args: recu.append(args),
    )

    assert code == 0
    assert len(recu) == 1
    resultat, controle, chemin_rapport = recu[0]
    assert resultat.fichiers_ecrits > 0
    assert controle["inscrits"] == resultat.fichiers_ecrits
    assert chemin_rapport == tmp_path / "_rapport.html"


def test_un_seul_cours_sur_fin_jamais_appele_si_cours_introuvable(tmp_path):
    session = SessionFactice()
    recu: list = []

    code = _un_seul_cours(
        "000000",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        sur_fin=lambda *args: recu.append(args),
    )

    assert code == 2
    assert recu == []


def test_un_seul_cours_annulation_posee_avant_meme_de_se_connecter(tmp_path):
    # L'interruption pendant l'attente de connexion se traduit, comme un
    # delai ecoule ordinaire, par une connexion non detectee -- chemin deja
    # entierement teste par ailleurs. Ce test verifie seulement que
    # l'Event est bien transmis jusqu'a la doublure de session.
    import threading

    session = SessionFactice(connectee=True)
    annulation = threading.Event()
    annulation.set()

    code = _un_seul_cours(
        "100001",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        annulation=annulation,
    )

    assert code == 1
    assert session.fermee is True


def test_session_sur_fin_recoit_le_resultat(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER], SESSION_AUTOMNE: [COURS_ANCIEN]})
    session = SessionFactice()
    recu: list = []

    code = _session(
        "Hiver 2026",
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: ena,
        sur_fin=lambda *args: recu.append(args),
    )

    assert code == 0
    assert len(recu) == 1


def test_tout_route_tout_vers_imprimer_rien_sur_stdout(tmp_path, capsys):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    lignes: list = []

    code = _tout(
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: ena,
        imprimer=lignes.append,
        imprimer_erreur=lignes.append,
    )

    assert code == 0
    assert any("Verification :" in ligne for ligne in lignes)
    sortie = capsys.readouterr()
    assert sortie.out == ""
    assert sortie.err == ""


def test_tout_sur_fin_recoit_le_resultat(tmp_path):
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    recu: list = []

    code = _tout(
        tmp_path, session=session, fabrique_ena=lambda _s: ena, sur_fin=lambda *args: recu.append(args)
    )

    assert code == 0
    assert len(recu) == 1


def test_tout_annulation_arretee_a_la_frontiere_du_deuxieme_cours(tmp_path):
    # Preuve d'integration : l'Event transmis par _tout atteint bien
    # Archiveur.archiver, pas seulement l'attente de connexion (deja couvert
    # cote archiveur.py par ses propres tests unitaires).
    import threading

    cours_automne = Cours(
        id_site="199999",
        sigle="AAA-1000",
        titre="Ancien",
        session=SESSION_AUTOMNE,
        # Un plan de cours telechargeable : sans lui le cours tente n'ecrirait
        # aucun fichier, et le code de sortie serait 1 (« rien n'a ete ecrit »)
        # au lieu de 4. C'est 4 qui doit etre verifie ici : un archivage
        # interrompu qui a tout de meme produit des fichiers est partiel, et
        # ne doit jamais etre rendu comme un succes.
        url_plan_de_cours="/contenu/sitescours/x/plan.pdf?identifiant=b",
    )

    class EnaQuiAnnuleApresLePremierCours(EnaDeTest):
        def modules(self, cours):
            if cours == cours_automne:
                annulation.set()
            return super().modules(cours)

    ena = EnaQuiAnnuleApresLePremierCours(
        {SESSION_AUTOMNE: [cours_automne], SESSION_HIVER: [COURS_HIVER]}
    )
    session = SessionFactice()
    annulation = threading.Event()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena, annulation=annulation)

    # Le cours de la session la plus ancienne (traite en premier) a bien ete
    # tente ; celui de Hiver, qui suit, ne l'a jamais ete.
    assert (tmp_path / SESSION_AUTOMNE.dossier() / cours_automne.dossier()).exists()
    assert not (tmp_path / SESSION_HIVER.dossier()).exists()
    assert code == 4

    # Et le rapport doit le dire en clair : ni succes complet, ni simple echec
    # partiel, mais un arret en cours de route -- avec le nombre de cours
    # jamais tentes, le seul chiffre qui dise ce qui manque encore.
    rapport = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    # Sans apostrophe dans le motif : ecrire_rapport passe le message par
    # html.escape, qui rend « l'utilisateur » sous la forme « l&#x27;utilisateur ».
    assert "interrompu par" in rapport
    assert "1 cours" in rapport
def test_tout_annulation_pendant_l_enumeration_n_est_jamais_un_succes(tmp_path):
    # L'archivage ne commence qu'une fois TOUTES les sessions enumerees. Un
    # arret pendant cette phase ne doit ni faire attendre la fin des douze
    # sessions, ni -- surtout -- rendre 0 : archiver([]) produirait un Resultat
    # sans echec ni cours non tente, que _code_de_sortie traduirait en succes
    # complet sur une archive inexistante.
    import threading

    annulation = threading.Event()

    class EnaQuiAnnuleALaPremiereSession(EnaDeTest):
        def sites_de_session(self, session):
            annulation.set()
            return super().sites_de_session(session)

    ena = EnaQuiAnnuleALaPremiereSession(
        {SESSION_AUTOMNE: [COURS_ANCIEN], SESSION_HIVER: [COURS_HIVER]}
    )
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena, annulation=annulation)

    assert code == 1
    # Rien n'a ete archive : aucune des deux sessions n'a de dossier.
    assert not (tmp_path / SESSION_AUTOMNE.dossier()).exists()
    assert not (tmp_path / SESSION_HIVER.dossier()).exists()
    # Et le rapport dit pourquoi, plutot que de laisser croire a une archive
    # vide mais reguliere.
    rapport = (tmp_path / "_rapport.html").read_text(encoding="utf-8")
    assert "interrompu par" in rapport
    assert "enumeration" in rapport


def test_tout_sans_annulation_enumere_toutes_les_sessions(tmp_path):
    # Contre-epreuve du test precedent : le garde d'annulation ne doit pas
    # ecourter une enumeration ordinaire.
    ena = EnaDeTest({SESSION_AUTOMNE: [COURS_ANCIEN], SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()

    code = _tout(tmp_path, session=session, fabrique_ena=lambda _s: ena)

    assert code == 0
    assert (tmp_path / SESSION_AUTOMNE.dossier()).exists()
    assert (tmp_path / SESSION_HIVER.dossier()).exists()


def test_session_annulation_pendant_l_enumeration_n_ecrit_aucun_zip_possible(tmp_path):
    # Meme garde pour --session : sur_fin ne doit pas etre appele, sans quoi
    # l'interface graphique compresserait une archive qui n'existe pas.
    import threading

    annulation = threading.Event()
    annulation.set()
    recu: list = []

    code = _session(
        "Hiver 2026",
        tmp_path,
        session=SessionFactice(),
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        annulation=annulation,
        sur_fin=lambda *args: recu.append(args),
    )

    assert code == 1
    assert recu == []


def test_la_trace_de_reparation_de_page_atteint_le_journal_de_la_fenetre(tmp_path):
    # Une execution qui se repare en silence empeche de voir que la
    # plateforme faiblit. La trace doit donc atterrir la ou l'utilisateur
    # regarde -- le journal de la fenetre graphique -- et non sur une sortie
    # standard qu'elle n'affiche pas.
    ena = EnaDeTest({SESSION_HIVER: [COURS_HIVER]})
    session = SessionFactice()
    lignes: list = []

    _tout(
        tmp_path,
        session=session,
        fabrique_ena=lambda _s: ena,
        imprimer=lignes.append,
        imprimer_erreur=lignes.append,
    )

    # Le coeur a bien pose son canal d'affichage sur l'objet Ena : c'est lui
    # que _visiter passera a reinitialiser_page le jour d'un incident.
    # Compare par egalite, jamais par identite : lignes.append rend un nouvel
    # objet de methode liee a chaque acces.
    assert ena.imprimer == lignes.append
    ena.imprimer("page reinitialisee apres un echec de navigation")
    assert lignes[-1] == "page reinitialisee apres un echec de navigation"


def test_l_archivage_suspend_la_veille_pendant_le_travail(tmp_path, monkeypatch):
    # Un archivage complet dure parfois plus d'une heure. Si la machine
    # s'endort, le reseau tombe et la navigation en cours expire -- incident
    # reellement survenu, qui a coute dix-sept cours sur trente-neuf. Le
    # gestionnaire doit envelopper le travail, et le relacher a la fin.
    import contextlib

    appels: list = []

    @contextlib.contextmanager
    def veille_factice(imprimer=print):
        appels.append("pose")
        try:
            yield True
        finally:
            appels.append("relache")

    monkeypatch.setattr("extracteur.__main__.empecher_la_veille", veille_factice)

    code = _tout(
        tmp_path,
        session=SessionFactice(),
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        imprimer=lambda _t: None,
        imprimer_erreur=lambda _t: None,
    )

    assert code == 0
    assert appels == ["pose", "relache"]


def test_la_veille_est_relachee_meme_si_l_archivage_plante(tmp_path, monkeypatch):
    # Le relachement est dans un finally : un archivage qui echoue ne doit pas
    # laisser la machine incapable de s'endormir.
    import contextlib

    appels: list = []

    @contextlib.contextmanager
    def veille_factice(imprimer=print):
        appels.append("pose")
        try:
            yield True
        finally:
            appels.append("relache")

    monkeypatch.setattr("extracteur.__main__.empecher_la_veille", veille_factice)

    _un_seul_cours(
        "000000",
        tmp_path,
        session=SessionFactice(),
        fabrique_ena=lambda _s: EnaDeTest({SESSION_HIVER: [COURS_HIVER]}),
        imprimer=lambda _t: None,
        imprimer_erreur=lambda _t: None,
    )

    assert appels == ["pose", "relache"]

