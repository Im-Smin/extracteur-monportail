# Extracteur monPortail — design

Date : 2026-09-14
Statut : design approuvé, **révisé après la phase 0**, en attente du plan
d'implémentation

> Révision du 2026-09-14, après reconnaissance sur session réelle. L'hypothèse
> d'origine — une API JSON à consommer — s'est révélée fausse : les sites de
> cours sont une application Oracle ADF sans API REST. Le pilotage d'interface
> devient obligatoire et non un repli. Les sections 2, 4, 6, 7, 10 et 11 ont été
> mises à jour en conséquence. Constats détaillés dans
> `docs/api-monportail.md`.

## 1. Problème

La plateforme monPortail de l'Université Laval ferme en novembre 2026. Tout le
contenu accessible à l'utilisateur disparaîtra : fichiers remis dans les boîtes
de dépôt, rétroactions des professeurs, documents déposés sur les sites de
cours, plans de cours et relevés de notes.

Objectif : un logiciel local qui télécharge ce contenu, pour tous les cours de
l'historique de l'utilisateur, et le range dans une archive de fichiers lisible
sans la plateforme et sans le logiciel lui-même.

## 2. Périmètre

Dans le périmètre :

- Boîtes de dépôt : fichiers remis par l'utilisateur et rétroactions ou
  corrections des professeurs lorsqu'elles sont accessibles.
- Documents déposés par les professeurs sur les sites de cours, avec la
  structure de dossiers d'origine.
- Plan de cours de chaque cours.
- Notes et résultats d'évaluation, exportés en CSV.
- Tous les cours de l'historique, toutes sessions confondues.
- **Capture PDF de chaque page visitée** (modules, sections, évaluations).
  Ajouté après la phase 0 : la reconnaissance a montré que le texte rédigé par
  les professeurs vit dans les pages elles-mêmes, pas seulement dans les pièces
  jointes. S'en tenir aux fichiers perdrait l'essentiel du contenu de certains
  cours.

Hors périmètre, par décision explicite :

- Annonces, nouvelles, forums de discussion.
- Capsules vidéo et enregistrements intégrés.
- Index HTML navigable de l'archive.
- Sites de cours hébergés sur Brio : la plateforme est conservée.

## 3. Contraintes techniques établies

Reconnaissance effectuée le 2026-09-14 sur `https://monportail.ulaval.ca/portail` :

- monPortail est une application monopage qui redirige vers Microsoft Entra ID
  (`login.microsoftonline.com`, tenant `56778bd5-6a3f-4bd3-a265-93163e4d5bfe`).
- Flux OAuth2 « authorization code » avec PKCE, `client_id`
  `bff03418-e5d5-4078-865e-984b4cf20ebe`, `redirect_uri`
  `https://monportail.ulaval.ca/auth/retour/`.
- Scope demandé : `api://api.ulaval.ca/.default offline_access`.

Conséquences : il existe une API JSON derrière l'interface, l'accès se fait par
jeton porteur, et l'authentification exige un MFA interactif. Aucun test
automatisé ne pourra donc atteindre l'API réelle.

Environnement cible : Windows 11, Python 3.12. Node 24 est présent sur la
machine mais n'est pas utilisé par ce projet.

## 4. Phase 0 — reconnaissance de l'API

Le logiciel ne peut pas être écrit avant que la carte des endpoints soit
connue. Phase préalable, menée en séance guidée :

1. L'utilisateur se connecte lui-même à monPortail dans le navigateur intégré.
   Ses identifiants ne sont ni saisis ni observés par l'agent.
2. Navigation dans deux ou trois cours, dont un cours ancien et un cours
   récent, en ouvrant une boîte de dépôt, la liste des documents, le plan de
   cours et les notes.
3. Lecture du trafic réseau et rédaction de la carte d'API.

Livrables de la phase 0, versionnés dans le dépôt :

- `docs/api-monportail.md` : pour chaque besoin — liste des cours, documents,
  dépôts, plan de cours, notes — la méthode, l'URL, les paramètres, la forme de
  la réponse et le mécanisme d'authentification observé.
- `tests/fixtures/*.html` : pages réelles capturées, anonymisées — noms de
  personnes et identifiants remplacés — avant d'entrer dans le dépôt. Ce sont
  des pages HTML et non des réponses JSON, l'API REST espérée n'existant pas.

**Statut : phase 0 terminée le 2026-09-14.** `docs/api-monportail.md` est écrit
et commité. La chaîne complète — sessions, sites, modules, fichiers,
évaluations, boîtes de dépôt, résultats — est validée sur session réelle. Les
captures HTML de fixtures restent à produire lors de la première exécution du
mode `--un-seul-cours`, sur un cours ancien puis un cours récent.

## 5. Déroulé utilisateur

1. Lancement de l'outil ; une fenêtre s'ouvre avec un bouton **Se connecter**.
2. Chromium s'ouvre sur monPortail. L'utilisateur se connecte à la main, MFA
   compris. Le programme attend d'observer un premier appel authentifié et en
   capture le jeton et les cookies. Le profil du navigateur est conservé dans un
   sous-dossier local ; les lancements suivants ne redemandent pas la connexion
   tant que la session vit.
3. La liste des cours s'affiche, groupée par session, cochée par défaut.
4. L'utilisateur décoche ce qu'il ne veut pas, choisit le dossier de
   destination, clique **Archiver**.
5. Progression : barre globale, ligne « cours et fichier en cours », journal
   défilant, bouton **Arrêter** qui interrompt proprement. Si la session
   d'authentification meurt, le même bouton devient **Reprendre** une fois
   l'utilisateur reconnecté, conformément à la section 9.
6. Fin : rapport des échecs affiché, bouton **Créer le .zip**.

Le logiciel possède deux points d'entrée sur le même cœur. `python -m
extracteur` ouvre la fenêtre décrite ci-dessus, et c'est le mode normal.
`python -m extracteur --un-seul-cours <SIGLE>` exécute la même chaîne sans
interface, en console, pour la vérification décrite à la section 10 ; le
navigateur de connexion s'ouvre de la même façon.

## 6. Architecture

Découpage en modules à responsabilité unique. L'interface et le réseau ne
communiquent jamais directement : l'archiveur tourne dans un thread de travail
et publie ses événements dans une `queue.Queue` que la fenêtre vide toutes les
100 ms. Cela évite l'interface gelée pendant un gros téléchargement et permet de
tester tout l'archiveur sans ouvrir de fenêtre.

| Module | Rôle | Dépend de |
|---|---|---|
| `auth.py` | Ouvre le navigateur, attend la connexion, expose une session — jeton et cookies — et sait la renouveler | Playwright |
| `ena.py` | Pilotage du site de cours et extraction. Navigue par URL canoniques, lit le DOM, rend les objets du modèle. Ne touche jamais au disque | `auth`, `modele` |
| `extraction.py` | Fonctions pures HTML → objets : liste de modules, liens de fichiers, tableau de résultats. Aucune dépendance navigateur, donc entièrement testable | `modele` |
| `modele.py` | Objets `Cours`, `Fichier`, `Depot`, `Note` : contrat entre l'API et l'archiveur | — |
| `stockage.py` | Écriture disque : noms sûrs, collisions, `.part`, reprise, SHA-256 | — |
| `archiveur.py` | Orchestration, arborescence, manifeste, événements de progression | tous |
| `manifeste.py` | Lecture et écriture du manifeste et du rapport | — |
| `ui.py` | Fenêtre Tkinter. Aucun appel réseau | `archiveur` |

Interface publique de `ena.py` :

- `lister_sessions() -> list[Session]`
- `lister_sites(session) -> list[Cours]`
- `lister_modules(cours) -> list[Module]`
- `fichiers_du_module(module) -> list[Fichier]`
- `lister_evaluations(cours) -> list[Evaluation]`
- `boite_depot(evaluation) -> list[Fichier]`
- `resultats(cours) -> list[Note]`
- `capturer_page_pdf(url, destination) -> Fichier`

Le parcours est **hybride, URL d'abord**. La phase 0 a établi que les libellés
de menu varient d'un site à l'autre — « Feuille de route » ici, « Contenu et
activités » là — alors que les URL canoniques restent valides partout :

```
/ena/site/modules?idSite=<id>
/ena/site/module?idSite=<id>&idModule=<id>&editionModule=false
/ena/site/evaluations?idSite=<id>
/ena/site/evaluation?idSite=<id>&idEvaluation=<id>&onglet=boiteDepots
/ena/site/resultats?idSite=<id>
```

L'outil tente donc ces URL en premier, accepte qu'elles ne répondent pas sur un
site donné, puis complète en lisant le menu réel du DOM. Sur **toute** page
visitée, il récolte les liens `/contenu/sitescours/...` sans présumer de la
section d'où ils viennent. Détails complets dans `docs/api-monportail.md`.

**Règle de sécurité inscrite dans le code.** L'outil ne suit que des liens de
navigation et de téléchargement. Toute commande ADF dont l'identifiant commence
par `cmd` est ignorée par défaut : la phase 0 a trouvé `cmdObtenirPlanCours`,
qui ressemble à un lien de téléchargement PDF mais déclenche en réalité « Vous
vous apprêtez à publier une nouvelle version du plan de cours ». Un archiveur
n'écrit jamais sur la plateforme qu'il archive.

**Téléchargement des fichiers.** Les liens de fichiers passent par un traceur
`/analytique/evenement/fichier?...&url=<URL réelle encodée>`. L'outil décode le
paramètre `url` et télécharge directement la ressource `/contenu/sitescours/...`
avec les cookies de la session. Cela évite d'alimenter les statistiques de
consultation de l'Université et donne le nom de fichier d'origine, que le texte
du lien tronque.

## 7. Arborescence de sortie

```
Archive monPortail/
├── 2019-3 Automne/
│   └── YZA-9000 Introduction à la programmation/
│       ├── Plan de cours/
│       │   └── plan-de-cours.pdf
│       ├── Documents/
│       │   └── Module 1 - Les bases/
│       │       └── notes-cours-01.pdf
│       ├── Mes dépôts/
│       │   └── TP1 - Calculatrice/
│       │       ├── Remis/
│       │       └── Rétroaction/
│       ├── Pages/
│       │   ├── 01 - Introduction.pdf
│       │   └── 02 - Notions principales et vocabulaire.pdf
│       └── notes.csv
├── 2020-1 Hiver/
├── notes-tous-cours.csv
├── manifeste.csv
└── _rapport.html
```

Décisions :

- Sessions préfixées `AAAA-N` (`2019-3 Automne`, `2020-1 Hiver`) pour que l'ordre
  alphabétique de l'Explorateur soit l'ordre chronologique. Convention retenue :
  1 = Hiver, 2 = Été, 3 = Automne.
- La structure de dossiers du professeur est reproduite sous `Documents/`.
- Plan de cours : export PDF officiel si l'API en fournit un ; sinon impression
  de la page en PDF par le Chromium déjà ouvert pour l'authentification.
- Notes en CSV par cours et en CSV consolidé à la racine : évaluation, note, sur
  combien, pondération, moyenne du groupe si l'API la fournit.
- `manifeste.csv` : une ligne par fichier — chemin local, taille, SHA-256, URL
  d'origine, horodatage, statut. Sert à la vérification et à la reprise.

Destination recommandée : un dossier local non synchronisé OneDrive. Une
création massive de fichiers déclenche une tempête de synchronisation, et
OneDrive ajoute ses propres restrictions de nommage à celles de Windows.

## 8. Nommage de fichiers sous Windows

Règles appliquées par `stockage.py` :

- Caractères interdits `< > : " / \ | ? *` remplacés par `-`.
- Noms réservés (`CON`, `PRN`, `AUX`, `NUL`, `COM1` à `COM9`, `LPT1` à `LPT9`)
  préfixés par `_`.
- Points et espaces en fin de nom retirés.
- Accents conservés, NTFS les gère.
- Chaque segment tronqué à 80 caractères, extension préservée.
- Collisions suffixées ` (2)`, ` (3)`, et ainsi de suite.
- Écriture via le préfixe de chemin long `\\?\` pour dépasser la limite de 260
  caractères.

## 9. Robustesse

**Reprise idempotente.** Chaque téléchargement s'écrit en `.part` puis est
renommé : un fichier portant son nom final est donc complet. Au démarrage,
l'outil relit le manifeste et saute ce qui est déjà présent à la bonne taille.
Il n'y a pas de mode reprise à activer — relancer, c'est reprendre.

**Isolation par cours.** Une exception dans un cours est consignée et le
traitement passe au suivant ; elle n'interrompt jamais la session. Même
isolation au niveau du fichier.

**Réessais différenciés.** Erreurs serveur, délais dépassés et connexions
coupées : trois tentatives avec attente croissante de 2 s, 8 s puis 30 s. Codes
`403` et `404` : aucun réessai, consignation directe dans le rapport.

**Jeton expiré.** Un `401` déclenche une demande de jeton frais au navigateur
resté ouvert et un seul rejeu de la requête. Si la session est réellement morte,
la file est mise en pause et l'interface affiche une invitation à se
reconnecter, au lieu d'épuiser les cours restants en erreurs
d'authentification. L'utilisateur se reconnecte puis clique **Reprendre**.

**Débit limité.** Trois téléchargements simultanés au maximum et une pause
légère entre les appels de découverte, afin de ne pas déclencher de limitation
ou de blocage côté Université.

**Écriture en flux.** Aucun fichier n'est chargé entièrement en mémoire. Le
SHA-256 est calculé pendant l'écriture.

**Rapport d'échecs.** `_rapport.html` liste chaque élément non récupéré : cours,
fichier, cause, URL d'origine cliquable. C'est le document qui indique ce qu'il
reste à récupérer manuellement avant la fermeture de la plateforme. Un journal
complet, `archive.log`, l'accompagne. Une vérification finale recompte les
fichiers sur disque face au manifeste avant de déclarer l'archive terminée.

## 10. Tests

Aucun test automatisé ne peut atteindre l'API réelle, puisqu'elle exige un MFA
interactif. La stratégie rend testable tout ce qui l'est sans réseau et compense
par un essai manuel ciblé.

1. **Nommage de fichiers, en TDD strict.** Table de cas écrite avant le code :
   caractères interdits, `CON.txt`, espaces et points de fin, accents,
   troncature à 80 caractères sans manger l'extension, collisions successives,
   chemin total au-delà de 260 caractères.
2. **Extraction, contre pages HTML capturées.** `extraction.py` ne dépend pas du
   navigateur : on lui donne le HTML d'une page réelle et on vérifie les objets
   produits. Les captures couvrent au moins trois structures différentes — un
   cours à « Feuille de route », un cours à « Contenu et activités », et un site
   sans plan de cours ni évaluations — puisque la phase 0 a montré qu'aucun site
   n'est monté pareil.
3. **Reprise, avec faux client et dossier temporaire.** Fichier absent :
   téléchargé. Présent à la bonne taille : sauté. `.part` orphelin :
   retéléchargé. Manifeste existant : rejoué.
4. **Réessais, avec transport simulé.** `500, 500, 200` réussit en trois appels.
   `401, 200` déclenche un renouvellement et un seul rejeu. `403` ne provoque
   aucun réessai et atterrit dans le rapport.

Non testés automatiquement, par décision assumée : Playwright et la connexion
réelle, ainsi que la fenêtre Tkinter.

Garde-fou principal : le mode console `--un-seul-cours <SIGLE>` décrit à la
section 5, qui exécute la chaîne complète sur un seul cours. Vérification visuelle du dossier produit — plan de
cours présent, dépôts au bon endroit, notes exactes — avant de lancer
l'archivage complet.

## 11. Risques

| Risque | Traitement |
|---|---|
| Chaque site de cours a une structure différente | Parcours par URL canoniques d'abord, lecture du menu réel ensuite, récolte des liens `/contenu/` sur toute page ; isolation par cours |
| Une section échappe aux URL canoniques | Découverte par le menu du DOM ; ce qui reste introuvable est consigné dans `_rapport.html` |
| Un clic déclenche une écriture sur la plateforme | Aucune commande ADF `cmd*` n'est suivie ; liste blanche explicite exigée |
| Session Entra ID expirée en cours d'archivage | Renouvellement de jeton, puis mise en pause et invitation à se reconnecter |
| Limitation de débit côté Université | Trois téléchargements simultanés au maximum, pauses entre les appels |
| Rétroactions de professeurs inaccessibles par l'API | Consignées dans `_rapport.html` pour récupération manuelle |
| Échéance de novembre 2026 | Phase 0 puis mode `--un-seul-cours` en priorité, archivage complet dès que la chaîne est validée |
