from extracteur.extraction import (
    cours_depuis_html,
    depots_depuis_html,
    est_commande_adf,
    fichiers_depuis_html,
    modules_depuis_html,
    nom_depuis_url,
    resultats_depuis_html,
    sections_du_menu,
    session_depuis_libelle,
    url_reelle,
)
from extracteur.modele import Session

LIEN_TRACEUR = (
    "/analytique/evenement/fichier?idFichier=140274665&idSite=181216"
    "&url=%2Fcontenu%2Fsitescours%2F040%2F04000%2F202601%2Fsite181216"
    "%2Fmodules1434431%2Fmodule1795743%2Fpage4874493%2Fbloccontenu5204221"
    "%2FCours_1_-_Introduction-janvier%25202026.pptx"
    "%3Fidentifiant%3D0a981dbdc4212d59737bc2e400fd0d39076480bc"
)

# Lien releve sur https://sitescours.monportail.ulaval.ca/portail/cours : le
# plan de cours passe par un traceur different de celui des fichiers, et son
# parametre url porte une URL absolue (et non un chemin relatif).
LIEN_PLANCOURS = (
    "/analytique/evenement/plancours?idFichier=141542389&idSite=181216"
    "&url=https%3A%2F%2Fsitescours.monportail.ulaval.ca%2Fcontenu%2Fsitescours"
    "%2F040%2F04000%2F202601%2Fsite181216%2Fplancours%2FPHI-3900_H26_17541.pdf"
    "%3Fidentifiant%3D6b6947ef288d16d135b3342db86127f2ee7afcbc"
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


def test_url_reelle_reconnait_le_traceur_du_plan_de_cours():
    # Le plan de cours passe par /analytique/evenement/plancours, pas
    # /analytique/evenement/fichier : sans elargir le filtre, il est ignore.
    assert url_reelle(LIEN_PLANCOURS) == (
        "https://sitescours.monportail.ulaval.ca/contenu/sitescours/040/04000"
        "/202601/site181216/plancours/PHI-3900_H26_17541.pdf"
        "?identifiant=6b6947ef288d16d135b3342db86127f2ee7afcbc"
    )


def test_nom_depuis_url_desencode_et_retire_la_requete():
    url = "/contenu/sitescours/x/Cours_1_-_Introduction-janvier%202026.pptx?identifiant=ab"
    assert nom_depuis_url(url) == "Cours_1_-_Introduction-janvier 2026.pptx"


def test_nom_depuis_url_gere_une_url_absolue():
    # Le parametre url du traceur plancours porte une URL absolue, alors que
    # les fichiers de module portent un chemin relatif : les deux doivent marcher.
    url = url_reelle(LIEN_PLANCOURS)
    assert nom_depuis_url(url) == "PHI-3900_H26_17541.pdf"


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


def test_fichiers_depuis_html_retient_aussi_le_traceur_plancours():
    html = f'<a href="{LIEN_PLANCOURS}">Plan de cours</a>'
    fichiers = fichiers_depuis_html(html)
    assert len(fichiers) == 1
    assert fichiers[0].nom == "PHI-3900_H26_17541.pdf"


def test_fichiers_dedoublonne_les_liens_identiques():
    html = f'<a href="{LIEN_TRACEUR}"></a><a href="{LIEN_TRACEUR}">Titre</a>'
    assert len(fichiers_depuis_html(html)) == 1


def test_fichiers_ecarte_les_commandes_adf_meme_sans_id():
    # Une commande ADF apparait souvent deux fois : icone avec id, puis titre sans id.
    # Le filtre doit ecarter l'URL entiere, pas juste le lien avec id.
    html = """
    <a id="m:j_id_1:cmdObtenirPlanCours" href="/analytique/evenement/fichier?idFichier=1&url=%2Fplandecours.pdf"></a>
    <a href="/analytique/evenement/fichier?idFichier=1&url=%2Fplandecours.pdf">Plan de cours</a>
    """
    assert fichiers_depuis_html(html) == []


def test_fichiers_preservent_les_plus_dans_les_noms():
    # parse_qs convertit les + littéraux en espaces, d'où extraction manuelle par regex.
    # Verifier que les + ne sont pas convertis en espaces.
    lien_avec_plus = (
        "/analytique/evenement/fichier?idFichier=123&url=%2Fcontenu%2Fmodule%2Ffichier+plus.pdf"
    )
    html = f'<a href="{lien_avec_plus}">Fichier</a>'
    fichiers = fichiers_depuis_html(html)
    assert len(fichiers) == 1
    assert fichiers[0].nom == "fichier+plus.pdf"


def test_resultats_ligne_d_evaluation():
    # Sommaire des resultats (/ena/site/resultats) : quatre colonnes remplies.
    html = """
    <table>
      <tr>
        <td><a href="#">Examen de mi-session (Em)</a></td>
        <td>70 %</td>
        <td>15 %</td>
        <td>10,5 / 15</td>
      </tr>
    </table>
    """
    notes = resultats_depuis_html(html)
    assert len(notes) == 1
    note = notes[0]
    assert note.evaluation == "Examen de mi-session (Em)"
    assert note.pourcentage == "70 %"
    assert note.ponderation == "15 %"
    assert note.note == "10,5"
    assert note.sur == "15"
    assert note.est_regroupement is False


def test_resultats_ligne_de_regroupement_sans_pourcentage():
    # Un regroupement porte le marqueur "(Somme des evaluations de ce
    # regroupement)" a la suite du titre, et n'a pas de pourcentage obtenu.
    html = """
    <table>
      <tr>
        <td>Examen final (en classe!)<br><span>(Somme des évaluations de ce regroupement)</span></td>
        <td></td>
        <td>39,99 %</td>
        <td>31,89 / 39,99</td>
      </tr>
      <tr>
        <td>Questionnaires d'autoévaluation (Évaluation formative)<br>
          <span>(Somme des évaluations de ce regroupement)</span></td>
        <td></td>
        <td>10 %</td>
        <td>9,83 / 10</td>
      </tr>
    </table>
    """
    notes = resultats_depuis_html(html)
    assert len(notes) == 2

    assert notes[0].evaluation == "Examen final (en classe!)"
    assert notes[0].pourcentage == ""
    assert notes[0].ponderation == "39,99 %"
    assert notes[0].note == "31,89"
    assert notes[0].sur == "39,99"
    assert notes[0].est_regroupement is True

    assert notes[1].evaluation == "Questionnaires d'autoévaluation (Évaluation formative)"
    assert notes[1].est_regroupement is True


def test_resultats_ligne_de_total_sans_titre():
    # La derniere ligne du tableau ne porte aucun titre, seulement le total.
    html = """
    <table>
      <tr><td><a href="#">Examen de mi-session (Em)</a></td><td>70 %</td><td>15 %</td><td>10,5 / 15</td></tr>
      <tr><td colspan="3"></td><td>78,59 / 100</td></tr>
    </table>
    """
    notes = resultats_depuis_html(html)
    total = notes[-1]
    assert total.evaluation == ""
    assert total.pourcentage == ""
    assert total.ponderation == ""
    assert total.note == "78,59"
    assert total.sur == "100"
    assert total.est_regroupement is False


def test_resultats_conserve_les_nombres_en_notation_francaise():
    # La virgule decimale ne doit jamais etre convertie : la fidelite a la
    # source prime, une conversion ratee fausserait des notes.
    html = "<table><tr><td>Participation</td><td>0 %</td><td>1 %</td><td>0,83 / 1</td></tr></table>"
    notes = resultats_depuis_html(html)
    assert notes[0].note == "0,83"
    assert notes[0].sur == "1"


def test_resultats_ignore_les_lignes_sans_points():
    # La page commence par des ancres vers des textes de politique : pas de
    # colonne de points, donc pas une ligne de resultat exploitable.
    html = """
    <table>
      <tr><th>Évaluation</th><th>%</th><th>Pondération</th><th>Points</th></tr>
      <tr><td>Barème de conversion</td></tr>
    </table>
    """
    assert resultats_depuis_html(html) == []


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


def test_cours_depuis_html():
    html = """
    <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=181216">
      Éthique et professionnalisme</a>
    <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=183033">
      Projet de fin d'études II</a>
    """
    session = Session(code="202601", libelle="Hiver 2026")
    cours = cours_depuis_html(html, session)

    assert [c.id_site for c in cours] == ["181216", "183033"]
    assert cours[0].titre == "Éthique et professionnalisme"


def test_cours_extrait_le_sigle_quand_il_est_present():
    html = (
        '<a href="/ena/site/accueil?idSite=1">PHI-3900 : Éthique et professionnalisme</a>'
    )
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    assert cours[0].sigle == "PHI-3900"
    assert cours[0].titre == "Éthique et professionnalisme"


def test_cours_sans_sigle():
    html = '<a href="/ena/site/accueil?idSite=149047">Nos biais inconscients</a>'
    cours = cours_depuis_html(html, Session(code="202209", libelle="Automne 2022"))
    assert cours[0].sigle is None
    assert cours[0].titre == "Nos biais inconscients"


def test_cours_dedoublonne():
    html = (
        '<a href="/ena/site/accueil?idSite=1"></a>'
        '<a href="/ena/site/accueil?idSite=1">Titre</a>'
    )
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    assert len(cours) == 1
    assert cours[0].titre == "Titre"


def test_cours_capte_le_lien_du_plan_de_cours_et_des_resultats():
    # Trois liens releves tels quels sur la page /portail/cours pour un meme cours.
    html = (
        '<a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=181216">'
        "PHI-3900 : Éthique et professionnalisme</a>"
        '<a href="https://sitescours.monportail.ulaval.ca/ena/site/resultats?idSite=181216">'
        "Résultats</a>"
        f'<a href="https://sitescours.monportail.ulaval.ca{LIEN_PLANCOURS}">Plan de cours</a>'
    )
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))

    assert cours[0].url_resultats == (
        "https://sitescours.monportail.ulaval.ca/ena/site/resultats?idSite=181216"
    )
    assert cours[0].url_plan_de_cours == (
        "https://sitescours.monportail.ulaval.ca/contenu/sitescours/040/04000"
        "/202601/site181216/plancours/PHI-3900_H26_17541.pdf"
        "?identifiant=6b6947ef288d16d135b3342db86127f2ee7afcbc"
    )


def test_cours_sans_plan_ni_resultats_a_des_liens_absents():
    html = '<a href="/ena/site/accueil?idSite=149047">Nos biais inconscients</a>'
    cours = cours_depuis_html(html, Session(code="202209", libelle="Automne 2022"))
    assert cours[0].url_plan_de_cours is None
    assert cours[0].url_resultats is None


def test_session_depuis_libelle_toutes_les_sessions_relevees():
    # Les douze sessions relevees dans le selecteur reel de /portail/cours.
    codes_attendus = {
        "Hiver 2027": "202701",
        "Automne 2026": "202609",
        "Hiver 2026": "202601",
        "Automne 2025": "202509",
        "Été 2025": "202505",
        "Hiver 2025": "202501",
        "Automne 2024": "202409",
        "Hiver 2024": "202401",
        "Automne 2023": "202309",
        "Été 2023": "202305",
        "Hiver 2023": "202301",
        "Automne 2022": "202209",
    }
    for libelle, code in codes_attendus.items():
        session = session_depuis_libelle(libelle)
        assert session.code == code
        assert session.libelle == libelle


def test_session_depuis_libelle_inattendu_ne_plante_pas():
    # Un libelle qui ne correspond a aucune saison connue ne doit pas lever
    # d'exception : mieux vaut un code explicitement marque comme inconnu
    # qu'une valeur numerique inventee.
    assert session_depuis_libelle("Trimestre").code == "INCONNU"
    assert session_depuis_libelle("Printemps 2025").code == "INCONNU"
    assert session_depuis_libelle("Automne 2022 supplementaire").code == "INCONNU"
    assert session_depuis_libelle("").code == "INCONNU"
    # Le libelle original est toujours conserve, meme quand le code echoue.
    assert session_depuis_libelle("Printemps 2025").libelle == "Printemps 2025"


LIEN_DOCUMENT_DEPOSE = (
    "/contenu/sitescours/040/04000/202601/site181216/depots"
    "/Z1-PHI3900-H2026-TP2.docx?identifiant=abc"
)


def test_depots_depuis_html_extrait_le_document():
    html = f"""
    <table>
      <tr><th>Nom du document</th><th>Taille</th><th>Déposé par</th><th>Date de remise</th></tr>
      <tr>
        <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx</a></td>
        <td>3,25 Mo</td>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
      </tr>
    </table>
    """
    depots = depots_depuis_html(html)
    assert len(depots) == 1
    depot = depots[0]
    assert depot.nom == "Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx"
    assert depot.url == LIEN_DOCUMENT_DEPOSE
    assert depot.taille == "3,25 Mo"
    # Sur un travail d'equipe, "Depose par" est un coequipier, pas forcement
    # l'utilisateur : c'est la seule trace de qui a remis quoi.
    assert depot.depose_par == "Buteau, Laurent"
    assert depot.date_remise == "12 avr. 2026 18h43"


def test_depots_depuis_html_ne_rend_jamais_la_case_a_cocher_ni_le_bouton_supprimer():
    # DANGER : la boite de depot porte une case a cocher devant chaque
    # document et un bouton Supprimer dans un formulaire. Un clic
    # malencontreux detruirait un travail remis : l'extraction ne doit
    # jamais rendre cliquable autre chose que les liens de telechargement.
    html = f"""
    <form action="/ena/site/evaluation" method="post">
      <table>
        <tr>
          <th></th>
          <th>Nom du document</th>
          <th>Taille</th>
          <th>Déposé par</th>
          <th>Date de remise</th>
        </tr>
        <tr>
          <td><input type="checkbox" name="docSelectionne" value="1"></td>
          <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx</a></td>
          <td>3,25 Mo</td>
          <td>Buteau, Laurent</td>
          <td>12 avr. 2026 18h43</td>
        </tr>
      </table>
      <button type="submit" name="cmdSupprimer">Supprimer</button>
    </form>
    """
    depots = depots_depuis_html(html)

    assert len(depots) == 1
    assert depots[0].nom == "Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx"
    assert depots[0].url == LIEN_DOCUMENT_DEPOSE
    # Rien issu de la case a cocher ou du bouton Supprimer ne doit apparaitre.
    assert all("Supprimer" not in d.nom for d in depots)
    assert all("docSelectionne" not in d.url for d in depots)


def test_depots_depuis_html_ignore_les_tableaux_sans_colonne_nom_du_document():
    html = "<table><tr><th>Module</th><th>Titre</th></tr><tr><td>1</td><td>Intro</td></tr></table>"
    assert depots_depuis_html(html) == []


def test_depots_depuis_html_page_sans_tableau():
    assert depots_depuis_html("<p>Aucun document</p>") == []
