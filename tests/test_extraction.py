from extracteur.extraction import (
    est_commande_adf,
    fichiers_depuis_html,
    modules_depuis_html,
    nom_depuis_url,
    resultats_depuis_html,
    sections_du_menu,
    url_reelle,
)

LIEN_TRACEUR = (
    "/analytique/evenement/fichier?idFichier=140274665&idSite=181216"
    "&url=%2Fcontenu%2Fsitescours%2F040%2F04000%2F202601%2Fsite181216"
    "%2Fmodules1434431%2Fmodule1795743%2Fpage4874493%2Fbloccontenu5204221"
    "%2FCours_1_-_Introduction-janvier%25202026.pptx"
    "%3Fidentifiant%3D0a981dbdc4212d59737bc2e400fd0d39076480bc"
)


def test_url_reelle_decode_le_parametre_url():
    assert url_reelle(LIEN_TRACEUR) == (
        "/contenu/sitescours/040/04000/202601/site181216/modules1434431"
        "/module1795743/page4874493/bloccontenu5204221"
        "/Cours_1_-_Introduction-janvier%202026.pptx"
        "?identifiant=0a981dbdc4212d59737bc2e400fd0d39076480bc"
    )


def test_url_reelle_laisse_passer_une_url_directe():
    directe = "/contenu/sitescours/040/x/a.pdf?identifiant=ab"
    assert url_reelle(directe) == directe


def test_url_reelle_ignore_un_lien_externe():
    assert url_reelle("https://www.oiq.qc.ca/doc.pdf") is None


def test_url_reelle_ignore_une_ancre():
    assert url_reelle("#") is None


def test_nom_depuis_url_desencode_et_retire_la_requete():
    url = "/contenu/sitescours/x/Cours_1_-_Introduction-janvier%202026.pptx?identifiant=ab"
    assert nom_depuis_url(url) == "Cours_1_-_Introduction-janvier 2026.pptx"


def test_modules_depuis_html():
    html = """
    <table>
      <tr><td><a href="/ena/site/module?idSite=181216&idModule=1795743&editionModule=false">1. Introduction</a></td></tr>
      <tr><td><a href="/ena/site/module?idSite=181216&idModule=1795744&editionModule=false">2. Vocabulaire</a></td></tr>
    </table>
    """
    modules = modules_depuis_html(html, "181216")
    assert [m.id_module for m in modules] == ["1795743", "1795744"]
    assert modules[0].titre == "1. Introduction"
    assert modules[0].rang == 0
    assert modules[1].rang == 1


def test_modules_ignore_les_liens_sans_id_module():
    html = '<a href="/ena/site/accueil?idSite=181216">Accueil</a>'
    assert modules_depuis_html(html, "181216") == []


def test_modules_dedoublonne_le_meme_module():
    # Une page peut porter deux fois le meme lien : l'icone et le titre.
    html = """
    <a href="/ena/site/module?idSite=1&idModule=99&editionModule=false"></a>
    <a href="/ena/site/module?idSite=1&idModule=99&editionModule=false">Module 99</a>
    """
    modules = modules_depuis_html(html, "1")
    assert len(modules) == 1
    assert modules[0].titre == "Module 99"


def test_fichiers_depuis_html_retient_les_ressources_internes():
    html = f"""
    <a href="{LIEN_TRACEUR}">Cours 1 - Introduction-.pptx</a>
    <a href="https://www.oiq.qc.ca/externe.pdf">Document externe</a>
    """
    fichiers = fichiers_depuis_html(html)
    assert len(fichiers) == 1
    # Le nom vient de l'URL, pas du texte du lien qui est tronque.
    assert fichiers[0].nom == "Cours_1_-_Introduction-janvier 2026.pptx"


def test_fichiers_dedoublonne_les_liens_identiques():
    html = f'<a href="{LIEN_TRACEUR}"></a><a href="{LIEN_TRACEUR}">Titre</a>'
    assert len(fichiers_depuis_html(html)) == 1


def test_resultats_depuis_html():
    html = """
    <table>
      <thead><tr><th>Évaluation</th><th>Note</th><th>Pondération</th></tr></thead>
      <tbody>
        <tr><td>Examen 1</td><td>18 / 20</td><td>30 %</td></tr>
        <tr><td>Travail final</td><td>45 / 50</td><td>70 %</td></tr>
      </tbody>
    </table>
    """
    notes = resultats_depuis_html(html)
    assert [n.evaluation for n in notes] == ["Examen 1", "Travail final"]
    assert notes[0].note == "18"
    assert notes[0].sur == "20"
    assert notes[0].ponderation == "30 %"


def test_resultats_tolere_une_note_absente():
    html = """
    <table><thead><tr><th>Évaluation</th><th>Note</th></tr></thead>
    <tbody><tr><td>Examen 2</td><td>Non disponible</td></tr></tbody></table>
    """
    notes = resultats_depuis_html(html)
    assert notes[0].evaluation == "Examen 2"
    assert notes[0].note == "Non disponible"
    assert notes[0].sur == ""


def test_resultats_sur_page_sans_tableau():
    assert resultats_depuis_html("<p>Aucun résultat</p>") == []


def test_sections_du_menu_varient_selon_les_sites():
    # PHI-3900 dit "Feuille de route", GIN-3320 dit "Contenu et activites".
    html_phi = '<nav><a href="#">Feuille de route</a><a href="#">Bibliographie</a></nav>'
    html_gin = '<nav><a href="#">Contenu et activités</a><a href="#">Archives</a></nav>'
    assert "Feuille de route" in sections_du_menu(html_phi)
    assert "Contenu et activités" in sections_du_menu(html_gin)


def test_sections_du_menu_ecarte_le_chrome_du_portail():
    # Toute page de site de cours embarque le menu global de monPortail.
    html = """
    <a href="#">Tableau de bord</a>
    <a href="#">Relevé de notes</a>
    <a href="#">Liste des cours</a>
    <a href="#">Droits de scolarité</a>
    <a href="#">Six biais</a>
    """
    assert sections_du_menu(html) == ["Six biais"]


def test_sections_du_menu_ecarte_les_libelles_vides_ou_enormes():
    html = '<a href="#"></a><a href="#">' + "x" * 80 + "</a><a href='#'>Concepts</a>"
    assert sections_du_menu(html) == ["Concepts"]


def test_commandes_adf_reconnues():
    assert est_commande_adf("m:j_id_1:cmdObtenirPlanCours") is True
    assert est_commande_adf("r1:0:ligneMod:voirMod") is False
    assert est_commande_adf(None) is False
