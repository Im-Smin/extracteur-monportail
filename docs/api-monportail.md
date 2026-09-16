# monPortail — carte technique (reconnaissance phase 0)

Relevé du 2026-09-14, session authentifiée réelle.

## Conclusion en une phrase

Les anciens sites de cours ne sont **pas** servis par une API JSON : c'est une
application Oracle ADF dont le contenu n'existe que dans un navigateur réel. Le
pilotage d'interface par Playwright est donc obligatoire, et non un repli.

## 1. Deux plateformes, une seule concernée

| Plateforme | Domaine | Sort en novembre 2026 |
|---|---|---|
| ENA « sites de cours monPortail » | `sitescours.monportail.ulaval.ca` | **Supprimée le 1er novembre 2026** |
| Brio | `www.brioeducation.ca` | Conservée — hors périmètre |

Avis officiel affiché sur le tableau de bord : « Dès le 1ᵉʳ novembre 2026, les
sites de cours des sessions antérieures n'ayant pas été créés sur Brio ne seront
plus accessibles, même en consultation. »

Les sites ENA sont déjà passés en lecture seule : « Ce site est maintenant
accessible en consultation seulement. »

## 2. Authentification

Deux domaines, deux clients OAuth distincts, tous deux sur Microsoft Entra ID
(tenant `56778bd5-6a3f-4bd3-a265-93163e4d5bfe`).

| Domaine | client_id | Scope | Ce qui autorise les appels |
|---|---|---|---|
| `monportail.ulaval.ca` | `bff03418-e5d5-4078-865e-984b4cf20ebe` | `api://api.ulaval.ca/.default offline_access` | Jeton porteur, clé `localStorage["mpo.shell.auth.token"]` |
| `sitescours.monportail.ulaval.ca` | `0925474d-a550-45a0-a749-396fbb526bdb` | `api://monportail.ulaval.ca/.default offline_access` | **Cookies de session seuls** |

Vérifié :

- `GET monportail.ulaval.ca/etudes/v1/sessions/courante/` avec cookies seuls →
  `401 AuthentificationRequiseException`. Le jeton porteur est requis.
- `GET sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=100001` avec
  cookies seuls → `200`. Aucun jeton nécessaire sur ce domaine.

Conséquence pratique : le téléchargement des fichiers de cours ne demande que
les cookies, que Playwright possède déjà. L'outil n'a jamais besoin de lire le
jeton porteur, sauf si l'on veut aussi le relevé de notes officiel du portail.

Point d'attention : si plusieurs comptes Microsoft sont connectés dans le
navigateur, Entra affiche un sélecteur de compte avant d'entrer dans l'ENA.
L'outil doit utiliser un profil de navigateur isolé, ou gérer cet écran.

## 3. L'ENA est une application Oracle ADF

Indices : `/adf/`, `/afr/partition/...`, paramètres `_afrLoop`,
`_afrWindowMode`, `Adf-Window-Id`, cookie de rebouclage `AdfLoopbackUtils`.

Test décisif — toutes ces URL renvoient **exactement la même page d'amorçage de
9 747 octets**, sans aucun contenu de cours :

```
/ena/site/accueil?idSite=100001
/ena/site/accueil?idSite=100001&_js=true
/ena/site/accueil?idSite=100001&_js=true&idPage=4874490
/ena/site/plandecours?idSite=100001
/ena/site/evaluations?idSite=100001
/ena/site/depots?idSite=100001
(et 14 autres chemins devinés : tous identiques)
```

Le segment de chemin après `/ena/site/` est ignoré par le serveur. La navigation
réelle se fait par postbacks ADF avec état de vue ; les entrées de menu portent
`href="#"`. Aucun endpoint REST n'existe côté ENA : `/ena/v1/...`,
`/sitescours/v1/...`, `/contenu/v1/...` renvoient tous `404`.

**Il n'existe donc pas d'URL stable par section de cours.** Le scraping HTTP est
impossible ; il faut un navigateur qui exécute l'application.

## 4. Énumération des cours — le mécanisme existe

Dans l'en-tête d'un site de cours, le bouton « Liste des cours » ouvre un panneau
contenant un sélecteur de session et la liste des sites de cette session.

- Sélecteur : `<select name="m:selectListeSessionsId">`
- Sessions présentes dans le dossier : **Hiver 2026, Automne 2025, Été 2025,
  Hiver 2025, Automne 2024, Hiver 2024, Automne 2023, Été 2023, Hiver 2023,
  Automne 2022** — 10 sessions.
- Chaque cours listé est un lien
  `https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=<N>`

C'est la source d'énumération de l'historique : parcourir les options du
sélecteur, relever les `idSite` de chaque session.

Sites relevés pour Hiver 2026 : `100001` (ABC-1000), `100002` (DEF-2000),
`100005` (VWX-8000). Autres `idSite` vus sur le tableau de bord : `100008`,
`159476`, `162283`, `100006`, `100007`, `153770`.

## 5. Les fichiers de contenu ont des URL directes

Observé sur les ressources chargées par une page de cours :

```
https://sitescours.monportail.ulaval.ca/contenu/sitescours/040/04000/202601/
  site100001/accueil/bloctexte1226860/ressourcestexte/
  PHI3900 - Bandeau - H2026 (1).png?identifiant=aa0676083b62...
```

Structure : `/contenu/sitescours/<unite>/<sousunite>/<session>/site<idSite>/
<page>/<bloc>/<typeressource>/<nomfichier>?identifiant=<empreinte>`

Ces URL sont directes, authentifiées par cookie, et téléchargeables en HTTP
simple. C'est le point qui sauve les performances du projet : **la découverte
passe par l'interface, mais le téléchargement reste du HTTP direct**, avec les
cookies du navigateur.

Le paramètre `identifiant` est obligatoire et propre à chaque ressource : il ne
peut pas être deviné, il doit être relevé dans le DOM de la page.

## 6. Structure d'un site de cours

Menu de gauche observé sur ABC-1000 : Introduction, Plan de cours, Informations
générales, Description du cours, Feuille de route, Évaluations et résultats,
Matériel didactique, Bibliographie. Une barre d'outils distincte donne accès aux
autres fonctions du site.

Chaque page porte un `idPage` (exemple : `4874490` pour l'accueil de ABC-1000),
visible dans l'URL affichée mais sans effet lors d'un appel HTTP direct.

### Piège à ne jamais déclencher : `cmdObtenirPlanCours`

Le menu « Plan de cours » se termine par une entrée à icône PDF qui ressemble à
un lien de téléchargement. Ce n'en est pas un.

- C'est un lien de commande ADF : `href="#"`, `onclick="return false;"`,
  identifiant terminé par `cmdObtenirPlanCours`.
- Le DOM de la page contient la boîte de dialogue qui lui est rattachée :
  « Vous vous apprêtez à **publier une nouvelle version** du plan de cours PDF.
  Désirez-vous continuer ? »

C'est donc une **action d'écriture**, pas une lecture. Non déclenchée pendant la
reconnaissance, et à proscrire dans le code.

Règle à appliquer dans l'implémentation : l'outil ne clique **que** sur des
liens de navigation et de téléchargement identifiés comme tels. Toute commande
ADF dont l'identifiant commence par `cmd` est traitée comme suspecte et ignorée
par défaut, sauf mise en liste blanche explicite après vérification. Un
archiveur ne doit jamais écrire sur la plateforme qu'il archive.

Pour le plan de cours, la voie sûre reste l'impression de la page en PDF par le
navigateur déjà piloté, comme prévu dans la spec. Si une version PDF a déjà été
publiée par l'enseignant, elle apparaît comme une ressource ordinaire sous
`/contenu/sitescours/...` et se télécharge normalement.

## 7. API REST du portail — utile uniquement en complément

Sur `monportail.ulaval.ca`, avec jeton porteur :

- `GET /etudes/v1/dossiersindividus/idul/<idul>`
- `GET /etudes/v1/sessions/courante/`, `GET /etudes/v1/sessions/<AAAASS>/`
- `GET /etudes/v1/inscriptions/<idUtilisateur>/statuts/sessions/<AAAASS>/?format=complet`
- `GET /api/utilisateur/preferences`

Identifiants du dossier : IDUL `<votre-idul>`, identifiant utilisateur mpo `<votre-identifiant-mpo>`.
Codes de session au format `AAAASS` : `202601` = Hiver 2026, `202609` = Automne
2026, `202605` = Été 2026.

Aucun de ces appels ne retourne d'`idSite` ENA. Ils ne remplacent donc pas
l'énumération par l'interface, mais `/etudes/v1/inscriptions/...` reste une
piste pour recouper la liste des cours et pour le relevé de notes officiel.

## 7bis. Chaîne de parcours validée — des cours aux fichiers

Contrairement au reste de l'application, certaines URL de l'ENA **sont**
déterministes. Elles suffisent à parcourir tout le contenu, à condition d'être
chargées dans un vrai navigateur.

### Étape 1 — routeur à URL stables

```
/lieninterne/redirection/<idSite>/liste_modules  →  /ena/site/modules?idSite=<idSite>
```

Ce routeur `lieninterne/redirection` accepte un nom de section et redirige vers
la page ADF correspondante, qui se rend correctement. C'est le point d'entrée
fiable de chaque section, à préférer au clic dans le menu.

### Étape 2 — la feuille de route donne les modules

La page `modules` liste les modules du cours. Chaque ligne est un vrai lien :

```
/ena/site/module?idSite=100001&idModule=1795743&editionModule=false
```

`editionModule=false` force la vue en consultation. À conserver tel quel.

### Étape 3 — les onglets d'une page de module

**Relevé le 15 septembre 2026 par inspection directe de JKL-4000 (`idSite=100003`)
et ABC-1000 (`idSite=100001`). Corrige une description antérieure fausse, qui
ne décrivait qu'un cas particulier et a causé une perte de contenu silencieuse.**

Une page de module porte une barre d'onglets, dont le nombre et les noms varient
d'un cours à l'autre — et d'un module à l'autre :

| Cours | Onglets |
|---|---|
| ABC-1000, module 1 | Général, Contenu du module |
| JKL-4000, module 1 | Notes de cours, Exercices 3e édition, Exercices 2e édition |
| (capture utilisateur) | Présentation, Théorie, Travaux pratiques |

Les trois montages utilisent **le même composant**. Ne jamais cibler un onglet
par son nom : il faut les énumérer.

#### DOM de la barre d'onglets

```html
<div class="ul_customizablePanelTabbed_tabs" id="r1:0:page:tabs::tabs">
  <span _ulitemid="r1:0:page:t4874492" class="ul_customizablePanelTabbed_tab">
    <span class="ul_customizablePanelTabbed_tab-content">
      <a id="r1:0:page:t4874492::a" class="ul_customizablePanelTabbed_tab-link"
         href="#" title="Général">Général</a>
    </span>
  </span>
  <span _ulitemid="r1:0:page:t4874493" class="ul_customizablePanelTabbed_tab p_AFSelected">
    ... title="Contenu du module" ...
  </span>
</div>
```

- Conteneur : `div.ul_customizablePanelTabbed_tabs`
- Un onglet : `span.ul_customizablePanelTabbed_tab`, l'onglet actif portant en
  plus `p_AFSelected`
- L'identifiant vit dans l'attribut `_ulitemid`, sous la forme
  `<préfixe>:t<idPage>` — `r1:0:page:t4874493` → `idPage=4874493`
- Le nom d'onglet se lit dans l'attribut `title` du lien, **pas** dans son texte :
  `innerText` est parfois vide selon l'état de rendu.

#### L'onglet est adressable par URL — pas besoin de cliquer

```
/ena/site/module?idSite=<idSite>&idModule=<idModule>&editionModule=false&idPage=<idPage>
```

Vérifié sur les deux cours : naviguer avec `idPage` sélectionne bien l'onglet
correspondant (`p_AFSelected` se déplace) et sert son contenu. C'est le même
principe que `&onglet=` pour les évaluations. Le clic ADF décrit dans la
version précédente de ce document est inutile.

#### Piège : sans `idPage`, l'onglet servi est imprévisible

Trois navigations successives vers la même URL **sans** `idPage` ont servi
`idPage=3608113`, puis `3608112`, puis `3608113` : le serveur ADF réaffiche le
dernier onglet consulté dans la session. Il ne s'agit donc pas du « premier
onglet » ni d'un défaut stable — un extracteur qui ne précise pas `idPage`
archive un onglet arbitraire.

#### Coût mesuré de l'omission

JKL-4000, module 1, fichiers distincts par onglet :

| Onglet | `idPage` | Fichiers |
|---|---|---|
| Notes de cours | 3608111 | 2 |
| Exercices 3e édition | 3608112 | 11 |
| Exercices 2e édition | 3608113 | 4 |

17 fichiers au total, dont un seul onglet était récupéré — et pas toujours le
même. Sur ABC-1000 l'onglet « Général » ne porte aucun fichier et « Contenu du
module » en porte un : c'est ce cours de phase 0 qui avait donné l'impression
qu'un clic sur un nom d'onglet suffisait.

#### Doublons de liens

Chaque fichier apparaît deux fois dans le DOM (lien sur l'icône et lien sur le
texte), pointant vers la même URL. La déduplication par URL du manifeste s'en
charge déjà.

### Étape 4 — les liens de fichiers passent par un traceur

```
/analytique/evenement/fichier
  ?idFichier=140274665
  &idSite=100001
  &url=%2Fcontenu%2Fsitescours%2F040%2F04000%2F202601%2Fsite100001
       %2Fmodules1434431%2Fmodule1795743%2Fpage4874493%2Fbloccontenu5204221
       %2FCours_1_-_Introduction-janvier%25202026.pptx
       %3Fidentifiant%3D0a981dbdc4212d59737bc2e400fd0d39076480bc
```

Le paramètre `url` contient, en double encodage, l'URL réelle du fichier sous
`/contenu/sitescours/...`. Deux stratégies possibles :

1. Suivre le lien d'analytique et laisser le serveur rediriger.
2. **Recommandé** : décoder le paramètre `url` et télécharger directement la
   ressource `/contenu/...`. On évite d'alimenter les statistiques de
   consultation de l'Université pour rien, et on obtient le nom de fichier
   d'origine (`Cours_1_-_Introduction-janvier 2026.pptx`) sans dépendre des
   en-têtes de réponse.

Le texte affiché du lien est tronqué (`Cours 1 - Introduction-.pptx`) : le vrai
nom de fichier doit être tiré du paramètre `url`, pas du texte du lien.

### Étape 5 — distinguer les ressources internes des liens externes

La même page contient des liens sortants vers des sites tiers (observé :
`oiq.qc.ca`). Règle : seules les URL sous `/contenu/sitescours/` sont
téléchargées. Les liens externes sont consignés dans le manifeste pour mémoire,
sans être suivis.

## 7ter. Évaluations, dépôts et notes — URL déterministes

Relevé sur DEF-2000 (`idSite=100002`). Ce sont les URL les plus précieuses du
projet, car elles couvrent le cœur du périmètre.

```
/ena/site/evaluations?idSite=<idSite>          liste des évaluations
/ena/site/resultats?idSite=<idSite>            sommaire des résultats (les notes)
/ena/site/evaluation?idSite=<idSite>&idEvaluation=<idEval>&onglet=boiteDepots
/ena/site/evaluation?idSite=<idSite>&idEvaluation=<idEval>&onglet=resultats
/ena/site/evaluation?idSite=<idSite>&idEvaluation=<idEval>&onglet=equipesTravail
```

Le paramètre `onglet` sélectionne l'onglet directement dans l'URL : pas besoin
de cliquer, contrairement à l'onglet « Contenu du module ». Les `idEvaluation`
se relèvent dans la page `evaluations` (observés : 1035434, 1035435, 1035436).

Parcours des dépôts : `evaluations` → pour chaque `idEvaluation`,
`onglet=boiteDepots` → relever les liens de fichiers, qui suivent le même
schéma `/analytique/evenement/fichier?...&url=...` décrit plus haut.

## 7quater. Chaque site de cours est structuré différemment

Constat confirmé par l'utilisateur et vérifié sur trois sites. Les menus n'ont
ni les mêmes entrées, ni les mêmes libellés pour une même fonction.

| Site | Menu observé |
|---|---|
| ABC-1000 (100001) | Introduction, Plan de cours, Informations générales, Description du cours, **Feuille de route**, Évaluations et résultats, Matériel didactique, Bibliographie |
| DEF-2000 (100002) | Introduction, Informations générales, Description du cours, **Contenu et activités**, Évaluations et résultats, Matériel didactique, Médiagraphie et annexes, Plan de cours |
| Formation EDI (100006) | Introduction, Concepts de base, Six biais, Comportements inclusifs, Boite à outils, Crédits et remerciements |

« Feuille de route » et « Contenu et activités » désignent la même chose. Le
troisième site n'a ni plan de cours, ni évaluations, ni modules au sens des deux
autres.

**Conséquence de conception, non négociable :** l'outil ne doit jamais présumer
d'une structure de menu. Il doit :

1. Essayer les URL déterministes connues (`evaluations`, `resultats`,
   `modules`) et accepter qu'elles ne donnent rien sur un site donné.
2. Lire le menu réel du site dans le DOM et parcourir ce qu'il trouve.
3. Récolter, sur **toute** page visitée, les liens `/contenu/sitescours/...`,
   quelle que soit la section où ils apparaissent.

Autrement dit : découverte générique par défaut, URL déterministes comme
accélérateur, jamais l'inverse.

### Le principe qui rend le projet faisable : les libellés varient, les URL non

Vérifié : `/ena/site/modules?idSite=100002` répond correctement sur DEF-2000 —
7 modules, même schéma `/ena/site/module?...&idModule=...` — **alors que ce site
nomme la section « Contenu et activités »** et non « Feuille de route ».

Les sections que le professeur renomme, réordonne ou supprime restent
accessibles par leur URL canonique. L'outil doit donc s'appuyer sur les URL
(`modules`, `evaluations`, `resultats`, `evaluation?...&onglet=...`) et traiter
les libellés du menu uniquement comme une source secondaire de découverte, pour
attraper les sections hors schéma.

Confirmé par l'utilisateur : la section « Contenu et activités » contient aussi
les documents fournis par le professeur, au même titre que « Feuille de route ».
Les deux se parcourent par `/ena/site/modules?idSite=<idSite>`.

## 7quinquies. Structure réelle des dépôts et des évaluations

Relevé sur ABC-1000 (`idSite=100001`), à partir de captures fournies par
l'utilisateur.

### Onglets d'une évaluation

Une évaluation présente cinq onglets : **Description**, **Équipe de travail**,
**Boîte de dépôt**, **Évaluation des pairs**, **Résultats**. Ils correspondent
au paramètre `onglet` de `/ena/site/evaluation?idSite=<id>&idEvaluation=<id>`.

### Boîte de dépôt

Tableau « Liste des documents déposés », colonnes : **Nom du document** (lien de
téléchargement), **Taille**, **Déposé par**, **Date de remise**.

Exemple observé : `Z1-PHI3900-H2026-TP2 - Éthique et professionnalisme.docx`,
3,25 Mo, déposé par « Buteau, Laurent », le 12 avr. 2026 à 18h43.

Deux enseignements :

1. **« Déposé par » n'est pas nécessairement l'utilisateur.** Sur un travail
   d'équipe, c'est un coéquipier qui dépose pour tout le monde. Cette colonne
   doit être conservée dans l'archive : elle est la seule trace de qui a remis
   quoi.
2. Un fichier remis reste accessible même quand le site est passé en
   consultation seulement.

### DANGER : bouton « Supprimer » dans la boîte de dépôt

La page porte une case à cocher devant chaque document et un bouton
**« Supprimer »**. Un clic malencontreux détruirait un travail remis.

C'est le second piège destructeur du site, après `cmdObtenirPlanCours`. La règle
en découle, et elle est absolue : **l'outil ne clique que des liens de
navigation et de téléchargement identifiés comme tels.** Jamais un bouton,
jamais une case à cocher, jamais une soumission de formulaire. Sur cette page en
particulier, seuls les liens du tableau des documents sont suivis.

### Page « Évaluations et résultats »

Elle ne contient **pas** les notes. Sa « Liste des évaluations » porte les
colonnes **Titre**, **Date**, **Pondération**, organisées en regroupements :

```
Sommatives
  Travaux pratiques : Création et résolution d'études de cas      30 %
    (Somme des évaluations de ce regroupement)
    Étude de cas - Travail pratique 1 (TP1)   dû le 1 févr. 2026    5 %
    Étude de cas - travail pratique 2 (TP2)   dû le 12 avr. 2026   25 %
```

Chaque ligne d'évaluation porte trois icônes à droite, qui mènent aux onglets
boîte de dépôt, équipe de travail et résultats.

La page commence par une série d'ancres vers des textes de politique — barème de
conversion, plagiat, politique du français, usage de ChatGPT, etc. Ce sont des
sections de texte, pas des fichiers.

**Les notes se trouvent derrière « Sommaire des résultats »**, en haut à droite,
c'est-à-dire `/ena/site/resultats?idSite=<id>`. Toute extraction de notes doit
viser cette page, et non la liste des évaluations.

### Structure du « Sommaire des résultats »

`/ena/site/resultats?idSite=<id>`. Relevé sur ABC-1000. **Quatre** colonnes :

| Colonne | Contenu | Exemples |
|---|---|---|
| 1 | Titre de l'évaluation, en lien | `Examen de mi-session (Em)` |
| 2 | Pourcentage obtenu | `70 %`, `79,75 %`, `100 %`, `0 %` |
| 3 | Pondération, affichée en gris | `15 %`, `39,99 %`, `1 %` |
| 4 | Points obtenus sur points possibles | `10,5 / 15`, `31,89 / 39,99`, `0,83 / 1` |

Trois formes de lignes cohabitent :

1. **Ligne d'évaluation** — les quatre colonnes sont remplies.
2. **Ligne de regroupement** — porte le nom du regroupement suivi de
   « (Somme des évaluations de ce regroupement) », avec la pondération et le
   total du groupe, mais **pas** de pourcentage en colonne 2. Exemples relevés :
   `Examen final (en classe!)` 39,99 % → 31,89 / 39,99 ;
   `Questionnaires d'autoévaluation (Évaluation formative)` 10 % → 9,83 / 10 ;
   `Participation aux forums (Évaluation formative)` 5 % → 0 / 5.
3. **Ligne de total**, en fin de tableau, sans titre : `78,59 / 100`.

Points d'attention pour l'extraction :

- Les nombres sont en notation française, avec la virgule comme séparateur
  décimal : `10,5`, `0,83`, `39,99`. Ne pas les convertir en flottants à
  l'aveugle ; les conserver tels quels dans le CSV est plus sûr et plus fidèle.
- Le modèle `Note` de la spec d'origine — évaluation, note, sur, pondération,
  moyenne de groupe — ne correspond pas à cette page. Il n'existe aucune moyenne
  de groupe dans cette vue, et il manque la colonne du pourcentage obtenu ainsi
  que la notion de regroupement.

## 7sexies. Périmètre arrêté avec l'utilisateur

- **Seuls les cours sont archivés.** Les « Autres activités » du tableau de bord
  — formations institutionnelles type EDI, prévention, santé et sécurité — sont
  hors périmètre par décision explicite. L'énumération par `/portail/cours`
  convient donc parfaitement.
- Les sections « Matériel didactique », « Médiagraphie et annexes » et
  « Bibliographie » ne sont pas parcourues.
- Les sites anciens de structure atypique seront traités à l'usage, à la
  première exécution réelle, plutôt que par anticipation.

## 8. Ce que la reconnaissance n'a pas encore établi

- L'emplacement exact des boîtes de dépôt dans l'interface d'un site, et la
  forme des liens vers les fichiers remis et les rétroactions.
- La page « Évaluations et résultats » et la façon d'en extraire les notes.
- Les noms de section acceptés par `/lieninterne/redirection/<idSite>/<section>`
  au-delà de `liste_modules`, à relever dans le DOM plutôt qu'à deviner.
- Si les sites anciens (Automne 2022) ont la même structure que les récents.
- Si `sitescours.monportail.ulaval.ca/portail/cours` — présent dans le menu —
  liste l'historique complet, ce qui serait plus simple que le sélecteur de
  session.

Ces points se règlent lors de la première exécution du mode `--un-seul-cours`,
sur un cours ancien puis un cours récent.

## 9. Conséquences sur le design

1. Le module `api.py` tel que décrit dans la spec — client REST — **n'est pas
   réalisable** pour l'ENA. Il devient un module de pilotage et d'extraction
   Playwright.
2. Les fixtures JSON prévues pour les tests n'existent pas. Elles sont
   remplacées par des **pages HTML capturées**, sur lesquelles se testent les
   fonctions d'extraction.
3. Le téléchargement reste du HTTP direct avec les cookies : la partie
   `stockage.py`, le nommage, la reprise et le manifeste restent valables tels
   quels.
4. L'énumération de l'historique passe par le sélecteur de session du panneau
   « Liste des cours ».

## 10. Pièges d'exploitation

### Le navigateur restait bloqué sur le domaine de connexion Microsoft (piège résolu)

**Symptôme observé.** `python -m extracteur` (quel que soit le mode) ouvrait
bien le navigateur, mais la page restait indéfiniment sur un domaine de
connexion Microsoft (`login.microsoftonline.com` et apparentés). L'attente de
connexion finissait par expirer sans jamais détecter la connexion, alors que
l'utilisateur avait bel et bien terminé son authentification — parfois
visible dans une **autre** fenêtre du navigateur, déjà authentifiée sur
`sitescours.monportail.ulaval.ca`, pendant que la fenêtre pilotée par
Playwright, elle, restait plantée sur l'écran Microsoft.

Cette dernière observation (une fenêtre authentifiée visible à l'écran) était
trompeuse : elle pousse naturellement à chercher le défaut du côté de la
détection de connexion (`est_page_authentifiee`), du sélecteur de compte, ou
d'une session déjà expirée — trois pistes qui se sont révélées fausses en
usage réel avant que la vraie cause ne soit identifiée.

**Cause réelle.** Le profil de navigateur Chromium persistant, conservé à
l'époque dans le dossier `.session` à la racine du projet, s'était corrompu —
à deux reprises en conditions réelles, vraisemblablement à la suite de
processus Chromium tués en cours d'exécution (interruption brutale du
processus Python, `Ctrl+C` répété, plantage). Un profil corrompu pouvait
faire échouer silencieusement la chaîne de redirections OAuth : le navigateur
restait sur l'écran de connexion Microsoft sans jamais recevoir la
redirection finale vers le domaine `ulaval.ca`, quel que soit ce que faisait
l'utilisateur dans cette fenêtre. Le diagnostic a coûté du temps à chaque
occurrence, faute d'indice reliant le symptôme (blocage sur l'écran Microsoft)
à sa cause réelle (un dossier de profil corrompu, invisible depuis la
console).

**Remède essayé, puis abandonné.** Un drapeau `--reinitialiser-session`
supprimait le dossier `.session` avant l'ouverture, forçant une
authentification complète depuis un profil neuf. Il fonctionnait, mais
demandait à l'utilisateur de reconnaître lui-même le symptôme et de se
souvenir du drapeau — exactement ce qui avait coûté une soirée entière la
première fois. Le compromis (un lancement plus rapide la plupart du temps,
contre un risque de blocage trompeur de temps en temps) a été jugé ne pas
valoir son prix pour un outil dont l'unique raison d'être est de sauver du
contenu avant une échéance ferme.

**Remède retenu : plus de profil persistant du tout.** Depuis, chaque
lancement de `SessionNavigateur.ouvrir()` (voir `extracteur/auth.py`) crée un
dossier de profil neuf dans le dossier temporaire du système
(`tempfile.mkdtemp`), jamais dans le dépôt ni dans un dossier synchronisé
OneDrive. `SessionNavigateur.fermer()` le supprime systématiquement, y
compris sur interruption clavier, session expirée ou erreur inattendue —
cette garantie s'appuie sur le `try/finally` qui entoure déjà chaque appel à
`fermer()` dans `extracteur/__main__.py`. Un profil qui n'existe plus au
lancement suivant ne peut plus se corrompre d'un lancement à l'autre : ce
piège précis ne peut plus se reproduire.

Le coût est une authentification complète à chaque lancement, MFA compris —
l'outil l'annonce explicitement avant d'ouvrir le navigateur, pour que ce ne
soit jamais pris pour une régression. Le calcul reste favorable : le mode
`--tout` traite les 33 cours du projet en une seule exécution, donc une seule
authentification suffit pour tout récupérer.

**Pour qui reprend ce projet plus tard.** Si l'idée de réintroduire un profil
persistant revient (pour économiser une reconnexion), le compromis a déjà été
pesé et écarté : deux corruptions en conditions réelles, un diagnostic coûteux
à chaque fois, et un drapeau de réinitialisation qui déplaçait le problème sur
l'utilisateur plutôt que de le supprimer. Le profil persistant a un coût de
fiabilité qui n'est pas justifié par le gain d'une reconnexion évitée de temps
en temps.
