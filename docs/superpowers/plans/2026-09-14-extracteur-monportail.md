# Extracteur monPortail — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Archiver sur disque local tout le contenu accessible des anciens sites de cours monPortail (documents, dépôts, rétroactions, plans de cours, notes, pages) avant la fermeture de la plateforme le 1er novembre 2026.

**Architecture:** Playwright pilote un Chromium à profil persistant où l'utilisateur se connecte lui-même. La découverte se fait dans le navigateur, par URL canoniques d'abord puis lecture du menu réel ; le téléchargement se fait en HTTP direct avec les cookies de la session. L'extraction HTML est isolée dans des fonctions pures, testables sans navigateur. Une interface Tkinter écoute une file d'événements alimentée par un thread de travail.

**Tech Stack:** Python 3.12, Playwright (Chromium), BeautifulSoup 4, Tkinter (bibliothèque standard), pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-extracteur-monportail-design.md`
**Carte technique:** `docs/api-monportail.md`

## Global Constraints

- Python 3.12, Windows 11. Chemins écrits via le préfixe `\\?\` pour dépasser la limite de 260 caractères.
- Dépendances autorisées : `playwright`, `beautifulsoup4`, `pytest`. Rien d'autre sans justification.
- **Aucun test ne touche le réseau réel.** L'API exige un MFA interactif ; tout test qui tenterait une connexion est un test cassé.
- **Aucune écriture sur la plateforme.** L'outil ne suit jamais une commande ADF dont l'identifiant contient `cmd` (piège `cmdObtenirPlanCours`, qui publie une nouvelle version du plan de cours au lieu de le télécharger).
- Identifiants en français, conformes à la spec : `ena.py`, `extraction.py`, `stockage.py`, `archiveur.py`, `manifeste.py`, `nommage.py`, `telechargement.py`, `auth.py`, `ui.py`, `modele.py`.
- Segments de chemin tronqués à 80 caractères, extension préservée. Caractères interdits `< > : " / \ | ? *` remplacés par `-`.
- Sessions préfixées `AAAA-N` : 1 = Hiver, 2 = Été, 3 = Automne.
- Les CSV sont écrits en `utf-8-sig` pour qu'Excel les ouvre correctement avec les accents.
- Trois téléchargements simultanés au maximum.

## Structure de fichiers

| Fichier | Responsabilité |
|---|---|
| `extracteur/modele.py` | Dataclasses `Session`, `Cours`, `Module`, `Fichier`, `Evaluation`, `Note`. Aucune logique. |
| `extracteur/nommage.py` | Assainissement des noms de fichiers Windows. Fonctions pures. |
| `extracteur/stockage.py` | Écriture disque : `.part`, renommage atomique, SHA-256, détection de présence. |
| `extracteur/telechargement.py` | Politique de réessai, distinction des codes d'erreur, renouvellement de jeton. Transport injecté. |
| `extracteur/manifeste.py` | Lecture/écriture de `manifeste.csv`, des CSV de notes et du `_rapport.html`. |
| `extracteur/extraction.py` | HTML → objets du modèle. Fonctions pures, aucune dépendance navigateur. |
| `extracteur/auth.py` | Lancement de Chromium, attente de la connexion manuelle, fourniture du contexte de requête. |
| `extracteur/ena.py` | Navigation dans les sites de cours. Utilise `extraction` pour interpréter les pages. |
| `extracteur/archiveur.py` | Orchestration, arborescence, isolation par cours, émission d'événements. |
| `extracteur/ui.py` | Fenêtre Tkinter. Aucun appel réseau. |
| `extracteur/__main__.py` | Points d'entrée : fenêtre par défaut, `--un-seul-cours` en console. |

Écart assumé par rapport à la spec : `stockage.py` y couvrait aussi le nommage et les réessais. Ils sont séparés ici en `nommage.py` et `telechargement.py` parce que ce sont les deux modules les plus testables du projet et qu'ils méritent chacun leur cycle de test.

---

### Task 1: Fondations du projet et nommage sûr

C'est le module le plus piégeux du projet et le plus pur. Il se fait en TDD strict, et l'échafaudage du projet est plié dans cette tâche parce que c'est elle qui en a besoin la première.

**Files:**
- Create: `pyproject.toml`
- Create: `extracteur/__init__.py`
- Create: `extracteur/nommage.py`
- Test: `tests/test_nommage.py`

**Interfaces:**
- Consumes: rien.
- Produces: `nom_sur(nom: str) -> str`, `tronquer(nom: str, longueur_max: int = 80) -> str`, `nom_unique(dossier: Path, nom: str) -> str`, `chemin_long(chemin: Path) -> str`.

- [ ] **Step 1: Créer le squelette du projet**

`pyproject.toml` :

```toml
[project]
name = "extracteur-monportail"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "playwright>=1.47",
    "beautifulsoup4>=4.12",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

Créer `extracteur/__init__.py` vide et `tests/__init__.py` vide.

- [ ] **Step 2: Écrire les tests de nommage (ils doivent échouer)**

`tests/test_nommage.py` :

```python
from pathlib import Path

import pytest

from extracteur.nommage import chemin_long, nom_sur, nom_unique, tronquer


@pytest.mark.parametrize(
    "entree, attendu",
    [
        ('rapport<final>.pdf', 'rapport-final-.pdf'),
        ('note: importante.txt', 'note- importante.txt'),
        ('a/b\\c.txt', 'a-b-c.txt'),
        ('qui?.doc', 'qui-.doc'),
        ('100% * 2.xlsx', '100% - 2.xlsx'),
    ],
)
def test_caracteres_interdits_remplaces(entree, attendu):
    assert nom_sur(entree) == attendu


@pytest.mark.parametrize("reserve", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
def test_noms_reserves_prefixes(reserve):
    assert nom_sur(f"{reserve}.txt") == f"_{reserve}.txt"
    assert nom_sur(reserve) == f"_{reserve}"


def test_nom_reserve_insensible_a_la_casse():
    assert nom_sur("con.TXT") == "_con.TXT"


def test_points_et_espaces_de_fin_retires():
    assert nom_sur("dossier. ") == "dossier"
    assert nom_sur("fichier.txt.") == "fichier.txt"


def test_accents_conserves():
    assert nom_sur("Résumé été 2026.pdf") == "Résumé été 2026.pdf"


def test_nom_vide_remplace():
    assert nom_sur("") == "_"
    assert nom_sur("   ") == "_"


def test_troncature_preserve_extension():
    long_nom = "a" * 200 + ".pptx"
    resultat = tronquer(long_nom, 80)
    assert len(resultat) == 80
    assert resultat.endswith(".pptx")


def test_troncature_laisse_les_noms_courts_intacts():
    assert tronquer("court.pdf", 80) == "court.pdf"


def test_troncature_extension_demesuree():
    # Une "extension" de plus de 80 caractères n'en est pas une : on tronque brutalement.
    resultat = tronquer("fichier." + "z" * 100, 80)
    assert len(resultat) == 80


def test_nom_unique_suffixe_les_collisions(tmp_path):
    (tmp_path / "note.pdf").write_text("x")
    assert nom_unique(tmp_path, "note.pdf") == "note (2).pdf"

    (tmp_path / "note (2).pdf").write_text("x")
    assert nom_unique(tmp_path, "note.pdf") == "note (3).pdf"


def test_nom_unique_laisse_passer_si_libre(tmp_path):
    assert nom_unique(tmp_path, "libre.pdf") == "libre.pdf"


def test_chemin_long_prefixe(tmp_path):
    resultat = chemin_long(tmp_path / "a.txt")
    assert resultat.startswith("\\\\?\\")
    assert resultat.endswith("a.txt")


def test_chemin_long_ne_double_pas_le_prefixe():
    deja = Path("\\\\?\\C:\\temp\\a.txt")
    assert chemin_long(deja).count("\\\\?\\") == 1
```

- [ ] **Step 3: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_nommage.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.nommage'`

- [ ] **Step 4: Écrire l'implémentation minimale**

`extracteur/nommage.py` :

```python
"""Assainissement des noms de fichiers pour Windows.

Windows refuse certains caracteres, reserve certains noms et limite les chemins
a 260 caracteres. Ce module traite ces trois pieges avant toute ecriture disque.
"""

import re
from pathlib import Path

CARACTERES_INTERDITS = r'<>:"/\|?*'

NOMS_RESERVES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

LONGUEUR_MAX_SEGMENT = 80
PREFIXE_CHEMIN_LONG = "\\\\?\\"


def nom_sur(nom: str) -> str:
    """Rend un nom de fichier ou de dossier acceptable par Windows."""
    assaini = "".join("-" if c in CARACTERES_INTERDITS else c for c in nom)

    # Les caracteres de controle passent parfois dans les titres extraits du HTML.
    assaini = re.sub(r"[\x00-\x1f]", "", assaini)

    # Windows supprime silencieusement les points et espaces de fin : on le fait
    # nous-memes pour que le nom sur disque soit celui qu'on croit avoir ecrit.
    assaini = assaini.rstrip(". ")

    if not assaini.strip():
        return "_"

    racine = assaini.split(".")[0]
    if racine.upper() in NOMS_RESERVES:
        assaini = f"_{assaini}"

    return assaini


def tronquer(nom: str, longueur_max: int = LONGUEUR_MAX_SEGMENT) -> str:
    """Tronque un nom en preservant son extension."""
    if len(nom) <= longueur_max:
        return nom

    chemin = Path(nom)
    extension = chemin.suffix

    # Une extension plus longue que la limite n'en est pas une.
    if len(extension) >= longueur_max:
        return nom[:longueur_max]

    return chemin.stem[: longueur_max - len(extension)] + extension


def nom_unique(dossier: Path, nom: str) -> str:
    """Suffixe le nom tant qu'un fichier du meme nom existe deja."""
    if not (dossier / nom).exists():
        return nom

    chemin = Path(nom)
    compteur = 2
    while True:
        candidat = f"{chemin.stem} ({compteur}){chemin.suffix}"
        if not (dossier / candidat).exists():
            return candidat
        compteur += 1


def chemin_long(chemin: Path) -> str:
    """Rend le chemin utilisable au-dela de la limite de 260 caracteres."""
    texte = str(chemin)
    if texte.startswith(PREFIXE_CHEMIN_LONG):
        return texte
    return PREFIXE_CHEMIN_LONG + str(chemin.resolve())
```

- [ ] **Step 5: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_nommage.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml extracteur/ tests/
git commit -m "feat: nommage sur pour Windows + squelette du projet"
```

---

### Task 2: Modèle de données

Un contrat sans logique, mais toutes les tâches suivantes s'y réfèrent. Les tests vérifient uniquement les quelques comportements dérivés.

**Files:**
- Create: `extracteur/modele.py`
- Test: `tests/test_modele.py`

**Interfaces:**
- Consumes: rien.
- Produces: `Session`, `Cours`, `Module`, `Fichier`, `Evaluation`, `Note`, toutes des dataclasses gelées. `Session.dossier` et `Cours.dossier` produisent les noms de dossiers de l'arborescence.

- [ ] **Step 1: Écrire les tests**

`tests/test_modele.py` :

```python
from extracteur.modele import Cours, Fichier, Session


def test_dossier_de_session_trie_chronologiquement():
    assert Session(code="202601", libelle="Hiver 2026").dossier() == "2026-1 Hiver"
    assert Session(code="202605", libelle="Été 2026").dossier() == "2026-2 Été"
    assert Session(code="202609", libelle="Automne 2026").dossier() == "2026-3 Automne"


def test_dossier_de_session_inconnue_reste_lisible():
    assert Session(code="202699", libelle="Intensif").dossier() == "2026-9 Intensif"


def test_dossier_de_cours_combine_sigle_et_titre():
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique et professionnalisme",
        session=Session(code="202601", libelle="Hiver 2026"),
    )
    assert cours.dossier() == "ABC-1000 Éthique et professionnalisme"


def test_dossier_de_cours_sans_sigle():
    cours = Cours(
        id_site="100006",
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_modele.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.modele'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/modele.py` :

```python
"""Objets echanges entre l'extraction, la navigation et l'archiveur."""

from dataclasses import dataclass, field

# Les codes de session de l'Universite Laval se terminent par le mois de debut.
RANG_SESSION = {"01": ("1", "Hiver"), "05": ("2", "Été"), "09": ("3", "Automne")}


@dataclass(frozen=True)
class Session:
    code: str  # AAAASS, par exemple "202601"
    libelle: str  # "Hiver 2026"

    def dossier(self) -> str:
        annee, suffixe = self.code[:4], self.code[4:]
        rang, _ = RANG_SESSION.get(suffixe, (suffixe.lstrip("0") or "9", ""))
        nom = self.libelle.split()[0] if self.libelle else ""
        return f"{annee}-{rang} {nom}".strip()


@dataclass(frozen=True)
class Cours:
    id_site: str
    sigle: str | None
    titre: str
    session: Session

    def dossier(self) -> str:
        return f"{self.sigle} {self.titre}".strip() if self.sigle else self.titre


@dataclass(frozen=True)
class Module:
    id_site: str
    id_module: str
    titre: str
    rang: int = 0


@dataclass(frozen=True)
class Fichier:
    nom: str
    url: str

    def est_interne(self) -> bool:
        """Vrai si la ressource est hebergee par monPortail et doit etre telechargee."""
        return "/contenu/sitescours/" in self.url


@dataclass(frozen=True)
class Evaluation:
    id_site: str
    id_evaluation: str
    titre: str


@dataclass(frozen=True)
class Note:
    evaluation: str
    note: str = ""
    sur: str = ""
    ponderation: str = ""
    moyenne_groupe: str = ""


@dataclass
class Echec:
    cours: str
    element: str
    cause: str
    url: str = ""


@dataclass
class Resultat:
    fichiers_ecrits: int = 0
    fichiers_sautes: int = 0
    echecs: list[Echec] = field(default_factory=list)
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_modele.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/modele.py tests/test_modele.py
git commit -m "feat: modele de donnees de l'archive"
```

---

### Task 3: Stockage, empreinte et reprise idempotente

La promesse « relancer, c'est reprendre » se joue entièrement ici.

**Files:**
- Create: `extracteur/stockage.py`
- Test: `tests/test_stockage.py`

**Interfaces:**
- Consumes: `nommage.chemin_long`.
- Produces: `deja_present(destination: Path, taille_attendue: int | None) -> bool`, `ecrire_flux(destination: Path, morceaux: Iterable[bytes]) -> tuple[int, str]` qui retourne `(taille, sha256)`.

- [ ] **Step 1: Écrire les tests**

`tests/test_stockage.py` :

```python
import hashlib

import pytest

from extracteur.stockage import deja_present, ecrire_flux


def test_ecrire_flux_retourne_taille_et_empreinte(tmp_path):
    destination = tmp_path / "a.bin"
    taille, empreinte = ecrire_flux(destination, [b"abc", b"def"])

    assert destination.read_bytes() == b"abcdef"
    assert taille == 6
    assert empreinte == hashlib.sha256(b"abcdef").hexdigest()


def test_aucun_fichier_part_ne_survit(tmp_path):
    destination = tmp_path / "a.bin"
    ecrire_flux(destination, [b"abc"])
    assert list(tmp_path.glob("*.part")) == []


def test_le_part_est_nettoye_si_le_flux_echoue(tmp_path):
    destination = tmp_path / "a.bin"

    def flux_qui_casse():
        yield b"abc"
        raise ConnectionError("coupure")

    with pytest.raises(ConnectionError):
        ecrire_flux(destination, flux_qui_casse())

    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_deja_present_faux_si_absent(tmp_path):
    assert deja_present(tmp_path / "absent.pdf", 10) is False


def test_deja_present_vrai_si_taille_correspond(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"0123456789")
    assert deja_present(fichier, 10) is True


def test_deja_present_faux_si_taille_differente(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"012")
    assert deja_present(fichier, 10) is False


def test_deja_present_vrai_si_taille_inconnue(tmp_path):
    # Le serveur ne donne pas toujours Content-Length : un fichier non vide suffit.
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"012")
    assert deja_present(fichier, None) is True


def test_deja_present_faux_si_fichier_vide(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"")
    assert deja_present(fichier, None) is False


def test_les_dossiers_parents_sont_crees(tmp_path):
    destination = tmp_path / "x" / "y" / "a.bin"
    ecrire_flux(destination, [b"abc"])
    assert destination.exists()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_stockage.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.stockage'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/stockage.py` :

```python
"""Ecriture disque : flux, empreinte, et reprise par simple presence du fichier."""

import hashlib
from collections.abc import Iterable
from pathlib import Path

from extracteur.nommage import chemin_long


def deja_present(destination: Path, taille_attendue: int | None) -> bool:
    """Vrai si le fichier est deja la et complet.

    Un fichier portant son nom final est forcement complet : l'ecriture passe
    par un .part renomme seulement a la fin.
    """
    if not destination.exists():
        return False

    taille_reelle = destination.stat().st_size
    if taille_attendue is None:
        return taille_reelle > 0
    return taille_reelle == taille_attendue


def ecrire_flux(destination: Path, morceaux: Iterable[bytes]) -> tuple[int, str]:
    """Ecrit un flux d'octets et retourne (taille, sha256).

    Rien n'est charge en memoire : une capsule video de 800 Mo ne doit pas faire
    gonfler le processus.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    partiel = destination.with_suffix(destination.suffix + ".part")

    empreinte = hashlib.sha256()
    taille = 0

    try:
        with open(chemin_long(partiel), "wb") as sortie:
            for morceau in morceaux:
                sortie.write(morceau)
                empreinte.update(morceau)
                taille += len(morceau)
    except BaseException:
        partiel.unlink(missing_ok=True)
        raise

    # Renommage atomique : c'est ce qui rend la reprise fiable.
    partiel.replace(destination)
    return taille, empreinte.hexdigest()
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_stockage.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/stockage.py tests/test_stockage.py
git commit -m "feat: ecriture en flux avec .part et reprise idempotente"
```

---

### Task 4: Téléchargement et politique de réessai

Le point le plus important : un jeton expiré doit mettre la file en pause, pas brûler les trente cours restants.

**Files:**
- Create: `extracteur/telechargement.py`
- Test: `tests/test_telechargement.py`

**Interfaces:**
- Consumes: `stockage.ecrire_flux`.
- Produces: `telecharger(transport, url, destination, renouveler=None, dormir=time.sleep) -> tuple[int, str]`, exceptions `ErreurPermanente`, `SessionExpiree`. Le `transport` est un appelable `(url) -> Reponse` où `Reponse` expose `.statut: int` et `.morceaux() -> Iterable[bytes]`.

- [ ] **Step 1: Écrire les tests**

`tests/test_telechargement.py` :

```python
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_telechargement.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.telechargement'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/telechargement.py` :

```python
"""Politique de reessai et distinction des causes d'echec.

La regle qui compte : un jeton expire ne doit jamais consommer les cours
restants en erreurs. Il remonte en SessionExpiree pour que l'archiveur mette la
file en pause et demande une reconnexion.
"""

import time
from pathlib import Path

from extracteur.stockage import ecrire_flux

PAUSES = (2, 8, 30)
STATUTS_SANS_REESSAI = (403, 404)
ERREURS_RESEAU = (ConnectionError, TimeoutError, OSError)


class ErreurPermanente(Exception):
    """Contenu inaccessible ou disparu : on consigne et on avance."""


class SessionExpiree(Exception):
    """La session d'authentification est morte : il faut se reconnecter."""


def telecharger(transport, url, destination: Path, renouveler=None, dormir=time.sleep):
    """Telecharge une ressource vers destination, avec reessais differencies."""
    jeton_renouvele = False

    for tentative in range(3):
        try:
            reponse = transport(url)
        except ERREURS_RESEAU as erreur:
            if tentative == 2:
                raise ErreurPermanente(f"reseau : {erreur}") from erreur
            dormir(PAUSES[tentative])
            continue

        if reponse.statut == 200:
            return ecrire_flux(Path(destination), reponse.morceaux())

        if reponse.statut == 401:
            # Une seule chance : on rafraichit le jeton et on rejoue.
            if renouveler is None or jeton_renouvele:
                raise SessionExpiree(url)
            renouveler()
            jeton_renouvele = True
            continue

        if reponse.statut in STATUTS_SANS_REESSAI:
            raise ErreurPermanente(f"HTTP {reponse.statut}")

        if tentative == 2:
            raise ErreurPermanente(f"HTTP {reponse.statut}")
        dormir(PAUSES[tentative])

    raise ErreurPermanente("tentatives epuisees")
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_telechargement.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/telechargement.py tests/test_telechargement.py
git commit -m "feat: politique de reessai et pause sur session expiree"
```

---

### Task 5: Extraction HTML

Fonctions pures : on leur donne du HTML, elles rendent des objets. Aucun navigateur. C'est ici que se teste la tolérance à la disparité des sites.

**Files:**
- Create: `extracteur/extraction.py`
- Test: `tests/test_extraction.py`

**Interfaces:**
- Consumes: `modele.Module`, `modele.Fichier`, `modele.Note`.
- Produces: `url_reelle(href: str) -> str | None`, `nom_depuis_url(url: str) -> str`, `modules_depuis_html(html: str, id_site: str) -> list[Module]`, `fichiers_depuis_html(html: str) -> list[Fichier]`, `resultats_depuis_html(html: str) -> list[Note]`, `sections_du_menu(html: str) -> list[str]`, `est_commande_adf(identifiant: str | None) -> bool`.

- [ ] **Step 1: Écrire les tests**

`tests/test_extraction.py` :

```python
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
    "/analytique/evenement/fichier?idFichier=140274665&idSite=100001"
    "&url=%2Fcontenu%2Fsitescours%2F040%2F04000%2F202601%2Fsite100001"
    "%2Fmodules1434431%2Fmodule1795743%2Fpage4874493%2Fbloccontenu5204221"
    "%2FCours_1_-_Introduction-janvier%25202026.pptx"
    "%3Fidentifiant%3D0a981dbdc4212d59737bc2e400fd0d39076480bc"
)


def test_url_reelle_decode_le_parametre_url():
    assert url_reelle(LIEN_TRACEUR) == (
        "/contenu/sitescours/040/04000/202601/site100001/modules1434431"
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
      <tr><td><a href="/ena/site/module?idSite=100001&idModule=1795743&editionModule=false">1. Introduction</a></td></tr>
      <tr><td><a href="/ena/site/module?idSite=100001&idModule=1795744&editionModule=false">2. Vocabulaire</a></td></tr>
    </table>
    """
    modules = modules_depuis_html(html, "100001")
    assert [m.id_module for m in modules] == ["1795743", "1795744"]
    assert modules[0].titre == "1. Introduction"
    assert modules[0].rang == 0
    assert modules[1].rang == 1


def test_modules_ignore_les_liens_sans_id_module():
    html = '<a href="/ena/site/accueil?idSite=100001">Accueil</a>'
    assert modules_depuis_html(html, "100001") == []


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
    # ABC-1000 dit "Feuille de route", DEF-2000 dit "Contenu et activités".
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_extraction.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.extraction'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/extraction.py` :

```python
"""HTML vers objets du modele. Aucune dependance navigateur, donc testable.

Les liens de fichiers de monPortail passent tous par un traceur d'analytique
dont le parametre `url` contient, en double encodage, l'URL reelle. On la decode
pour telecharger a la source : cela evite d'alimenter les statistiques de
consultation et donne le vrai nom de fichier, que le texte du lien tronque.
"""

from urllib.parse import parse_qs, unquote, urlsplit

from bs4 import BeautifulSoup

from extracteur.modele import Fichier, Module, Note

PREFIXE_TRACEUR = "/analytique/evenement/fichier"
PREFIXE_CONTENU = "/contenu/sitescours/"


def _soupe(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def url_reelle(href: str) -> str | None:
    """Extrait l'URL de contenu d'un href, ou None si ce n'est pas une ressource interne."""
    if not href or href.startswith("#"):
        return None

    if href.startswith(PREFIXE_TRACEUR):
        parametres = parse_qs(urlsplit(href).query)
        valeurs = parametres.get("url")
        if not valeurs:
            return None
        # Un seul unquote : le second niveau d'encodage est celui de l'URL elle-meme.
        return unquote(valeurs[0])

    if PREFIXE_CONTENU in href:
        return href

    return None


def nom_depuis_url(url: str) -> str:
    """Nom de fichier lisible, tire du chemin et non du texte du lien."""
    chemin = urlsplit(url).path
    return unquote(chemin.rsplit("/", 1)[-1])


def est_commande_adf(identifiant: str | None) -> bool:
    """Vrai si l'element est une commande ADF, donc potentiellement une ecriture.

    Le menu Plan de cours contient `cmdObtenirPlanCours`, qui publie une nouvelle
    version au lieu de telecharger. On ne suit jamais ces elements.
    """
    return bool(identifiant) and "cmd" in identifiant


def modules_depuis_html(html: str, id_site: str) -> list[Module]:
    modules: dict[str, Module] = {}

    for lien in _soupe(html).find_all("a", href=True):
        href = lien["href"]
        if "idModule=" not in href:
            continue

        parametres = parse_qs(urlsplit(href).query)
        id_module = parametres.get("idModule", [""])[0]
        if not id_module:
            continue

        titre = lien.get_text(strip=True)
        existant = modules.get(id_module)
        # Un module apparait souvent deux fois : une icone sans texte, puis le titre.
        if existant is None:
            modules[id_module] = Module(
                id_site=id_site, id_module=id_module, titre=titre, rang=len(modules)
            )
        elif titre and not existant.titre:
            modules[id_module] = Module(
                id_site=id_site,
                id_module=id_module,
                titre=titre,
                rang=existant.rang,
            )

    return list(modules.values())


def fichiers_depuis_html(html: str) -> list[Fichier]:
    fichiers: dict[str, Fichier] = {}

    for lien in _soupe(html).find_all("a", href=True):
        if est_commande_adf(lien.get("id")):
            continue

        url = url_reelle(lien["href"])
        if url is None:
            continue

        if url not in fichiers:
            fichiers[url] = Fichier(nom=nom_depuis_url(url), url=url)

    return list(fichiers.values())


def _scinder_note(texte: str) -> tuple[str, str]:
    """'18 / 20' devient ('18', '20'). Un texte libre reste entier."""
    if "/" in texte:
        gauche, droite = texte.split("/", 1)
        return gauche.strip(), droite.strip()
    return texte.strip(), ""


def resultats_depuis_html(html: str) -> list[Note]:
    notes: list[Note] = []

    for tableau in _soupe(html).find_all("table"):
        for ligne in tableau.find_all("tr"):
            cellules = [c.get_text(strip=True) for c in ligne.find_all("td")]
            if len(cellules) < 2:
                continue

            note, sur = _scinder_note(cellules[1])
            notes.append(
                Note(
                    evaluation=cellules[0],
                    note=note,
                    sur=sur,
                    ponderation=cellules[2] if len(cellules) > 2 else "",
                )
            )

    return notes


# Le menu global de monPortail est present sur toutes les pages de site de
# cours. Ces libelles n'appartiennent pas au site et ne doivent jamais etre
# parcourus. Releves en phase 0 sur trois sites.
LIBELLES_HORS_SITE = frozenset(
    {
        "menu", "Profil", "Tableau de bord", "Études", "Admission",
        "Inscription aux cours", "Accommodement", "Cours", "Sites de cours Brio",
        "Cheminement", "Appréciation de l'enseignement", "Relevé de notes",
        "Collation des grades", "Documents officiels", "Carte d'identité",
        "Attestation d'inscription", "Documents légaux en renouvellement",
        "Emplois et stages", "Profil professionnel", "Grille de stages",
        "Services", "Application monPortail", "Impression",
        "Laissez-passer universitaire", "Séjour mobilité", "Finances",
        "Bourses Perspective Québec", "Droits de scolarité",
        "Passer au contenu", "Liste des cours", "Envoi de courriel",
        "Conditions d'utilisation", "Confidentialité", "Accessibilité",
        "Nouveautés", "Contactez-nous",
    }
)

LONGUEUR_MAX_LIBELLE = 60


def sections_du_menu(html: str) -> list[str]:
    """Libelles propres au site, pour parcourir les sections hors schema canonique.

    Le repli par le menu est indispensable : certains sites n'ont ni modules ni
    evaluations, et leurs sections ne sont atteignables que par leur libelle.
    """
    libelles: list[str] = []
    for lien in _soupe(html).find_all("a"):
        texte = lien.get_text(strip=True)
        if not texte or len(texte) >= LONGUEUR_MAX_LIBELLE:
            continue
        if texte in LIBELLES_HORS_SITE or texte in libelles:
            continue
        libelles.append(texte)
    return libelles
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_extraction.py -v`
Expected: PASS, 17 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/extraction.py tests/test_extraction.py
git commit -m "feat: extraction HTML des modules, fichiers et resultats"
```

---

### Task 6: Manifeste, CSV de notes et rapport d'échecs

Le manifeste sert la reprise ; le rapport dit ce que l'archive n'a **pas**. C'est le livrable le plus important après l'archive elle-même.

**Files:**
- Create: `extracteur/manifeste.py`
- Test: `tests/test_manifeste.py`

**Interfaces:**
- Consumes: `modele.Note`, `modele.Echec`.
- Produces: `Manifeste(racine: Path)` avec `.charger() -> dict[str, dict]`, `.ajouter(chemin_relatif, taille, sha256, url, statut)`, `.deja_archive(chemin_relatif) -> bool` ; `ecrire_notes(destination, notes)` ; `ecrire_rapport(destination, echecs, resume)`.

- [ ] **Step 1: Écrire les tests**

`tests/test_manifeste.py` :

```python
from extracteur.manifeste import Manifeste, ecrire_notes, ecrire_rapport
from extracteur.modele import Echec, Note


def test_manifeste_vide_au_depart(tmp_path):
    assert Manifeste(tmp_path).charger() == {}


def test_ajout_puis_relecture(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("2026-1 Hiver/PHI/a.pdf", 120, "abc", "/contenu/a.pdf", "ok")

    relu = Manifeste(tmp_path).charger()
    assert relu["2026-1 Hiver/PHI/a.pdf"]["sha256"] == "abc"
    assert relu["2026-1 Hiver/PHI/a.pdf"]["taille"] == "120"


def test_deja_archive(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("a.pdf", 1, "x", "/contenu/a.pdf", "ok")

    suivant = Manifeste(tmp_path)
    assert suivant.deja_archive("a.pdf") is True
    assert suivant.deja_archive("b.pdf") is False


def test_entete_ecrit_une_seule_fois(tmp_path):
    manifeste = Manifeste(tmp_path)
    manifeste.ajouter("a.pdf", 1, "x", "/u", "ok")
    manifeste.ajouter("b.pdf", 2, "y", "/u", "ok")

    lignes = (tmp_path / "manifeste.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lignes) == 3
    assert lignes[0].startswith("chemin")


def test_ecrire_notes(tmp_path):
    destination = tmp_path / "notes.csv"
    ecrire_notes(destination, [Note(evaluation="Examen 1", note="18", sur="20")])

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "Examen 1" in contenu
    assert "18" in contenu


def test_ecrire_notes_avec_accents(tmp_path):
    destination = tmp_path / "notes.csv"
    ecrire_notes(destination, [Note(evaluation="Résumé de l'été", note="9")])
    assert "Résumé de l'été" in destination.read_text(encoding="utf-8-sig")


def test_notes_consolidees_portent_session_et_cours(tmp_path):
    from extracteur.manifeste import ecrire_notes_consolidees

    destination = tmp_path / "notes-tous-cours.csv"
    ecrire_notes_consolidees(
        destination,
        [
            ("2026-1 Hiver", "ABC-1000 Éthique", Note(evaluation="Examen 1", note="18", sur="20")),
            ("2025-3 Automne", "DEF-2000 Projet", Note(evaluation="Rapport", note="45", sur="50")),
        ],
    )

    contenu = destination.read_text(encoding="utf-8-sig")
    assert "2026-1 Hiver" in contenu
    assert "ABC-1000 Éthique" in contenu
    assert "Rapport" in contenu


def test_notes_consolidees_vides_ecrivent_l_entete(tmp_path):
    from extracteur.manifeste import ecrire_notes_consolidees

    destination = tmp_path / "notes-tous-cours.csv"
    ecrire_notes_consolidees(destination, [])
    assert destination.read_text(encoding="utf-8-sig").startswith("Session")


def test_rapport_liste_les_echecs(tmp_path):
    destination = tmp_path / "_rapport.html"
    ecrire_rapport(
        destination,
        [Echec(cours="ABC-1000", element="a.pdf", cause="HTTP 403", url="/contenu/a.pdf")],
        {"fichiers": 12, "cours": 3},
    )

    contenu = destination.read_text(encoding="utf-8")
    assert "ABC-1000" in contenu
    assert "HTTP 403" in contenu
    assert "/contenu/a.pdf" in contenu


def test_rapport_sans_echec_le_dit(tmp_path):
    destination = tmp_path / "_rapport.html"
    ecrire_rapport(destination, [], {"fichiers": 12, "cours": 3})
    assert "Aucun échec" in destination.read_text(encoding="utf-8")


def test_rapport_echappe_le_html():
    # Un titre de document peut contenir des chevrons.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as dossier:
        destination = Path(dossier) / "r.html"
        ecrire_rapport(destination, [Echec("C", "<script>x</script>", "erreur")], {})
        assert "<script>" not in destination.read_text(encoding="utf-8")
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_manifeste.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.manifeste'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/manifeste.py` :

```python
"""Manifeste de l'archive, export des notes et rapport d'echecs."""

import csv
from datetime import datetime
from html import escape
from pathlib import Path

COLONNES = ["chemin", "taille", "sha256", "url", "horodatage", "statut"]
ENCODAGE_CSV = "utf-8-sig"  # Excel lit mal l'UTF-8 sans BOM.


class Manifeste:
    """Une ligne par fichier archive. Sert a la verification et a la reprise."""

    def __init__(self, racine: Path):
        self.chemin = Path(racine) / "manifeste.csv"
        self._entrees: dict[str, dict] | None = None

    def charger(self) -> dict[str, dict]:
        if self._entrees is not None:
            return self._entrees

        self._entrees = {}
        if self.chemin.exists():
            with open(self.chemin, encoding=ENCODAGE_CSV, newline="") as source:
                for ligne in csv.DictReader(source):
                    self._entrees[ligne["chemin"]] = ligne
        return self._entrees

    def deja_archive(self, chemin_relatif: str) -> bool:
        return chemin_relatif in self.charger()

    def ajouter(self, chemin_relatif, taille, sha256, url, statut="ok") -> None:
        entrees = self.charger()
        nouveau = not self.chemin.exists()

        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        with open(self.chemin, "a", encoding=ENCODAGE_CSV, newline="") as sortie:
            redacteur = csv.DictWriter(sortie, fieldnames=COLONNES)
            if nouveau:
                redacteur.writeheader()
            ligne = {
                "chemin": chemin_relatif,
                "taille": str(taille),
                "sha256": sha256,
                "url": url,
                "horodatage": datetime.now().isoformat(timespec="seconds"),
                "statut": statut,
            }
            redacteur.writerow(ligne)

        entrees[chemin_relatif] = ligne


def ecrire_notes(destination: Path, notes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding=ENCODAGE_CSV, newline="") as sortie:
        redacteur = csv.writer(sortie)
        redacteur.writerow(["Évaluation", "Note", "Sur", "Pondération", "Moyenne groupe"])
        for note in notes:
            redacteur.writerow(
                [note.evaluation, note.note, note.sur, note.ponderation, note.moyenne_groupe]
            )


def ecrire_notes_consolidees(destination: Path, lignes) -> None:
    """Toutes les notes de tous les cours dans un seul CSV, a la racine.

    `lignes` est une suite de tuples (dossier_session, dossier_cours, Note).
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding=ENCODAGE_CSV, newline="") as sortie:
        redacteur = csv.writer(sortie)
        redacteur.writerow(
            ["Session", "Cours", "Évaluation", "Note", "Sur", "Pondération", "Moyenne groupe"]
        )
        for session, cours, note in lignes:
            redacteur.writerow(
                [
                    session,
                    cours,
                    note.evaluation,
                    note.note,
                    note.sur,
                    note.ponderation,
                    note.moyenne_groupe,
                ]
            )


def ecrire_rapport(destination: Path, echecs, resume: dict) -> None:
    """Le document qui dit ce qu'il reste a recuperer a la main."""
    lignes = []
    for echec in echecs:
        lien = (
            f'<a href="{escape(echec.url)}">{escape(echec.url)}</a>' if echec.url else ""
        )
        lignes.append(
            "<tr>"
            f"<td>{escape(echec.cours)}</td>"
            f"<td>{escape(echec.element)}</td>"
            f"<td>{escape(echec.cause)}</td>"
            f"<td>{lien}</td>"
            "</tr>"
        )

    corps = (
        "<p><strong>Aucun échec.</strong> Tout le contenu visé a été récupéré.</p>"
        if not echecs
        else "<table><tr><th>Cours</th><th>Élément</th><th>Cause</th><th>URL</th></tr>"
        + "".join(lignes)
        + "</table>"
    )

    resume_html = "".join(f"<li>{escape(str(k))} : {escape(str(v))}</li>" for k, v in resume.items())

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<title>Rapport d'archivage monPortail</title>"
        "<style>body{font-family:system-ui;margin:2rem;max-width:60rem}"
        "table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ccc;padding:.4rem;text-align:left;font-size:.9rem}"
        "th{background:#f0f0f0}</style></head><body>"
        "<h1>Rapport d'archivage monPortail</h1>"
        f"<ul>{resume_html}</ul>"
        f"<h2>Éléments non récupérés</h2>{corps}"
        "</body></html>",
        encoding="utf-8",
    )
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_manifeste.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/manifeste.py tests/test_manifeste.py
git commit -m "feat: manifeste, export des notes et rapport d'echecs"
```

---

### Task 7: Authentification par navigateur piloté

Pas de test automatisé possible — le MFA est interactif. La vérification est manuelle et fait partie de la tâche.

**Files:**
- Create: `extracteur/auth.py`
- Create: `verifier_connexion.py` (script de vérification manuelle, à la racine)

**Interfaces:**
- Consumes: rien.
- Produits: `SessionNavigateur(dossier_profil: Path)` avec `.ouvrir()`, `.attendre_connexion(delai=300) -> bool`, `.page`, `.transport(url) -> Reponse` (compatible avec `telechargement.telecharger`), `.fermer()`.

- [ ] **Step 1: Installer Playwright**

```bash
python -m pip install playwright beautifulsoup4 pytest
python -m playwright install chromium
```

- [ ] **Step 2: Écrire le module d'authentification**

`extracteur/auth.py` :

```python
"""Ouverture du navigateur et attente de la connexion manuelle.

L'utilisateur saisit lui-meme son mot de passe et son MFA. Le programme ne lit
jamais ses identifiants : il attend seulement de voir une page authentifiee.

Sur le domaine des sites de cours, les cookies suffisent : aucun jeton porteur
n'est necessaire pour telecharger les fichiers.
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL_DEPART = "https://sitescours.monportail.ulaval.ca/ena/site/accueil"
HOTE_SITESCOURS = "sitescours.monportail.ulaval.ca"


class Reponse:
    """Adaptateur vers l'interface attendue par telechargement.telecharger."""

    def __init__(self, reponse_playwright):
        self.statut = reponse_playwright.status
        self._reponse = reponse_playwright

    def morceaux(self):
        yield self._reponse.body()


class SessionNavigateur:
    def __init__(self, dossier_profil: Path, sans_fenetre: bool = False):
        self.dossier_profil = Path(dossier_profil)
        self.sans_fenetre = sans_fenetre
        self._playwright = None
        self.contexte = None
        self.page = None

    def ouvrir(self) -> None:
        self.dossier_profil.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        # Profil persistant : les lancements suivants ne redemandent pas la
        # connexion tant que la session vit.
        self.contexte = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.dossier_profil),
            headless=self.sans_fenetre,
            accept_downloads=True,
        )
        self.page = self.contexte.pages[0] if self.contexte.pages else self.contexte.new_page()
        self.page.goto(URL_DEPART, wait_until="domcontentloaded")

    def est_connecte(self) -> bool:
        if self.page is None:
            return False
        return HOTE_SITESCOURS in self.page.url

    def attendre_connexion(self, delai: int = 300) -> bool:
        """Attend que l'utilisateur ait termine sa connexion, MFA compris."""
        limite = time.time() + delai
        while time.time() < limite:
            if self.est_connecte():
                return True
            time.sleep(2)
        return False

    def transport(self, url: str) -> Reponse:
        """GET partageant les cookies du navigateur."""
        if not url.startswith("http"):
            url = f"https://{HOTE_SITESCOURS}{url}"
        return Reponse(self.contexte.request.get(url))

    def fermer(self) -> None:
        if self.contexte is not None:
            self.contexte.close()
        if self._playwright is not None:
            self._playwright.stop()
```

- [ ] **Step 3: Écrire le script de vérification manuelle**

`verifier_connexion.py` :

```python
"""Verification manuelle de l'authentification.

Usage : python verifier_connexion.py
Se connecter dans la fenetre qui s'ouvre, puis lire le resultat en console.
"""

from pathlib import Path

from extracteur.auth import SessionNavigateur

session = SessionNavigateur(Path(".session"))
session.ouvrir()

print("Connectez-vous dans la fenetre, puis patientez...")
if not session.attendre_connexion():
    print("ECHEC : connexion non detectee dans le delai imparti.")
    session.fermer()
    raise SystemExit(1)

print("Connexion detectee.")
reponse = session.transport("/ena/site/accueil?idSite=100001")
print(f"GET /ena/site/accueil -> HTTP {reponse.statut}")
print("OK" if reponse.statut == 200 else "ECHEC")
session.fermer()
```

- [ ] **Step 4: Exécuter la vérification manuelle**

Run: `python verifier_connexion.py`
Se connecter dans la fenêtre.
Expected: `Connexion detectee.` puis `GET /ena/site/accueil -> HTTP 200` et `OK`.

Si la fenêtre affiche un sélecteur de compte Microsoft, choisir le compte ULaval. Le profil étant persistant, cet écran ne devrait apparaître qu'au premier lancement.

- [ ] **Step 5: Commit**

```bash
git add extracteur/auth.py verifier_connexion.py
git commit -m "feat: authentification par navigateur a profil persistant"
```

---

### Task 8: Navigation dans les sites de cours

URL canoniques d'abord, menu réel ensuite. Aucune présomption de structure : la phase 0 a montré que trois sites testés avaient trois menus différents.

**Files:**
- Create: `extracteur/ena.py`
- Test: `tests/test_ena.py`

**Interfaces:**
- Consumes: `auth.SessionNavigateur`, `extraction.*`, `modele.*`.
- Produces: `Ena(session)` avec `.sessions_disponibles() -> list[Session]`, `.sites_de_session(session) -> list[Cours]`, `.modules(cours) -> list[Module]`, `.fichiers_du_module(module) -> list[Fichier]`, `.evaluations(cours) -> list[Evaluation]`, `.fichiers_de_depot(evaluation) -> list[Fichier]`, `.resultats(cours) -> list[Note]`, `.capturer_pdf(url, destination)`.

- [ ] **Step 1: Écrire les tests avec une page factice**

`tests/test_ena.py` :

```python
from extracteur.ena import URL, Ena
from extracteur.modele import Cours, Evaluation, Module, Session


class PageFactice:
    """Enregistre les URL visitees et rend le HTML programme."""

    def __init__(self, pages):
        self.pages = pages
        self.visitees = []
        self.url = ""

    def goto(self, url, **_):
        self.visitees.append(url)
        self.url = url

    def content(self):
        for motif, html in self.pages.items():
            if motif in self.url:
                return html
        return "<html></html>"

    def click(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, _ms):
        pass

    def pdf(self, **_kwargs):
        pass


class SessionFactice:
    def __init__(self, pages):
        self.page = PageFactice(pages)


def test_urls_canoniques_sont_bien_formees():
    assert URL.modules("100001") == "/ena/site/modules?idSite=100001"
    assert URL.evaluations("100001") == "/ena/site/evaluations?idSite=100001"
    assert URL.resultats("100001") == "/ena/site/resultats?idSite=100001"
    assert URL.boite_depot("100001", "1035434") == (
        "/ena/site/evaluation?idSite=100001&idEvaluation=1035434&onglet=boiteDepots"
    )
    assert URL.module("100001", "1795743") == (
        "/ena/site/module?idSite=100001&idModule=1795743&editionModule=false"
    )
    assert URL.redirection("100001", "liste_modules") == (
        "/lieninterne/redirection/100001/liste_modules"
    )


def test_plan_de_cours_capture_en_pdf(tmp_path):
    # Le lien PDF du menu est une commande ADF qui PUBLIE : on imprime la page.
    ena = Ena(SessionFactice({}))
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") is True
    assert any("/lieninterne/redirection/100001/" in u for u in ena.session.page.visitees)


def test_plan_de_cours_absent_renvoie_faux(tmp_path):
    class PageEnErreur(PageFactice):
        def goto(self, url, **_):
            super().goto(url, **_)
            self.url = "https://sitescours.monportail.ulaval.ca/portail/page_erreur"

    session = SessionFactice({})
    session.page = PageEnErreur({})
    ena = Ena(session)
    cours = Cours(
        id_site="1",
        sigle=None,
        titre="Sans plan",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    assert ena.capturer_plan_de_cours(cours, tmp_path / "plan.pdf") is False


def test_modules_utilise_l_url_canonique():
    html = (
        '<a href="/ena/site/module?idSite=100001&idModule=1795743&editionModule=false">'
        "1. Introduction</a>"
    )
    ena = Ena(SessionFactice({"/ena/site/modules": html}))
    cours = Cours(
        id_site="100001",
        sigle="ABC-1000",
        titre="Éthique",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    modules = ena.modules(cours)
    assert [m.id_module for m in modules] == ["1795743"]
    assert "/ena/site/modules?idSite=100001" in ena.session.page.visitees[0]


def test_modules_absents_ne_font_pas_echouer():
    # Le site de formation EDI n'a ni modules ni evaluations.
    ena = Ena(SessionFactice({}))
    cours = Cours(
        id_site="100006",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    assert ena.modules(cours) == []


def test_evaluations_extraites():
    html = """
    <a href="/ena/site/evaluation?idSite=100002&idEvaluation=1035434&onglet">Exposé oral I</a>
    <a href="/ena/site/evaluation?idSite=100002&idEvaluation=1035435&onglet">Rapport de suivi I</a>
    """
    ena = Ena(SessionFactice({"/ena/site/evaluations": html}))
    cours = Cours(
        id_site="100002",
        sigle="DEF-2000",
        titre="Projet",
        session=Session(code="202601", libelle="Hiver 2026"),
    )

    evaluations = ena.evaluations(cours)
    assert [e.id_evaluation for e in evaluations] == ["1035434", "1035435"]
    assert evaluations[0].titre == "Exposé oral I"


def test_fichiers_de_depot_visitent_l_onglet_boite_depots():
    ena = Ena(SessionFactice({}))
    ena.fichiers_de_depot(Evaluation(id_site="100002", id_evaluation="1035434", titre="T"))
    assert "onglet=boiteDepots" in ena.session.page.visitees[0]


def test_fichiers_du_module_visitent_l_url_du_module():
    ena = Ena(SessionFactice({}))
    ena.fichiers_du_module(Module(id_site="100001", id_module="1795743", titre="M"))
    assert "idModule=1795743" in ena.session.page.visitees[0]
    assert "editionModule=false" in ena.session.page.visitees[0]


class LienFactice:
    def __init__(self, identifiant=None):
        self.identifiant = identifiant
        self.clique = False

    def get_attribute(self, _nom):
        return self.identifiant

    def click(self, **_kwargs):
        self.clique = True


class PageAvecMenu(PageFactice):
    """Rend un menu de site et enregistre les libelles cliques."""

    def __init__(self, html_menu, liens=None):
        super().__init__({"/ena/site/accueil": html_menu})
        self.liens = liens or {}
        self.cliques = []

    def get_by_role(self, _role, name=None, exact=False):
        lien = self.liens.get(name, LienFactice())
        self.cliques.append(name)

        class Localisateur:
            first = lien

        return Localisateur()


def test_parcourir_menu_ouvre_chaque_section_du_site():
    page = PageAvecMenu(
        '<a href="#">Concepts de base</a><a href="#">Six biais</a>'
        '<a href="#">Tableau de bord</a>'
    )
    session = SessionFactice({})
    session.page = page
    ena = Ena(session)

    vues = []
    nombre = ena.parcourir_menu(
        Cours(
            id_site="100006",
            sigle=None,
            titre="EDI",
            session=Session(code="202209", libelle="Automne 2022"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    # Le chrome du portail est ecarte par sections_du_menu.
    assert vues == ["Concepts de base", "Six biais"]
    assert nombre == 2


def test_parcourir_menu_n_ouvre_jamais_une_commande_adf():
    # cmdObtenirPlanCours publie une nouvelle version du plan de cours.
    page = PageAvecMenu(
        '<a href="#">Plan de cours</a>',
        liens={"Plan de cours": LienFactice("m:j_id_1:cmdObtenirPlanCours")},
    )
    session = SessionFactice({})
    session.page = page
    ena = Ena(session)

    vues = []
    nombre = ena.parcourir_menu(
        Cours(
            id_site="1",
            sigle=None,
            titre="X",
            session=Session(code="202601", libelle="Hiver 2026"),
        ),
        lambda libelle, html: vues.append(libelle),
    )

    assert vues == []
    assert nombre == 0
    assert page.liens["Plan de cours"].clique is False
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_ena.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.ena'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/ena.py` :

```python
"""Navigation dans les anciens sites de cours monPortail.

Principe etabli en phase 0 : les libelles de menu varient d'un site a l'autre
(« Feuille de route » ici, « Contenu et activites » la), mais les URL canoniques
restent valides partout. On navigue donc par URL, et on lit le menu seulement
pour attraper ce qui sort du schema.
"""

from pathlib import Path

from extracteur.extraction import (
    est_commande_adf,
    fichiers_depuis_html,
    modules_depuis_html,
    resultats_depuis_html,
    sections_du_menu,
)
from extracteur.modele import Evaluation

BASE = "https://sitescours.monportail.ulaval.ca"
ONGLET_CONTENU = "text=Contenu du module"


class URL:
    """Les URL canoniques relevees en phase 0."""

    @staticmethod
    def accueil(id_site: str) -> str:
        return f"/ena/site/accueil?idSite={id_site}"

    @staticmethod
    def modules(id_site: str) -> str:
        return f"/ena/site/modules?idSite={id_site}"

    @staticmethod
    def module(id_site: str, id_module: str) -> str:
        return f"/ena/site/module?idSite={id_site}&idModule={id_module}&editionModule=false"

    @staticmethod
    def evaluations(id_site: str) -> str:
        return f"/ena/site/evaluations?idSite={id_site}"

    @staticmethod
    def boite_depot(id_site: str, id_evaluation: str) -> str:
        return (
            f"/ena/site/evaluation?idSite={id_site}"
            f"&idEvaluation={id_evaluation}&onglet=boiteDepots"
        )

    @staticmethod
    def resultats(id_site: str) -> str:
        return f"/ena/site/resultats?idSite={id_site}"

    @staticmethod
    def redirection(id_site: str, section: str) -> str:
        """Routeur a URL stables de l'ENA, verifie en phase 0 sur liste_modules."""
        return f"/lieninterne/redirection/{id_site}/{section}"


# Noms de section tentes pour le plan de cours. La phase 0 n'a confirme que
# `liste_modules` ; les autres sont des candidats a valider au premier passage.
SECTIONS_PLAN_DE_COURS = ("plan_de_cours", "plancours", "plan_cours")


class Ena:
    def __init__(self, session):
        self.session = session

    def _visiter(self, chemin: str) -> str:
        url = chemin if chemin.startswith("http") else BASE + chemin
        self.session.page.goto(url, wait_until="networkidle")
        return self.session.page.content()

    def modules(self, cours) -> list:
        html = self._visiter(URL.modules(cours.id_site))
        return modules_depuis_html(html, cours.id_site)

    def fichiers_du_module(self, module) -> list:
        self._visiter(URL.module(module.id_site, module.id_module))

        # L'onglet « Contenu du module » est un lien ADF : les documents ne sont
        # pas dans le DOM avant le clic. Son absence n'est pas une erreur.
        try:
            self.session.page.click(ONGLET_CONTENU, timeout=5000)
            self.session.page.wait_for_timeout(1500)
        except Exception:
            pass

        return fichiers_depuis_html(self.session.page.content())

    def evaluations(self, cours) -> list:
        html = self._visiter(URL.evaluations(cours.id_site))
        return _evaluations_depuis_html(html, cours.id_site)

    def fichiers_de_depot(self, evaluation) -> list:
        html = self._visiter(URL.boite_depot(evaluation.id_site, evaluation.id_evaluation))
        return fichiers_depuis_html(html)

    def resultats(self, cours) -> list:
        html = self._visiter(URL.resultats(cours.id_site))
        return resultats_depuis_html(html)

    def parcourir_menu(self, cours, action) -> int:
        """Repli pour les sites sans modules ni evaluations.

        Les entrees de menu de l'ENA sont des liens ADF `href="#"` : elles ne
        sont pas atteignables par URL, il faut cliquer. `action(libelle, html)`
        est appele pour chaque section ouverte ; la navigation ne sait rien du
        disque.

        Toute commande ADF `cmd*` est ecartee : `cmdObtenirPlanCours` publie une
        nouvelle version du plan de cours au lieu de le telecharger.
        """
        html = self._visiter(URL.accueil(cours.id_site))
        visitees = 0

        for libelle in sections_du_menu(html):
            try:
                cible = self.session.page.get_by_role("link", name=libelle, exact=True).first
                if est_commande_adf(cible.get_attribute("id")):
                    continue
                cible.click(timeout=5000)
                self.session.page.wait_for_timeout(1500)
            except Exception:
                continue

            action(libelle, self.session.page.content())
            visitees += 1

        return visitees

    def capturer_pdf(self, chemin: str, destination: Path) -> None:
        self._visiter(chemin)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.session.page.pdf(path=str(destination), format="A4", print_background=True)

    def capturer_plan_de_cours(self, cours, destination: Path) -> bool:
        """Imprime le plan de cours en PDF. Retourne False s'il n'y en a pas.

        Le menu contient un lien a icone PDF qui ressemble a un telechargement :
        c'est `cmdObtenirPlanCours`, une commande ADF qui PUBLIE une nouvelle
        version du plan. On ne la touche jamais. On imprime la page a la place.
        """
        for section in SECTIONS_PLAN_DE_COURS:
            self._visiter(URL.redirection(cours.id_site, section))
            if "page_erreur" in self.session.page.url:
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            self.session.page.pdf(path=str(destination), format="A4", print_background=True)
            return True

        return False


def _evaluations_depuis_html(html: str, id_site: str) -> list[Evaluation]:
    from urllib.parse import parse_qs, urlsplit

    from bs4 import BeautifulSoup

    trouvees: dict[str, Evaluation] = {}
    for lien in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        href = lien["href"]
        if "idEvaluation=" not in href:
            continue

        id_evaluation = parse_qs(urlsplit(href).query).get("idEvaluation", [""])[0]
        if not id_evaluation:
            continue

        titre = lien.get_text(strip=True)
        existante = trouvees.get(id_evaluation)
        if existante is None or (titre and not existante.titre):
            trouvees[id_evaluation] = Evaluation(
                id_site=id_site, id_evaluation=id_evaluation, titre=titre
            )

    return list(trouvees.values())
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_ena.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/ena.py tests/test_ena.py
git commit -m "feat: navigation ENA par URL canoniques"
```

---

### Task 9: Énumération des cours et des sessions

Le sélecteur `m:selectListeSessionsId` du panneau « Liste des cours » est la seule source de l'historique complet.

**Files:**
- Modify: `extracteur/ena.py`
- Modify: `tests/test_ena.py`

**Interfaces:**
- Consumes: `Ena`.
- Produces: `Ena.sessions_disponibles() -> list[Session]`, `Ena.sites_de_session(session) -> list[Cours]`, et la fonction pure `extraction.cours_depuis_html(html, session) -> list[Cours]`.

- [ ] **Step 1: Ajouter les tests d'extraction des cours**

Ajouter à `tests/test_extraction.py` :

```python
from extracteur.extraction import cours_depuis_html
from extracteur.modele import Session


def test_cours_depuis_html():
    html = """
    <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=100001">
      Éthique et professionnalisme</a>
    <a href="https://sitescours.monportail.ulaval.ca/ena/site/accueil?idSite=100002">
      Projet de fin d'études II</a>
    """
    session = Session(code="202601", libelle="Hiver 2026")
    cours = cours_depuis_html(html, session)

    assert [c.id_site for c in cours] == ["100001", "100002"]
    assert cours[0].titre == "Éthique et professionnalisme"


def test_cours_extrait_le_sigle_quand_il_est_present():
    html = (
        '<a href="/ena/site/accueil?idSite=1">ABC-1000 : Éthique et professionnalisme</a>'
    )
    cours = cours_depuis_html(html, Session(code="202601", libelle="Hiver 2026"))
    assert cours[0].sigle == "ABC-1000"
    assert cours[0].titre == "Éthique et professionnalisme"


def test_cours_sans_sigle():
    html = '<a href="/ena/site/accueil?idSite=100006">Nos biais inconscients</a>'
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
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_extraction.py -v`
Expected: FAIL avec `ImportError: cannot import name 'cours_depuis_html'`

- [ ] **Step 3: Implémenter l'extraction des cours**

Ajouter à `extracteur/extraction.py` :

```python
import re

from extracteur.modele import Cours

MOTIF_SIGLE = re.compile(r"^([A-Z]{3}-\d{4})\s*:\s*(.+)$")


def cours_depuis_html(html: str, session) -> list[Cours]:
    """Cours listes dans le panneau « Liste des cours »."""
    trouves: dict[str, Cours] = {}

    for lien in _soupe(html).find_all("a", href=True):
        href = lien["href"]
        if "/ena/site/accueil" not in href or "idSite=" not in href:
            continue

        id_site = parse_qs(urlsplit(href).query).get("idSite", [""])[0]
        if not id_site:
            continue

        texte = lien.get_text(strip=True)
        correspondance = MOTIF_SIGLE.match(texte)
        sigle = correspondance.group(1) if correspondance else None
        titre = correspondance.group(2) if correspondance else texte

        existant = trouves.get(id_site)
        if existant is None or (titre and not existant.titre):
            trouves[id_site] = Cours(
                id_site=id_site, sigle=sigle, titre=titre, session=session
            )

    return list(trouves.values())
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_extraction.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Ajouter l'énumération des sessions à `ena.py`**

Ajouter à la classe `Ena` :

```python
    SELECTEUR_SESSIONS = "select[name='m:selectListeSessionsId']"
    BOUTON_LISTE_COURS = "text=Liste des cours"

    def _ouvrir_panneau_cours(self, id_site_depart: str) -> None:
        """Le panneau « Liste des cours » porte le selecteur de session."""
        self._visiter(URL.accueil(id_site_depart))
        self.session.page.click(self.BOUTON_LISTE_COURS, timeout=10000)
        self.session.page.wait_for_selector(self.SELECTEUR_SESSIONS, timeout=10000)

    def sessions_disponibles(self, id_site_depart: str) -> list:
        from extracteur.modele import Session

        self._ouvrir_panneau_cours(id_site_depart)
        options = self.session.page.eval_on_selector_all(
            f"{self.SELECTEUR_SESSIONS} option",
            "els => els.map(e => [e.value, e.textContent.trim()])",
        )
        return [Session(code=valeur, libelle=libelle) for valeur, libelle in options]

    def sites_de_session(self, session_cours, id_site_depart: str) -> list:
        from extracteur.extraction import cours_depuis_html

        self._ouvrir_panneau_cours(id_site_depart)
        self.session.page.select_option(self.SELECTEUR_SESSIONS, session_cours.code)
        self.session.page.wait_for_timeout(2000)
        return cours_depuis_html(self.session.page.content(), session_cours)
```

- [ ] **Step 6: Lancer toute la suite**

Run: `python -m pytest -v`
Expected: PASS, tous les tests.

- [ ] **Step 7: Commit**

```bash
git add extracteur/ena.py extracteur/extraction.py tests/
git commit -m "feat: enumeration des sessions et des cours de l'historique"
```

---

### Task 10: Archiveur — orchestration, arborescence et isolation

L'échec d'un cours ne doit jamais emporter la session. C'est ce que les tests vérifient ici.

**Files:**
- Create: `extracteur/archiveur.py`
- Test: `tests/test_archiveur.py`

**Interfaces:**
- Consumes: `ena.Ena`, `manifeste.Manifeste`, `telechargement.telecharger`, `nommage.*`, `modele.*`.
- Produces: `Archiveur(ena, transport, racine, evenements)` avec `.archiver(cours_choisis) -> Resultat` et `.chemin_du_cours(cours) -> Path`. Les événements sont des tuples `(type, texte)` poussés dans une `queue.Queue` ; types : `"cours"`, `"fichier"`, `"saute"`, `"echec"`, `"pause"`, `"fin"`.

- [ ] **Step 1: Écrire les tests**

`tests/test_archiveur.py` :

```python
import queue

import pytest

from extracteur.archiveur import Archiveur
from extracteur.modele import Cours, Evaluation, Fichier, Module, Note, Session
from extracteur.telechargement import ErreurPermanente, SessionExpiree

SESSION = Session(code="202601", libelle="Hiver 2026")
COURS = Cours(id_site="100001", sigle="ABC-1000", titre="Éthique", session=SESSION)


class EnaFactice:
    def __init__(self, fichiers=None, notes=None, erreur=None):
        self._fichiers = fichiers or []
        self._notes = notes or []
        self._erreur = erreur
        self.pdf_captures = []

    def modules(self, cours):
        if self._erreur:
            raise self._erreur
        return [Module(id_site=cours.id_site, id_module="1", titre="Module 1")]

    def fichiers_du_module(self, module):
        return self._fichiers

    def evaluations(self, cours):
        return [Evaluation(id_site=cours.id_site, id_evaluation="9", titre="TP1")]

    def fichiers_de_depot(self, evaluation):
        return []

    def resultats(self, cours):
        return self._notes

    def capturer_pdf(self, chemin, destination):
        self.pdf_captures.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 factice")

    def capturer_plan_de_cours(self, cours, destination):
        self.pdf_captures.append(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 plan")
        return True


def transport_ok(url):
    class R:
        statut = 200

        def morceaux(self):
            yield b"contenu"

    return R()


def transport_403(url):
    class R:
        statut = 403

        def morceaux(self):
            yield b""

    return R()


def test_arborescence_session_cours(tmp_path):
    archiveur = Archiveur(EnaFactice(), transport_ok, tmp_path, queue.Queue())
    chemin = archiveur.chemin_du_cours(COURS)
    assert chemin == tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique"


def test_fichier_telecharge_et_inscrit_au_manifeste(tmp_path):
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    archiveur = Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    attendu = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1" / "notes.pdf"
    assert attendu.exists()
    assert resultat.fichiers_ecrits == 1
    assert (tmp_path / "manifeste.csv").exists()


def test_relancer_saute_ce_qui_est_deja_la(tmp_path):
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    second = Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    assert second.fichiers_ecrits == 0
    assert second.fichiers_sautes == 1


def test_echec_permanent_consigne_et_continue(tmp_path):
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    archiveur = Archiveur(EnaFactice([fichier]), transport_403, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS])

    assert resultat.fichiers_ecrits == 0
    assert len(resultat.echecs) == 1
    assert "403" in resultat.echecs[0].cause


def test_un_cours_qui_casse_n_emporte_pas_les_autres(tmp_path):
    autre = Cours(id_site="2", sigle="DEF-2000", titre="Projet", session=SESSION)
    ena = EnaFactice(erreur=RuntimeError("site illisible"))
    archiveur = Archiveur(ena, transport_ok, tmp_path, queue.Queue())

    resultat = archiveur.archiver([COURS, autre])

    assert len(resultat.echecs) == 2
    assert all("site illisible" in e.cause for e in resultat.echecs)


def test_session_expiree_interrompt_et_signale(tmp_path):
    def transport_401(url):
        raise SessionExpiree(url)

    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    evenements = queue.Queue()
    archiveur = Archiveur(EnaFactice([fichier]), transport_401, tmp_path, evenements)

    with pytest.raises(SessionExpiree):
        archiveur.archiver([COURS])

    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "pause" in types


def test_notes_exportees(tmp_path):
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    notes = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "notes.csv"
    assert notes.exists()
    assert "Examen 1" in notes.read_text(encoding="utf-8-sig")


def test_pages_capturees_en_pdf(tmp_path):
    ena = EnaFactice()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    assert any("Pages" in str(p) for p in ena.pdf_captures)


def test_evenements_emis(tmp_path):
    evenements = queue.Queue()
    fichier = Fichier(nom="a.pdf", url="/contenu/sitescours/x/a.pdf?identifiant=a")
    Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, evenements).archiver([COURS])

    types = []
    while not evenements.empty():
        types.append(evenements.get()[0])
    assert "cours" in types
    assert "fichier" in types
    assert "fin" in types


def test_noms_de_fichiers_assainis(tmp_path):
    fichier = Fichier(nom='ra:pport<1>.pdf', url="/contenu/sitescours/x/a.pdf?identifiant=a")
    Archiveur(EnaFactice([fichier]), transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert (dossier / "ra-pport-1-.pdf").exists()


def test_relancer_ne_cree_jamais_de_doublon_numerote(tmp_path):
    # Le piege : assainir puis desambiguiser le nom AVANT de verifier le
    # manifeste ferait retelecharger tout le contenu sous « (2) » a chaque
    # relance, et l'archive doublerait de taille a chaque coupure reseau.
    fichier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    ena = EnaFactice([fichier])

    for _ in range(3):
        Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes.pdf"]


def test_collision_reelle_entre_deux_urls_differentes(tmp_path):
    # Deux ressources distinctes portant le meme nom doivent coexister.
    premier = Fichier(nom="notes.pdf", url="/contenu/sitescours/x/notes.pdf?identifiant=a")
    second = Fichier(nom="notes.pdf", url="/contenu/sitescours/y/notes.pdf?identifiant=b")
    archiveur = Archiveur(EnaFactice([premier, second]), transport_ok, tmp_path, queue.Queue())

    archiveur.archiver([COURS])

    dossier = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Documents" / "Module 1"
    assert sorted(p.name for p in dossier.iterdir()) == ["notes (2).pdf", "notes.pdf"]


def test_plan_de_cours_archive(tmp_path):
    Archiveur(EnaFactice(), transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    plan = tmp_path / "2026-1 Hiver" / "ABC-1000 Éthique" / "Plan de cours" / "plan-de-cours.pdf"
    assert plan.exists()


def test_site_sans_modules_ni_evaluations_passe_par_le_menu(tmp_path):
    # Le site de formation EDI n'a ni modules ni evaluations : sans repli par le
    # menu, son dossier serait vide.
    class EnaSansSchema(EnaFactice):
        def __init__(self):
            super().__init__()
            self.session = type("S", (), {"page": type("P", (), {"pdf": lambda *a, **k: None})()})()

        def modules(self, cours):
            return []

        def evaluations(self, cours):
            return []

        def parcourir_menu(self, cours, action):
            action(
                "Six biais",
                '<a href="/contenu/sitescours/x/biais.pdf?identifiant=a">biais.pdf</a>',
            )
            return 1

    cours = Cours(
        id_site="100006",
        sigle=None,
        titre="Nos biais inconscients",
        session=Session(code="202209", libelle="Automne 2022"),
    )
    Archiveur(EnaSansSchema(), transport_ok, tmp_path, queue.Queue()).archiver([cours])

    attendu = (
        tmp_path / "2022-3 Automne" / "Nos biais inconscients" / "Documents" / "Six biais" / "biais.pdf"
    )
    assert attendu.exists()


def test_repli_par_le_menu_non_declenche_si_des_modules_existent(tmp_path):
    class EnaAvecSentinelle(EnaFactice):
        def __init__(self):
            super().__init__()
            self.menu_parcouru = False

        def parcourir_menu(self, cours, action):
            self.menu_parcouru = True
            return 0

    ena = EnaAvecSentinelle()
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])
    assert ena.menu_parcouru is False


def test_notes_consolidees_a_la_racine(tmp_path):
    ena = EnaFactice(notes=[Note(evaluation="Examen 1", note="18", sur="20")])
    Archiveur(ena, transport_ok, tmp_path, queue.Queue()).archiver([COURS])

    consolide = tmp_path / "notes-tous-cours.csv"
    contenu = consolide.read_text(encoding="utf-8-sig")
    assert "2026-1 Hiver" in contenu
    assert "ABC-1000 Éthique" in contenu
    assert "Examen 1" in contenu
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_archiveur.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.archiveur'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/archiveur.py` :

```python
"""Orchestration de l'archivage.

Deux promesses tenues ici : un cours qui casse n'emporte jamais les autres, et
une session expiree met la file en pause plutot que de bruler les cours
restants en erreurs d'authentification.
"""

from pathlib import Path

from extracteur.manifeste import Manifeste, ecrire_notes, ecrire_notes_consolidees
from extracteur.modele import Echec, Resultat
from extracteur.nommage import nom_sur, nom_unique, tronquer
from extracteur.stockage import deja_present
from extracteur.telechargement import ErreurPermanente, SessionExpiree, telecharger


def _segment(texte: str) -> str:
    return tronquer(nom_sur(texte))


class Archiveur:
    def __init__(self, ena, transport, racine: Path, evenements):
        self.ena = ena
        self.transport = transport
        self.racine = Path(racine)
        self.evenements = evenements
        self.manifeste = Manifeste(self.racine)
        self.resultat = Resultat()

    def _emettre(self, type_evenement: str, texte: str) -> None:
        self.evenements.put((type_evenement, texte))

    def chemin_du_cours(self, cours) -> Path:
        return self.racine / _segment(cours.session.dossier()) / _segment(cours.dossier())

    def _relatif(self, destination: Path) -> str:
        return str(destination.relative_to(self.racine)).replace("\\", "/")

    def archiver(self, cours_choisis) -> Resultat:
        notes_consolidees: list[tuple[str, str, object]] = []

        for cours in cours_choisis:
            self._emettre("cours", cours.dossier())
            try:
                self._archiver_un_cours(cours, notes_consolidees)
            except SessionExpiree:
                self._emettre("pause", "Session expirée — reconnectez-vous puis reprenez.")
                raise
            except Exception as erreur:  # isolation stricte par cours
                self.resultat.echecs.append(
                    Echec(cours=cours.dossier(), element="(cours entier)", cause=str(erreur))
                )
                self._emettre("echec", f"{cours.dossier()} : {erreur}")

        ecrire_notes_consolidees(self.racine / "notes-tous-cours.csv", notes_consolidees)
        self._emettre("fin", f"{self.resultat.fichiers_ecrits} fichiers archivés")
        return self.resultat

    def _archiver_un_cours(self, cours, notes_consolidees) -> None:
        base = self.chemin_du_cours(cours)

        plan = base / "Plan de cours" / "plan-de-cours.pdf"
        if not plan.exists():
            try:
                self.ena.capturer_plan_de_cours(cours, plan)
            except Exception as erreur:
                self.resultat.echecs.append(
                    Echec(cours=cours.dossier(), element="plan-de-cours.pdf", cause=str(erreur))
                )

        modules = self.ena.modules(cours)
        for module in modules:
            dossier = base / "Documents" / _segment(module.titre or module.id_module)
            for fichier in self.ena.fichiers_du_module(module):
                self._recuperer(cours, fichier, dossier)

            cible_pdf = base / "Pages" / _segment(f"{module.titre or module.id_module}.pdf")
            self._capturer(cours, module, cible_pdf)

        evaluations = self.ena.evaluations(cours)
        for evaluation in evaluations:
            dossier = base / "Mes dépôts" / _segment(evaluation.titre or evaluation.id_evaluation)
            for fichier in self.ena.fichiers_de_depot(evaluation):
                self._recuperer(cours, fichier, dossier)

        notes = self.ena.resultats(cours)
        if notes:
            ecrire_notes(base / "notes.csv", notes)
            for note in notes:
                notes_consolidees.append((cours.session.dossier(), cours.dossier(), note))

        # Repli. Certains sites n'ont ni modules ni evaluations : leur contenu
        # n'est atteignable qu'en cliquant les entrees de leur propre menu. Sans
        # cette branche, ces cours produiraient un dossier vide.
        if not modules and not evaluations:
            self._parcourir_le_menu(cours, base)

    def _parcourir_le_menu(self, cours, base: Path) -> None:
        from extracteur.extraction import fichiers_depuis_html

        def traiter(libelle, html):
            dossier = base / "Documents" / _segment(libelle)
            for fichier in fichiers_depuis_html(html):
                self._recuperer(cours, fichier, dossier)

            cible = base / "Pages" / _segment(f"{libelle}.pdf")
            if not cible.exists():
                cible.parent.mkdir(parents=True, exist_ok=True)
                self.session_pdf(cible)

        self.ena.parcourir_menu(cours, traiter)

    def session_pdf(self, destination: Path) -> None:
        """Imprime la page courante, sans renavigation."""
        self.ena.session.page.pdf(path=str(destination), format="A4", print_background=True)

    def _capturer(self, cours, module, destination: Path) -> None:
        if destination.exists():
            return
        try:
            from extracteur.ena import URL

            self.ena.capturer_pdf(URL.module(module.id_site, module.id_module), destination)
        except Exception as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=destination.name, cause=f"PDF : {erreur}")
            )

    def _recuperer(self, cours, fichier, dossier: Path) -> None:
        if not fichier.est_interne():
            return

        dossier.mkdir(parents=True, exist_ok=True)
        nom = tronquer(nom_sur(fichier.nom))
        destination = dossier / nom
        relatif = self._relatif(destination)

        # Reprise. Le nom n'est surtout PAS desambiguise avant ce test : sinon
        # chaque relance retelechargerait tout sous « (2) », « (3) »... et
        # l'archive doublerait de taille a chaque coupure reseau.
        if self.manifeste.deja_archive(relatif) and deja_present(destination, None):
            self.resultat.fichiers_sautes += 1
            self._emettre("saute", nom)
            return

        # Collision reelle : deux ressources distinctes portent le meme nom.
        if destination.exists():
            nom = nom_unique(dossier, nom)
            destination = dossier / nom
            relatif = self._relatif(destination)

        try:
            taille, empreinte = telecharger(self.transport, fichier.url, destination)
        except SessionExpiree:
            raise
        except ErreurPermanente as erreur:
            self.resultat.echecs.append(
                Echec(cours=cours.dossier(), element=nom, cause=str(erreur), url=fichier.url)
            )
            self._emettre("echec", f"{nom} : {erreur}")
            return

        self.manifeste.ajouter(relatif, taille, empreinte, fichier.url)
        self.resultat.fichiers_ecrits += 1
        self._emettre("fichier", nom)

    # Note sur le debit : l'archiveur telecharge en serie, donc un seul transfert
    # a la fois. La spec plafonne a trois simultanes ; rester en dessous est
    # conforme, et evite toute limitation de debit cote Universite.
```

- [ ] **Step 4: Lancer les tests pour vérifier qu'ils passent**

Run: `python -m pytest tests/test_archiveur.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add extracteur/archiveur.py tests/test_archiveur.py
git commit -m "feat: archiveur avec isolation par cours et pause sur session expiree"
```

---

### Task 11: Mode console `--un-seul-cours`

Le vrai garde-fou du projet : on vérifie de ses yeux un cours avant de lâcher l'outil sur quarante.

**Files:**
- Create: `extracteur/__main__.py`

**Interfaces:**
- Consumes: `auth.SessionNavigateur`, `ena.Ena`, `archiveur.Archiveur`, `manifeste.ecrire_rapport`.
- Produces: la commande `python -m extracteur --un-seul-cours <idSite> --destination <dossier>`.

- [ ] **Step 1: Écrire le point d'entrée**

`extracteur/__main__.py` :

```python
"""Points d'entree de l'extracteur.

  python -m extracteur                          -> fenetre graphique
  python -m extracteur --un-seul-cours 100001   -> verification en console
"""

import argparse
import queue
import sys
from pathlib import Path

from extracteur.archiveur import Archiveur
from extracteur.auth import SessionNavigateur
from extracteur.ena import Ena
from extracteur.manifeste import ecrire_rapport
from extracteur.modele import Cours, Session

DOSSIER_PROFIL = Path(".session")


def _connecter(sans_fenetre: bool = False) -> SessionNavigateur:
    session = SessionNavigateur(DOSSIER_PROFIL, sans_fenetre=sans_fenetre)
    session.ouvrir()
    print("Connectez-vous dans la fenetre du navigateur...")
    if not session.attendre_connexion():
        print("ECHEC : connexion non detectee.", file=sys.stderr)
        session.fermer()
        raise SystemExit(1)
    print("Connexion detectee.")
    return session


def _un_seul_cours(id_site: str, destination: Path) -> int:
    session = _connecter()
    ena = Ena(session)

    # Sans passer par l'enumeration, on fabrique un cours minimal : le but est de
    # verifier la chaine complete sur un site precis, pas de le nommer joliment.
    cours = Cours(
        id_site=id_site,
        sigle=None,
        titre=f"site{id_site}",
        session=Session(code="000000", libelle="Verification"),
    )

    evenements = queue.Queue()
    archiveur = Archiveur(ena, session.transport, destination, evenements)
    resultat = archiveur.archiver([cours])
    session.fermer()

    while not evenements.empty():
        type_evenement, texte = evenements.get()
        print(f"  [{type_evenement}] {texte}")

    ecrire_rapport(
        destination / "_rapport.html",
        resultat.echecs,
        {"fichiers ecrits": resultat.fichiers_ecrits, "fichiers sautes": resultat.fichiers_sautes},
    )

    print(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    print(f"Echecs : {len(resultat.echecs)}  -> {destination / '_rapport.html'}")
    return 0 if resultat.fichiers_ecrits or not resultat.echecs else 1


def main() -> int:
    analyseur = argparse.ArgumentParser(prog="extracteur")
    analyseur.add_argument("--un-seul-cours", dest="id_site", help="idSite a archiver, en console")
    analyseur.add_argument(
        "--destination", type=Path, default=Path("Archive monPortail"), help="dossier de sortie"
    )
    arguments = analyseur.parse_args()

    if arguments.id_site:
        return _un_seul_cours(arguments.id_site, arguments.destination)

    from extracteur.ui import lancer

    lancer(arguments.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Vérifier que la suite de tests passe toujours**

Run: `python -m pytest -v`
Expected: PASS, tous les tests. (`ui` n'est pas encore importé au chargement du module, seulement dans `main()`.)

- [ ] **Step 3: Vérification manuelle sur un cours récent**

Run: `python -m extracteur --un-seul-cours 100001 --destination "C:\Temp\ArchiveTest"`

Ouvrir le dossier produit et vérifier : les documents des modules sont présents, `Pages/` contient les PDF, `notes.csv` contient les résultats, `_rapport.html` s'ouvre.

- [ ] **Step 4: Vérification manuelle sur un cours ancien**

Run: `python -m extracteur --un-seul-cours 100007 --destination "C:\Temp\ArchiveTest"`

C'est le test qui compte : un site d'une autre génération, dont la structure diffère. Noter dans `_rapport.html` tout ce qui échoue, et capturer le HTML des pages fautives dans `tests/fixtures/` pour en faire des cas de test.

- [ ] **Step 5: Commit**

```bash
git add extracteur/__main__.py
git commit -m "feat: mode console --un-seul-cours pour verification avant archivage complet"
```

---

### Task 12: Interface graphique

Tkinter, aucun appel réseau, un thread de travail et une file d'événements.

**Files:**
- Create: `extracteur/ui.py`

**Interfaces:**
- Consumes: `auth.SessionNavigateur`, `ena.Ena`, `archiveur.Archiveur`, `manifeste.ecrire_rapport`.
- Produces: `lancer(destination_par_defaut: Path) -> None`.

- [ ] **Step 1: Écrire la fenêtre**

`extracteur/ui.py` :

```python
"""Fenetre de controle. Aucun appel reseau : elle ne fait que lire une file.

L'archiveur tourne dans un thread de travail et publie ses evenements ; la
fenetre vide la file toutes les 100 ms. C'est ce qui evite l'interface gelee
pendant un telechargement de 200 Mo.
"""

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from extracteur.archiveur import Archiveur
from extracteur.auth import SessionNavigateur
from extracteur.ena import Ena
from extracteur.manifeste import ecrire_rapport
from extracteur.telechargement import SessionExpiree

DOSSIER_PROFIL = Path(".session")


class Fenetre:
    def __init__(self, racine: tk.Tk, destination: Path):
        self.racine = racine
        self.destination = destination
        self.evenements: queue.Queue = queue.Queue()
        self.session = None
        self.ena = None
        self.cours = []
        self.cases = []

        racine.title("Extracteur monPortail")
        racine.geometry("820x620")

        haut = ttk.Frame(racine, padding=10)
        haut.pack(fill="x")

        self.bouton_connexion = ttk.Button(haut, text="Se connecter", command=self._connecter)
        self.bouton_connexion.pack(side="left")

        self.bouton_dossier = ttk.Button(haut, text="Destination…", command=self._choisir_dossier)
        self.bouton_dossier.pack(side="left", padx=6)

        self.etiquette_dossier = ttk.Label(haut, text=str(destination))
        self.etiquette_dossier.pack(side="left", padx=6)

        self.bouton_archiver = ttk.Button(
            haut, text="Archiver", command=self._archiver, state="disabled"
        )
        self.bouton_archiver.pack(side="right")

        self.liste = ttk.Frame(racine, padding=10)
        self.liste.pack(fill="both", expand=True)

        self.progression = ttk.Progressbar(racine, mode="determinate")
        self.progression.pack(fill="x", padx=10)

        self.journal = tk.Text(racine, height=12, state="disabled")
        self.journal.pack(fill="both", expand=True, padx=10, pady=10)

        self.racine.after(100, self._vider_la_file)

    def _ecrire(self, texte: str) -> None:
        self.journal.configure(state="normal")
        self.journal.insert("end", texte + "\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def _choisir_dossier(self) -> None:
        choisi = filedialog.askdirectory()
        if choisi:
            self.destination = Path(choisi)
            self.etiquette_dossier.configure(text=choisi)

    def _connecter(self) -> None:
        self.bouton_connexion.configure(state="disabled")
        self._ecrire("Ouverture du navigateur. Connectez-vous, MFA compris.")
        threading.Thread(target=self._connecter_en_fond, daemon=True).start()

    def _connecter_en_fond(self) -> None:
        self.session = SessionNavigateur(DOSSIER_PROFIL)
        self.session.ouvrir()
        if not self.session.attendre_connexion():
            self.evenements.put(("echec", "Connexion non détectée."))
            return

        self.ena = Ena(self.session)
        id_depart = self.session.page.url.split("idSite=")[-1].split("&")[0]

        cours = []
        for session_cours in self.ena.sessions_disponibles(id_depart):
            cours.extend(self.ena.sites_de_session(session_cours, id_depart))

        self.cours = cours
        self.evenements.put(("cours_charges", f"{len(cours)} cours trouvés"))

    def _afficher_les_cours(self) -> None:
        for enfant in self.liste.winfo_children():
            enfant.destroy()
        self.cases = []

        zone = tk.Canvas(self.liste)
        barre = ttk.Scrollbar(self.liste, orient="vertical", command=zone.yview)
        interieur = ttk.Frame(zone)
        interieur.bind("<Configure>", lambda _e: zone.configure(scrollregion=zone.bbox("all")))
        zone.create_window((0, 0), window=interieur, anchor="nw")
        zone.configure(yscrollcommand=barre.set)
        zone.pack(side="left", fill="both", expand=True)
        barre.pack(side="right", fill="y")

        for cours in self.cours:
            variable = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                interieur,
                text=f"{cours.session.dossier()} — {cours.dossier()}",
                variable=variable,
            ).pack(anchor="w")
            self.cases.append((variable, cours))

        self.bouton_archiver.configure(state="normal")

    def _archiver(self) -> None:
        choisis = [cours for variable, cours in self.cases if variable.get()]
        if not choisis:
            self._ecrire("Aucun cours sélectionné.")
            return

        self.bouton_archiver.configure(state="disabled")
        self.progression.configure(maximum=len(choisis), value=0)
        threading.Thread(target=self._archiver_en_fond, args=(choisis,), daemon=True).start()

    def _archiver_en_fond(self, choisis) -> None:
        archiveur = Archiveur(self.ena, self.session.transport, self.destination, self.evenements)
        try:
            resultat = archiveur.archiver(choisis)
        except SessionExpiree:
            # L'archiveur a deja emis un evenement "pause" : la fenetre reactive
            # le bouton, qui devient de fait le bouton Reprendre. Relancer
            # reprend ou on en etait, la reprise etant idempotente.
            return

        ecrire_rapport(
            self.destination / "_rapport.html",
            resultat.echecs,
            {
                "fichiers écrits": resultat.fichiers_ecrits,
                "fichiers sautés": resultat.fichiers_sautes,
                "échecs": len(resultat.echecs),
            },
        )

    def _vider_la_file(self) -> None:
        while not self.evenements.empty():
            type_evenement, texte = self.evenements.get()
            if type_evenement == "cours_charges":
                self._ecrire(texte)
                self._afficher_les_cours()
            elif type_evenement == "cours":
                self._ecrire(f"→ {texte}")
                self.progression.step(1)
            elif type_evenement == "echec":
                self._ecrire(f"   ÉCHEC {texte}")
            elif type_evenement == "pause":
                self._ecrire(f"   PAUSE {texte}")
                # Le bouton redevient actif : c'est le bouton Reprendre.
                self.bouton_archiver.configure(text="Reprendre", state="normal")
            elif type_evenement == "fin":
                self._ecrire(texte)
                self.bouton_archiver.configure(state="normal")
            else:
                self._ecrire(f"   {texte}")

        self.racine.after(100, self._vider_la_file)


def lancer(destination: Path) -> None:
    racine = tk.Tk()
    Fenetre(racine, destination)
    racine.mainloop()
```

- [ ] **Step 2: Vérifier que la suite de tests passe**

Run: `python -m pytest -v`
Expected: PASS, tous les tests.

- [ ] **Step 3: Vérification manuelle de la fenêtre**

Run: `python -m extracteur`

Vérifier : le bouton **Se connecter** ouvre Chromium ; après connexion, la liste des cours s'affiche groupée par session et cochée ; décocher un cours puis cliquer **Archiver** lance le traitement ; l'interface ne gèle pas et le journal défile.

- [ ] **Step 4: Commit**

```bash
git add extracteur/ui.py
git commit -m "feat: fenetre de controle Tkinter alimentee par une file d'evenements"
```

---

### Task 13: Vérification finale et archive ZIP

Dernière étape : confronter le disque au manifeste, puis proposer le ZIP.

**Files:**
- Create: `extracteur/verification.py`
- Modify: `extracteur/__main__.py`
- Test: `tests/test_verification.py`

**Interfaces:**
- Consumes: `manifeste.Manifeste`.
- Produces: `verifier(racine: Path) -> dict`, `creer_zip(racine: Path, destination: Path) -> Path`.

- [ ] **Step 1: Écrire les tests**

`tests/test_verification.py` :

```python
import zipfile

from extracteur.manifeste import Manifeste
from extracteur.verification import creer_zip, verifier


def test_verification_sur_archive_coherente(tmp_path):
    fichier = tmp_path / "a.pdf"
    fichier.write_bytes(b"abc")
    Manifeste(tmp_path).ajouter("a.pdf", 3, "x", "/contenu/a.pdf")

    rapport = verifier(tmp_path)
    assert rapport["manquants"] == []
    assert rapport["taille_incorrecte"] == []
    assert rapport["inscrits"] == 1


def test_verification_detecte_un_fichier_manquant(tmp_path):
    Manifeste(tmp_path).ajouter("absent.pdf", 3, "x", "/contenu/absent.pdf")
    assert verifier(tmp_path)["manquants"] == ["absent.pdf"]


def test_verification_detecte_une_taille_incorrecte(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"ab")
    Manifeste(tmp_path).ajouter("a.pdf", 3, "x", "/contenu/a.pdf")
    assert verifier(tmp_path)["taille_incorrecte"] == ["a.pdf"]


def test_creation_du_zip(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"abc")
    destination = tmp_path.parent / "archive.zip"

    creer_zip(tmp_path, destination)

    assert destination.exists()
    with zipfile.ZipFile(destination) as archive:
        assert "a.pdf" in archive.namelist()
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `python -m pytest tests/test_verification.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'extracteur.verification'`

- [ ] **Step 3: Écrire l'implémentation**

`extracteur/verification.py` :

```python
"""Confrontation du disque au manifeste, puis mise en archive ZIP."""

import zipfile
from pathlib import Path

from extracteur.manifeste import Manifeste


def verifier(racine: Path) -> dict:
    """Recompte les fichiers reels face au manifeste avant de declarer l'archive terminee."""
    racine = Path(racine)
    entrees = Manifeste(racine).charger()

    manquants: list[str] = []
    taille_incorrecte: list[str] = []

    for relatif, ligne in entrees.items():
        chemin = racine / relatif
        if not chemin.exists():
            manquants.append(relatif)
            continue
        if ligne.get("taille") and chemin.stat().st_size != int(ligne["taille"]):
            taille_incorrecte.append(relatif)

    return {
        "inscrits": len(entrees),
        "manquants": manquants,
        "taille_incorrecte": taille_incorrecte,
    }


def creer_zip(racine: Path, destination: Path) -> Path:
    racine = Path(racine)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for chemin in sorted(racine.rglob("*")):
            if chemin.is_file():
                archive.write(chemin, chemin.relative_to(racine))

    return destination
```

- [ ] **Step 4: Brancher la vérification sur le mode console**

Dans `extracteur/__main__.py`, remplacer la ligne d'affichage finale de `_un_seul_cours` par :

```python
    from extracteur.verification import verifier

    controle = verifier(destination)
    print(f"\nEcrits : {resultat.fichiers_ecrits}   Sautes : {resultat.fichiers_sautes}")
    print(f"Echecs : {len(resultat.echecs)}  -> {destination / '_rapport.html'}")
    print(
        f"Verification : {controle['inscrits']} inscrits, "
        f"{len(controle['manquants'])} manquants, "
        f"{len(controle['taille_incorrecte'])} de taille incorrecte"
    )
    return 0 if resultat.fichiers_ecrits or not resultat.echecs else 1
```

- [ ] **Step 5: Lancer toute la suite**

Run: `python -m pytest -v`
Expected: PASS, tous les tests.

- [ ] **Step 6: Commit**

```bash
git add extracteur/verification.py extracteur/__main__.py tests/test_verification.py
git commit -m "feat: verification finale face au manifeste et creation du ZIP"
```

---

## Ordre d'exécution recommandé

Les tâches 1 à 6 ne dépendent d'aucun accès à monPortail : elles peuvent être faites d'un trait, hors ligne. La tâche 7 exige une connexion réelle. Les tâches 8 à 10 se testent hors ligne avec des doublures. Les tâches 11 à 13 demandent à nouveau un accès réel.

Compte tenu de l'échéance du 1er novembre 2026 : dès la tâche 11 terminée et vérifiée sur un cours ancien, **lancer un archivage complet même sans l'interface graphique**. La tâche 12 est du confort ; l'archive est le but. Le mode console fait déjà le travail.
