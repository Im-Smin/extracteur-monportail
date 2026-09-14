import queue

from extracteur.__main__ import (
    _afficher_sessions,
    _chercher_cours,
    _code_de_sortie,
    _drainer,
    _ecrire_rapport_final,
    _un_seul_cours,
)
from extracteur.modele import Cours, Echec, Resultat, Session
from extracteur.telechargement import SessionExpiree

SESSION_HIVER = Session(code="202601", libelle="Hiver 2026")
SESSION_AUTOMNE = Session(code="202509", libelle="Automne 2025")

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

    def __init__(self, connectee=True, transport=None):
        self.connectee = connectee
        self.ouverte = False
        self.fermee = False
        self.transport = transport or (lambda url: _ReponseOk())

    def ouvrir(self):
        self.ouverte = True

    def attendre_connexion(self, delai=300):
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


def test_code_de_sortie_zero_si_des_fichiers_ont_ete_ecrits():
    assert _code_de_sortie(Resultat(fichiers_ecrits=2, echecs=[Echec("C", "a", "x")])) == 0


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
