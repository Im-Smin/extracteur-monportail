"""Tests de la suspension de la mise en veille pendant un archivage."""

import pytest

from extracteur.veille import (
    ETAT_NORMAL,
    ETAT_TRAVAIL_EN_COURS,
    MESSAGE_ACTIVE,
    MESSAGE_INDISPONIBLE,
    empecher_la_veille,
)


class ApiFactice:
    """Doublure de SetThreadExecutionState : enregistre les etats demandes.

    Rend un etat precedent non nul, comme l'API reelle en cas de succes.
    """

    # Valeur arbitraire non nulle : l'API reelle rend l'etat precedent, et
    # seul son caractere non nul distingue un succes d'un echec.
    ETAT_PRECEDENT_QUELCONQUE = 0x80000000

    def __init__(self, reponse=ETAT_PRECEDENT_QUELCONQUE, erreur=None):
        self.appels: list = []
        self._reponse = reponse
        self._erreur = erreur

    def __call__(self, etat):
        self.appels.append(etat)
        if self._erreur is not None:
            raise self._erreur
        return self._reponse


def test_suspend_puis_retablit_la_veille():
    api = ApiFactice()
    journal: list = []

    with empecher_la_veille(regler=api, imprimer=journal.append) as actif:
        assert actif is True
        # Pendant le bloc, seul l'etat de travail a ete pose.
        assert api.appels == [ETAT_TRAVAIL_EN_COURS]

    # A la sortie, la veille est rendue a Windows sans attendre la fin du
    # processus : l'utilisateur ne doit pas rester avec un ecran qui refuse
    # de s'eteindre apres un archivage termine.
    assert api.appels == [ETAT_TRAVAIL_EN_COURS, ETAT_NORMAL]
    assert MESSAGE_ACTIVE in journal


def test_retablit_la_veille_meme_si_le_travail_echoue():
    # Un archivage qui plante ne doit pas laisser la machine incapable de
    # s'endormir : le retablissement est dans un finally.
    api = ApiFactice()

    with pytest.raises(RuntimeError):
        with empecher_la_veille(regler=api, imprimer=lambda _t: None):
            raise RuntimeError("archivage interrompu")

    assert api.appels == [ETAT_TRAVAIL_EN_COURS, ETAT_NORMAL]


def test_systeme_sans_api_laisse_l_archivage_continuer():
    # Hors de Windows, ou si l'API est indisponible : l'archivage doit tourner
    # quand meme. Perdre le filet anti-veille ne justifie pas de refuser une
    # heure de travail -- mais il faut le dire.
    journal: list = []

    with empecher_la_veille(regler=lambda _etat: None, imprimer=journal.append) as actif:
        assert actif is False

    assert MESSAGE_INDISPONIBLE in journal


def test_api_qui_echoue_laisse_l_archivage_continuer():
    # SetThreadExecutionState rend zero quand elle echoue. Meme raisonnement :
    # on le dit, on continue.
    api = ApiFactice(reponse=0)
    journal: list = []

    with empecher_la_veille(regler=api, imprimer=journal.append) as actif:
        assert actif is False

    assert any(MESSAGE_INDISPONIBLE in ligne for ligne in journal)
    # Rien a retablir : l'etat n'a jamais ete pose.
    assert api.appels == [ETAT_TRAVAIL_EN_COURS]


def test_api_qui_leve_laisse_l_archivage_continuer():
    api = ApiFactice(erreur=OSError("appel systeme refuse"))
    journal: list = []

    with empecher_la_veille(regler=api, imprimer=journal.append) as actif:
        assert actif is False

    assert any("appel systeme refuse" in ligne for ligne in journal)


def test_un_echec_du_retablissement_ne_remonte_pas():
    # Windows libere l'etat a la fin du processus de toute facon : un echec
    # ici ne doit pas masquer le resultat d'un archivage reussi.
    class ApiQuiEchoueAuRetablissement(ApiFactice):
        def __call__(self, etat):
            self.appels.append(etat)
            if etat == ETAT_NORMAL:
                raise OSError("retablissement refuse")
            return 0x80000000

    api = ApiQuiEchoueAuRetablissement()

    with empecher_la_veille(regler=api, imprimer=lambda _t: None):
        pass

    assert api.appels == [ETAT_TRAVAIL_EN_COURS, ETAT_NORMAL]


def test_l_etat_demande_couvre_le_systeme_et_l_ecran():
    # Le systeme seul ne suffit pas : un ecran qui s'eteint entraine sur
    # certaines configurations une mise en veille differee.
    from extracteur.veille import ES_CONTINUOUS, ES_DISPLAY_REQUIRED, ES_SYSTEM_REQUIRED

    assert ETAT_TRAVAIL_EN_COURS & ES_CONTINUOUS
    assert ETAT_TRAVAIL_EN_COURS & ES_SYSTEM_REQUIRED
    assert ETAT_TRAVAIL_EN_COURS & ES_DISPLAY_REQUIRED
