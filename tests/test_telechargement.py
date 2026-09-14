import pytest

from extracteur.telechargement import (
    ErreurPermanente,
    SessionExpiree,
    telecharger,
)


class ReponseFactice:
    def __init__(self, statut, corps=b"ok"):
        self.statut = statut
        self._corps = corps

    def morceaux(self):
        yield self._corps


class TransportFactice:
    """Rend les statuts fournis, dans l'ordre, et compte les appels."""

    def __init__(self, statuts):
        self.statuts = list(statuts)
        self.appels = 0

    def __call__(self, url):
        self.appels += 1
        statut = self.statuts.pop(0)
        if isinstance(statut, Exception):
            raise statut
        return ReponseFactice(statut)


def sans_pause(_secondes):
    pass


def test_succes_immediat(tmp_path):
    transport = TransportFactice([200])
    taille, _ = telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 1
    assert taille == 2


def test_erreur_serveur_reessayee_puis_reussie(tmp_path):
    transport = TransportFactice([500, 500, 200])
    telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 3


def test_erreur_serveur_persistante_abandonne_apres_trois_tentatives(tmp_path):
    transport = TransportFactice([500, 500, 500])
    with pytest.raises(ErreurPermanente):
        telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 3


def test_connexion_coupee_reessayee(tmp_path):
    transport = TransportFactice([ConnectionError("coupure"), 200])
    telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 2


def test_403_ne_declenche_aucun_reessai(tmp_path):
    transport = TransportFactice([403])
    with pytest.raises(ErreurPermanente):
        telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 1


def test_404_ne_declenche_aucun_reessai(tmp_path):
    transport = TransportFactice([404])
    with pytest.raises(ErreurPermanente):
        telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)
    assert transport.appels == 1


def test_401_renouvelle_le_jeton_et_rejoue_une_fois(tmp_path):
    transport = TransportFactice([401, 200])
    renouvellements = []

    telecharger(
        transport,
        "/a",
        tmp_path / "a.bin",
        renouveler=lambda: renouvellements.append(1),
        dormir=sans_pause,
    )

    assert renouvellements == [1]
    assert transport.appels == 2


def test_401_persistant_leve_session_expiree(tmp_path):
    transport = TransportFactice([401, 401])
    with pytest.raises(SessionExpiree):
        telecharger(
            transport,
            "/a",
            tmp_path / "a.bin",
            renouveler=lambda: None,
            dormir=sans_pause,
        )
    assert transport.appels == 2


def test_401_sans_renouvellement_leve_session_expiree(tmp_path):
    transport = TransportFactice([401])
    with pytest.raises(SessionExpiree):
        telecharger(transport, "/a", tmp_path / "a.bin", dormir=sans_pause)


def test_les_pauses_sont_croissantes(tmp_path):
    transport = TransportFactice([500, 500, 200])
    pauses = []
    telecharger(transport, "/a", tmp_path / "a.bin", dormir=pauses.append)
    assert pauses == [2, 8]


def test_401_apres_erreurs_reseau_rejoue_immediatement(tmp_path):
    """Un 401 arrivant apres echecs reseau ne consomme pas le budget."""
    transport = TransportFactice([500, 500, 401, 200])
    renouvellements = []

    taille, _ = telecharger(
        transport,
        "/a",
        tmp_path / "a.bin",
        renouveler=lambda: renouvellements.append(1),
        dormir=sans_pause,
    )

    assert transport.appels == 4
    assert renouvellements == [1]
    assert taille == 2


def test_401_persistant_apres_erreurs_reseau_leve_session_expiree(tmp_path):
    """Un 401 persistant apres renouvellement leve SessionExpiree."""
    transport = TransportFactice([500, 500, 401, 401])

    with pytest.raises(SessionExpiree):
        telecharger(
            transport,
            "/a",
            tmp_path / "a.bin",
            renouveler=lambda: None,
            dormir=sans_pause,
        )

    assert transport.appels == 4


def test_401_puis_erreur_reseau_puis_401_leve_session_expiree(tmp_path):
    """Le renouvellement consomme au premier 401, le second est terminal."""
    transport = TransportFactice([401, 500, 401])

    with pytest.raises(SessionExpiree):
        telecharger(
            transport,
            "/a",
            tmp_path / "a.bin",
            renouveler=lambda: None,
            dormir=sans_pause,
        )

    assert transport.appels == 3
