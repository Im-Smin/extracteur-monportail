# Extracteur monPortail

Archivez tout votre contenu monPortail (Université Laval) avant la fermeture de
la plateforme, le **1er novembre 2026**. Passé cette date, plus rien n'y sera
récupérable.

L'outil ouvre un navigateur, vous laisse vous connecter vous-même, parcourt vos
sites de cours et copie sur votre disque les documents, les pages, vos travaux
remis et vos résultats. Il vérifie ensuite que tout est bien là, puis compresse
le tout dans un `.zip`.

> **Aucun mot de passe ne transite par le programme.** Vous vous authentifiez
> vous-même dans la fenêtre du navigateur, exactement comme d'habitude, MFA
> compris. Le programme ne lit que les témoins de session de cette fenêtre-là.

---

## Ce qu'il récupère

Un dossier par session, puis un par cours :

```
Automne 2025/
  ABC-1000 Nom du cours/
    Plan de cours/        le PDF officiel de l'Université
    Documents/            les fichiers fournis par l'enseignant, rangés par module et par onglet
    Pages/                une copie PDF de chaque page visitée
    Évaluations/          par évaluation : l'énoncé, la boîte de dépôt,
                          VOS travaux remis, la rétroaction et la note
    notes.csv             vos résultats pour ce cours
```

Et à la racine de l'archive :

| Fichier | Contenu |
|---|---|
| `notes-tous-cours.csv` | tous vos résultats réunis |
| `manifeste.csv` | l'inventaire de ce qui a été écrit, avec taille et empreinte SHA-256 |
| `_rapport.html` | ce qui a échoué, s'il y a lieu |

**La copie PDF des pages compte autant que les pièces jointes** : beaucoup
d'enseignants écrivent leurs consignes directement dans la page, et ce texte
n'existe sous forme d'aucun fichier téléchargeable.

## Ce qu'il ne récupère pas

- les annonces, les forums de discussion et les vidéos ;
- les « Autres activités » du tableau de bord (formations institutionnelles) :
  seuls les cours sont traités ;
- les sections « Matériel didactique », « Médiagraphie et annexes » et
  « Bibliographie », qui ne listent que des renvois vers des ouvrages.

---

## Installation

Python 3.12 ou plus récent.

```bash
git clone https://github.com/Im-Smin/extracteur-monportail.git
cd extracteur-monportail
pip install -e .
python -m playwright install chromium
```

## Utilisation

### Fenêtre graphique

```bash
python -m extracteur
```

Choisissez un emplacement, ce que vous voulez archiver (tout / une session / un
seul cours), puis **Commencer**. La progression défile dans la fenêtre. À la
fin, l'archive est vérifiée puis compressée.

### En ligne de commande

```bash
python -m extracteur --lister                      # vos sessions et vos cours, sans rien télécharger
python -m extracteur --tout                        # tout, de la session la plus ancienne à la plus récente
python -m extracteur --session "Automne 2022"      # une seule session
python -m extracteur --un-seul-cours 100001        # un seul cours, par son idSite
python -m extracteur --verifier                    # confronte une archive existante à son manifeste
python -m extracteur --zip                         # compresse une archive existante
```

`--destination` choisit le dossier de sortie (défaut : `Archive monPortail`).

`--verifier` et `--zip` ne se connectent jamais à monPortail : ils ne touchent
qu'au disque. Ils resteront utilisables bien après la fermeture de la
plateforme.

### Codes de sortie

| Code | Signification |
|---|---|
| 0 | succès complet, rien à signaler |
| 1 | rien n'a été écrit |
| 2 | aucun cours ne correspond à l'idSite demandé |
| 3 | session expirée en cours de route — relancez |
| 4 | terminé, mais avec des éléments manquants (voir `_rapport.html`) |
| 130 | interrompu |

---

## Trois choses à savoir avant de lancer

**L'authentification est redemandée à chaque lancement.** Aucun profil de
navigateur n'est conservé. C'est délibéré : un profil persistant s'est corrompu
à deux reprises en conditions réelles, et le coût d'une reconnexion est faible
puisqu'un seul lancement traite tous les cours.

**Connectez-vous dans la bonne fenêtre.** Le programme ouvre sa propre fenêtre
de navigateur, reconnaissable à son titre d'onglet. Se connecter dans votre
Chrome habituel ne sert à rien : le programme ne peut lire que les témoins de
la fenêtre qu'il a ouverte.

**Relancer ne recommence rien.** La reprise s'appuie sur le manifeste : un
fichier déjà présent sur le disque n'est jamais retéléchargé. Vous pouvez
interrompre et reprendre autant de fois que nécessaire.

## Si vous archivez vers OneDrive ou un dossier synchronisé

C'est nettement plus lent, pour trois raisons qui se cumulent : le filtre de
synchronisation intercepte chaque création de fichier, le motif d'écriture
atomique fait téléverser deux fois, et votre bande passante est partagée entre
le téléchargement depuis l'Université et le téléversement vers le nuage. Le
pire cas est la compression finale, qui relit toute l'arborescence : si des
fichiers ont été « déshydratés » en *disponibles en ligne seulement*, il faut
tous les retélécharger. **Préférez un dossier local.**

---

## Développement

```bash
pip install -e ".[dev]"
python -m pytest -q
```

`docs/api-monportail.md` est la carte technique de la plateforme : structures
d'URL, structures du DOM et pièges d'exploitation, tous relevés par inspection
directe. C'est le document à lire avant de toucher à `ena.py` ou à
`extraction.py`. Les identifiants qu'il cite sont des exemples, pas de vrais
cours.

Le projet n'a que trois dépendances : `playwright`, `beautifulsoup4` et
`pytest`. L'interface graphique utilise Tkinter, de la bibliothèque standard.

### Pourquoi une automatisation de navigateur, et pas des appels HTTP

`sitescours.monportail.ulaval.ca` est une application Oracle ADF. Hors d'un
vrai navigateur, toutes les URL renvoient la même page d'amorçage de 9 747
octets. Playwright n'est pas un choix de confort, c'est la seule voie.

### Le principe qui gouverne tout le code

**Une archive silencieusement incomplète est pire qu'un plantage.** La
plateforme ferme définitivement : un manque non signalé ne serait découvert
qu'une fois la source disparue. C'est pourquoi l'outil ne déclare jamais
« terminé » sur une archive incomplète, consigne chaque échec dans
`_rapport.html`, et recompte le disque face à son manifeste avant de conclure.

---

## Avertissement

Cet outil récupère **votre propre contenu**, celui auquel votre compte donne
déjà accès, et rien d'autre. Il ne contourne aucune restriction et n'écrit
jamais sur la plateforme : il ne fait que lire.

Le matériel de cours reste la propriété de ses auteurs. Archiver pour votre
usage personnel est une chose ; redistribuer en est une autre.

Projet indépendant, sans aucun lien avec l'Université Laval.

## Licence

MIT — voir [LICENSE](LICENSE).
