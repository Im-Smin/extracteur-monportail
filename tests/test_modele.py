from extracteur.modele import Cours, Fichier, Session


def test_dossier_de_session_trie_chronologiquement():
    assert Session(code="202601", libelle="Hiver 2026").dossier() == "2026-1 Hiver"
    assert Session(code="202605", libelle="Été 2026").dossier() == "2026-2 Été"
    assert Session(code="202609", libelle="Automne 2026").dossier() == "2026-3 Automne"


def test_dossier_de_session_inconnue_reste_lisible():
    assert Session(code="202699", libelle="Intensif").dossier() == "2026-9 Intensif"


def test_dossier_de_cours_combine_sigle_et_titre():
    cours = Cours(
        id_site="181216",
        sigle="PHI-3900",
        titre="Éthique et professionnalisme",
        session=Session(code="202601", libelle="Hiver 2026"),
    )
    assert cours.dossier() == "PHI-3900 Éthique et professionnalisme"


def test_dossier_de_cours_sans_sigle():
    cours = Cours(
        id_site="149047",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    assert cours.dossier() == "Nos biais inconscients"


def test_fichier_est_interne_selon_son_url():
    interne = Fichier(nom="a.pdf", url="/contenu/sitescours/040/x/a.pdf?identifiant=ab")
    externe = Fichier(nom="b.pdf", url="https://www.oiq.qc.ca/b.pdf")
    assert interne.est_interne() is True
    assert externe.est_interne() is False
