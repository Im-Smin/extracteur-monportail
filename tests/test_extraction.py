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

# Variante sans le parametre idSite sur le traceur lui-meme : un appariement
# global par idSite ne peut alors associer ce plan de cours a aucun cours,
# alors qu'une lecture carte par carte le trouve quand meme, puisqu'il est
# physiquement dans la carte du bon cours.
LIEN_PLANCOURS_SANS_IDSITE = (
    "/analytique/evenement/plancours?idFichier=141542389"
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


# Structure relevee sur MAT-1900 (idSite=145065), /ena/site/resultats. Cette
# page Oracle ADF porte 46 <table> : boites de dialogue, menus de navigation,
# et un seul tableau de notes, repere par sa classe explicite. Deux valeurs
# supplementaires (note finale, cote) vivent hors du tableau, dans le corps
# de la page. Le sigle du cours est reduit ici, les libelles et classes des
# lignes sont ceux constates en conditions reelles.
HTML_RESULTATS_MAT_1900 = """
<html><body>
  <div id="dialogueFinSession" style="display:none">
    <table>
      <tr><td colspan="2">Votre session de travail se terminera dans 5 min.</td></tr>
      <tr><td><button>Continuer la session</button></td><td><button>Déconnexion</button></td></tr>
    </table>
  </div>
  <table class="menuNavigationSite">
    <tr>
      <td><a href="#">Accueil</a></td>
      <td><a href="#">Modules</a></td>
      <td><a href="#">Évaluations</a></td>
    </tr>
  </table>
  <p>Note finale : 63,49 %</p>
  <p>Cote : C+</p>
  <table class="ul_table_data TableauAvecRegroupements">
    <tr class="ul_table_header-row">
      <th></th><th>Note obtenue</th><th>Pondération</th><th>Note pondérée</th>
    </tr>
    <tr class="ul_table_body-row regroupement-first">
      <td>Évaluations en présentiel (Somme des évaluations de ce regroupement)</td>
      <td>80 %</td>
      <td>52,01 / 80</td>
    </tr>
    <tr class="ul_table_body-row pair p_AFOdd regr1">
      <td>Examen 1 (E1)</td><td>71,5 %</td><td>40 %</td><td>28,6 / 40</td>
    </tr>
    <tr class="ul_table_body-row pair p_AFOdd regr1">
      <td>Examen 2 (E2)</td><td>58,52 %</td><td>40 %</td><td>23,41 / 40</td>
    </tr>
    <tr class="ul_table_body-row regroupement-first">
      <td>Évaluations en ligne (Somme des évaluations de ce regroupement)</td>
      <td>20 %</td>
      <td>11,48 / 20</td>
    </tr>
    <tr class="ul_table_body-row impair p_AFEven regr2">
      <td>Minitest 1 (monPortail) (MT1)</td><td>68,57 %</td><td>10 %</td><td>6,86 / 10</td>
    </tr>
    <tr class="ul_table_body-row impair p_AFEven regr2">
      <td>Minitest 2 - (monPortail) (MT2)</td><td>46,15 %</td><td>10 %</td><td>4,62 / 10</td>
    </tr>
    <tr class="ul_table_footer VerticalAlignMiddle">
      <td></td><td></td><td></td><td>63,49 / 100</td>
    </tr>
  </table>
</body></html>
"""


def test_resultats_cible_le_bon_tableau_malgre_les_parasites():
    # Reproduit le defaut constate : balayer tous les <table> ramassait les
    # boites de dialogue et menus, et le decompte a quatre cellules
    # exigeait ecartait les lignes de regroupement (trois cellules
    # seulement, pas de colonne de pourcentage).
    notes = resultats_depuis_html(HTML_RESULTATS_MAT_1900)

    # Sept lignes de tableau (2 regroupements + 4 evaluations + le total),
    # plus les deux valeurs hors tableau (note finale, cote).
    assert len(notes) == 9

    evaluations = [n.evaluation for n in notes]
    assert "Votre session de travail se terminera dans 5 min." not in evaluations
    assert "Accueil" not in evaluations
    assert "Continuer la session" not in evaluations

    presentiel, e1, e2, en_ligne, mt1, mt2, total, note_finale, cote = notes

    assert presentiel.evaluation == "Évaluations en présentiel"
    assert presentiel.pourcentage == ""
    assert presentiel.ponderation == "80 %"
    assert presentiel.note == "52,01"
    assert presentiel.sur == "80"
    assert presentiel.est_regroupement is True

    assert e1.evaluation == "Examen 1 (E1)"
    assert e1.pourcentage == "71,5 %"
    assert e1.ponderation == "40 %"
    assert e1.note == "28,6"
    assert e1.sur == "40"
    assert e1.est_regroupement is False

    assert e2.evaluation == "Examen 2 (E2)"
    assert e2.pourcentage == "58,52 %"
    assert e2.note == "23,41"
    assert e2.sur == "40"

    assert en_ligne.evaluation == "Évaluations en ligne"
    assert en_ligne.pourcentage == ""
    assert en_ligne.ponderation == "20 %"
    assert en_ligne.note == "11,48"
    assert en_ligne.sur == "20"
    assert en_ligne.est_regroupement is True

    assert mt1.evaluation == "Minitest 1 (monPortail) (MT1)"
    assert mt1.pourcentage == "68,57 %"
    assert mt1.note == "6,86"
    assert mt1.sur == "10"

    assert mt2.evaluation == "Minitest 2 - (monPortail) (MT2)"
    assert mt2.pourcentage == "46,15 %"
    assert mt2.note == "4,62"
    assert mt2.sur == "10"

    assert total.evaluation == ""
    assert total.pourcentage == ""
    assert total.ponderation == ""
    assert total.note == "63,49"
    assert total.sur == "100"
    assert total.est_regroupement is False

    # Note finale et cote, hors tableau : deux lignes dediees plutot qu'un
    # nouveau champ sur Note (voir commentaire de _note_finale_et_cote).
    assert note_finale.evaluation == "Note finale"
    assert note_finale.pourcentage == "63,49 %"

    assert cote.evaluation == "Cote"
    assert cote.note == "C+"


def test_resultats_repli_sans_classe_de_tableau():
    # Si la classe venait a manquer (page modifiee), le comptage de cellules
    # doit encore distinguer regroupement (3 cellules) et evaluation (4).
    html = """
    <table>
      <tr>
        <td>Examen final (Somme des évaluations de ce regroupement)</td>
        <td>60 %</td>
        <td>50 / 60</td>
      </tr>
      <tr>
        <td>Devoir 1</td><td>90 %</td><td>10 %</td><td>9 / 10</td>
      </tr>
    </table>
    """
    notes = resultats_depuis_html(html)
    assert len(notes) == 2

    assert notes[0].evaluation == "Examen final"
    assert notes[0].pourcentage == ""
    assert notes[0].ponderation == "60 %"
    assert notes[0].note == "50"
    assert notes[0].sur == "60"
    assert notes[0].est_regroupement is True

    assert notes[1].evaluation == "Devoir 1"
    assert notes[1].pourcentage == "90 %"
    assert notes[1].ponderation == "10 %"
    assert notes[1].note == "9"
    assert notes[1].sur == "10"
    assert notes[1].est_regroupement is False


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
    # Structure reelle constatee sur /portail/cours (session Automne 2025) :
    # le lien ne porte que le titre, jamais le sigle. Le sigle est un texte de
    # la carte elle-meme, sous la forme "SIGLE-0000, NRC : xxxxx (sect. yy)".
    html = """
    <article class="mpo-boite mpo-boite-principale mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178960">Analyse et modélisation des données</a>
      MQT-2101, NRC : 86582 (sect. H1)
      Cours présentiel-hybride
      Dates limites d'abandon
      Plages horaires
      Plan de cours
      Suivi de ma...
    </article>
    """
    cours = cours_depuis_html(html, Session(code="202509", libelle="Automne 2025"))
    assert cours[0].sigle == "MQT-2101"
    assert cours[0].titre == "Analyse et modélisation des données"


def test_cours_sigle_ignore_un_faux_positif_hors_forme_carte():
    # Reproduit par relecture : le motif "[A-Z]{3}-\\d{4}" cherche n'importe
    # ou dans le texte matchait aussi un numero de dossier sans rapport,
    # produisant un sigle invente sans la moindre alerte. Le vrai sigle est
    # toujours immediatement suivi d'une virgule (et generalement de la
    # mention NRC) : un numero de dossier au milieu d'une phrase ne l'est pas.
    html = """
    <article class="mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=1">Cours X</a>
      Reference dossier NRC-4567 pour ce cours
    </article>
    """
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    assert cours[0].sigle is None


def test_cours_extrait_le_sigle_meme_sans_mention_nrc():
    # Repli tolerant : la virgule apres le sigle suffit, meme sans "NRC" a la
    # suite (forme non confirmee en session reelle, mais a ne pas perdre).
    html = """
    <article class="mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=1">Cours X</a>
      MQT-2101, (sect. H1)
    </article>
    """
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    assert cours[0].sigle == "MQT-2101"


def test_cours_deux_cartes_gardent_chacune_leur_propre_sigle():
    # Deuxieme carte relevee, meme session : verifie que le sigle de chaque
    # carte reste bien le sien, sans fuite d'une carte a l'autre.
    html = """
    <article class="mpo-boite mpo-boite-principale mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178960">Analyse et modélisation des données</a>
      MQT-2101, NRC : 86582 (sect. H1)
    </article>
    <article class="mpo-boite mpo-boite-principale mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178785">Aspects administratifs et humains de la gestion</a>
      RLT-1700, NRC : 88184 (sect. Z3)
    </article>
    """
    cours = cours_depuis_html(html, Session(code="202509", libelle="Automne 2025"))
    par_id = {c.id_site: c for c in cours}

    assert par_id["178960"].sigle == "MQT-2101"
    assert par_id["178785"].sigle == "RLT-1700"


def test_cours_associe_le_plan_de_cours_a_la_bonne_carte_sans_idsite_global():
    # Le lien du plan de cours vit dans la carte de son cours (releve reel),
    # mais son href ne porte pas toujours un idSite exploitable pour un
    # appariement global : un balayage de page qui s'appuie uniquement sur ce
    # parametre perdrait ce plan de cours, ou pire, l'attribuerait au mauvais
    # cours si l'appariement se faisait par ordre d'apparition. La premiere
    # carte n'a pas de plan de cours du tout : elle ne doit jamais recevoir
    # celui de la seconde.
    html = f"""
    <article class="mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178960">Analyse et modélisation des données</a>
      MQT-2101, NRC : 86582 (sect. H1)
    </article>
    <article class="mpo-boite">
      <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=181216">Éthique et professionnalisme</a>
      PHI-3900, NRC : 12345 (sect. A1)
      <a href="https://sitescours.monportail.ulaval.ca{LIEN_PLANCOURS_SANS_IDSITE}">Plan de cours</a>
    </article>
    """
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    par_id = {c.id_site: c for c in cours}

    assert par_id["178960"].url_plan_de_cours is None
    assert par_id["181216"].url_plan_de_cours is not None
    assert "PHI-3900_H26_17541.pdf" in par_id["181216"].url_plan_de_cours


def test_carte_avec_deux_liens_vers_le_meme_cours_ne_perd_pas_son_contenu():
    # Motif deja documente ailleurs sur ce site pour les modules (icone puis
    # titre) : deux liens de site vers LE MEME cours dans une carte. Compter
    # les balises de lien (et non les identifiants de site distincts) fait
    # croire a _carte_du_lien que l'ancetre commun contient deja "plus d'un"
    # cours, et la remontee s'arrete aussitot -- la carte se reduit au lien
    # lui-meme, sigle et plan de cours perdus en silence. La carte voisine ne
    # doit pas non plus etre polluee par cette correction.
    html = f"""
    <div class="page">
      <article class="mpo-boite">
        <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178960"></a>
        <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178960">Analyse et modélisation des données</a>
        MQT-2101, NRC : 86582 (sect. H1)
        <a href="https://sitescours.monportail.ulaval.ca{LIEN_PLANCOURS}">Plan de cours</a>
      </article>
      <article class="mpo-boite">
        <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=178785">Aspects administratifs et humains de la gestion</a>
        RLT-1700, NRC : 88184 (sect. Z3)
      </article>
    </div>
    """
    cours = cours_depuis_html(html, Session(code="202509", libelle="Automne 2025"))
    par_id = {c.id_site: c for c in cours}

    assert par_id["178960"].sigle == "MQT-2101"
    assert par_id["178960"].titre == "Analyse et modélisation des données"
    assert par_id["178960"].url_plan_de_cours is not None
    # La carte voisine garde son propre sigle, sans fuite.
    assert par_id["178785"].sigle == "RLT-1700"


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


# URL reelle relevee sur PHI-3900, boite de depot du TP2 : le nom du fichier
# est URL-encode (espaces et accents) et ne passe jamais par le traceur
# d'analytique, contrairement aux fichiers de module.
LIEN_DOCUMENT_DEPOSE = (
    "/contenu/sitescours/040/04000/202601/site181216/evaluations1434432"
    "/evaluation1018611/boitedepot/equipe1528525"
    "/Z1-PHI3900-H2026-TP2%20-%20%C3%89thique%20et%20professionnalisme.docx"
    "?identifiant=9b035967"
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


def test_depots_depuis_html_ignore_un_tableau_parasite_de_la_meme_classe():
    # La classe ul_table_data n'est pas discriminante a elle seule : d'autres
    # tableaux de la plateforme la portent sans etre la boite de depot. Seul
    # l'en-tete "Nom du document" doit designer le bon tableau.
    html = """
    <table class="ul_table_data">
      <tr><th>Module</th><th>Titre</th></tr>
      <tr><td>1</td><td>Intro</td></tr>
    </table>
    """
    assert depots_depuis_html(html) == []


def test_depots_depuis_html_associe_les_colonnes_par_entete_quel_que_soit_l_ordre():
    # Rien ne garantit que l'ordre observe sur PHI-3900 soit celui d'une autre
    # boite de depot : l'association se fait par libelle d'en-tete, jamais
    # par position de colonne.
    html = f"""
    <table>
      <tr>
        <th>Déposé par</th>
        <th>Date de remise</th>
        <th>Nom du document</th>
        <th>Taille</th>
      </tr>
      <tr>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
        <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx</a></td>
        <td>3,25 Mo</td>
      </tr>
    </table>
    """
    depots = depots_depuis_html(html)
    assert len(depots) == 1
    depot = depots[0]
    assert depot.nom == "Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx"
    assert depot.taille == "3,25 Mo"
    assert depot.depose_par == "Buteau, Laurent"
    assert depot.date_remise == "12 avr. 2026 18h43"


def test_depots_depuis_html_nom_vient_de_l_url_meme_si_le_texte_du_lien_est_tronque():
    # Comme pour les fichiers de module, rien ne garantit que la plateforme
    # ne tronque jamais le texte affiche du lien : le nom retenu doit venir
    # de l'URL, la seule source fidele.
    html = f"""
    <table>
      <tr><th>Nom du document</th><th>Taille</th><th>Déposé par</th><th>Date de remise</th></tr>
      <tr>
        <td><a href="{LIEN_DOCUMENT_DEPOSE}">Z1-PHI3900-H2026-TP2 - Éthiq...</a></td>
        <td>3,25 Mo</td>
        <td>Buteau, Laurent</td>
        <td>12 avr. 2026 18h43</td>
      </tr>
    </table>
    """
    depots = depots_depuis_html(html)
    assert len(depots) == 1
    assert depots[0].nom == "Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx"


def test_depots_depuis_html_html_reel_boite_de_depot_phi3900():
    # Transcription fidele du HTML releve sur PHI-3900 (boite de depot du
    # TP2) : cinq cellules par ligne, la premiere vide pour la case a cocher,
    # classe ul_table_data, lien direct sous /contenu/sitescours/ (pas de
    # traceur d'analytique pour les depots).
    html = """
    <table class="ul_table_data">
      <tr>
        <th></th>
        <th>Nom du document</th>
        <th>Taille</th>
        <th>Déposé par</th>
        <th>Date de remise</th>
      </tr>
      <tr>
        <td><input type="checkbox" id="r1:0:t1:0:selectionner::content"></td>
        <td>
          <a id="r1:0:t1:0:gl1"
             href="/contenu/sitescours/040/04000/202601/site181216/evaluations1434432/evaluation1018611/boitedepot/equipe1528525/Z1-PHI3900-H2026-TP2%20-%20%C3%89thique%20et%20professionnalisme.docx?identifiant=9b035967">
            Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx
          </a>
        </td>
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
    assert depot.taille == "3,25 Mo"
    assert depot.depose_par == "Buteau, Laurent"
    assert depot.date_remise == "12 avr. 2026 18h43"
    assert (
        depot.url
        == "/contenu/sitescours/040/04000/202601/site181216/evaluations1434432"
        "/evaluation1018611/boitedepot/equipe1528525"
        "/Z1-PHI3900-H2026-TP2%20-%20%C3%89thique%20et%20professionnalisme.docx"
        "?identifiant=9b035967"
    )


def test_depots_depuis_html_page_sans_tableau():
    assert depots_depuis_html("<p>Aucun document</p>") == []
