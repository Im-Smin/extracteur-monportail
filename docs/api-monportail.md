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
- `GET sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=181216` avec
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
/ena/site/accueil?idSite=181216
/ena/site/accueil?idSite=181216&_js=true
/ena/site/accueil?idSite=181216&_js=true&idPage=4874490
/ena/site/plandecours?idSite=181216
/ena/site/evaluations?idSite=181216
/ena/site/depots?idSite=181216
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

Sites relevés pour Hiver 2026 : `181216` (PHI-3900), `183033` (GIN-3320),
`181638` (MED-1100). Autres `idSite` vus sur le tableau de bord : `162134`,
`159476`, `162283`, `149047`, `148734`, `153770`.

## 5. Les fichiers de contenu ont des URL directes

Observé sur les ressources chargées par une page de cours :

```
https://sitescours.monportail.ulaval.ca/contenu/sitescours/040/04000/202601/
  site181216/accueil/bloctexte1226860/ressourcestexte/
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

Menu de gauche observé sur PHI-3900 : Introduction, Plan de cours, Informations
générales, Description du cours, Feuille de route, Évaluations et résultats,
Matériel didactique, Bibliographie. Une barre d'outils distincte donne accès aux
autres fonctions du site.

Chaque page porte un `idPage` (exemple : `4874490` pour l'accueil de PHI-3900),
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

Identifiants du dossier : IDUL `jamof7`, identifiant utilisateur mpo `6294113`.
Codes de session au format `AAAASS` : `202601` = Hiver 2026, `202609` = Automne
2026, `202605` = Été 2026.

Aucun de ces appels ne retourne d'`idSite` ENA. Ils ne remplacent donc pas
l'énumération par l'interface, mais `/etudes/v1/inscriptions/...` reste une
piste pour recouper la liste des cours et pour le relevé de notes officiel.

## 8. Ce que la reconnaissance n'a pas encore établi

- L'emplacement exact des boîtes de dépôt dans l'interface d'un site, et la
  forme des liens vers les fichiers remis et les rétroactions.
- La page « Évaluations et résultats » et la façon d'en extraire les notes.
- Si les sites anciens (Automne 2022) ont la même structure de menu que les
  récents.

Ces trois points se règlent lors de la première exécution du mode
`--un-seul-cours`, sur un cours ancien puis un cours récent.

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
