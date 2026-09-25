"""Confrontation du disque au manifeste, puis mise en archive ZIP.

Derniere etape avant de declarer une archive terminee : verifier() recompte
les fichiers reellement presents face a ce que le manifeste dit avoir ecrit,
et creer_zip() rassemble le tout dans un fichier unique.
"""

import contextlib
import os
import zipfile
from pathlib import Path

from extracteur.manifeste import Manifeste
from extracteur.nommage import PREFIXE_CHEMIN_LONG, chemin_long


def _taille_attendue(ligne: dict) -> int | None:
    """Parse la colonne taille d'une entree de manifeste, ou None si illisible.

    Meme precaution que archiveur._taille_manifeste : le manifeste est
    toujours ecrit par Manifeste.ajouter en usage normal, mais reste un
    fichier CSV ouvrable et modifiable a la main. Une colonne vide ou non
    numerique ne doit jamais faire planter la verification -- elle rend
    seulement cette taille incontrolable, jamais une anomalie en soi.
    """
    try:
        return int(ligne["taille"])
    except (KeyError, TypeError, ValueError):
        return None


def verifier(racine: Path) -> dict:
    """Recompte les fichiers reels face au manifeste avant de declarer
    l'archive terminee.

    Rend un dictionnaire avec le nombre d'entrees inscrites au manifeste, la
    liste des chemins relatifs absents du disque, et celle dont la taille sur
    disque ne correspond pas a celle consignee. Une taille illisible n'entre
    ni dans l'une ni dans l'autre : elle est simplement hors de portee du
    controle, pas une anomalie constatee.

    CRITIQUE : seules les entrees de statut "ok" sont controlees. Une
    ressource ignoree (video, lien externe, traceur inconnu -- voir
    Fichier.genre) n'a jamais ete ecrite sur disque ; sans ce filtre, chacune
    serait comptee comme un fichier manquant, et une archive parfaitement
    saine serait annoncee incomplete.
    """
    racine = Path(racine)
    entrees = Manifeste(racine).charger()

    manquants: list[str] = []
    taille_incorrecte: list[str] = []

    for relatif, ligne in entrees.items():
        if ligne.get("statut") != "ok":
            continue

        chemin_str = chemin_long(racine / relatif)
        if not os.path.exists(chemin_str):
            manquants.append(relatif)
            continue

        taille_attendue = _taille_attendue(ligne)
        if taille_attendue is not None and os.stat(chemin_str).st_size != taille_attendue:
            taille_incorrecte.append(relatif)

    return {
        "inscrits": len(entrees),
        "manquants": manquants,
        "taille_incorrecte": taille_incorrecte,
    }


def _sans_prefixe_long(chemin: str) -> str:
    if chemin.startswith(PREFIXE_CHEMIN_LONG):
        return chemin[len(PREFIXE_CHEMIN_LONG) :]
    return chemin


def _normaliser(chemin) -> str:
    """Forme comparable d'un chemin, prefixe \\\\?\\ ou non, absolu ou non.

    Sert uniquement a reconnaitre si le fichier ZIP en cours d'ecriture se
    trouve lui-meme dans l'arborescence qu'on est en train de compresser.
    Purement textuel (os.path.abspath ne touche jamais le disque) : reste
    valide sur un chemin qui n'existe pas encore, comme la destination du
    ZIP au moment ou on la compare.
    """
    return os.path.normcase(os.path.abspath(_sans_prefixe_long(str(chemin))))


# Formats deja compresses : les recompresser ne fait rien gagner. Mesure sur une
# archive reelle de 7,6 Go dont ces formats forment 95 % du volume : DEFLATE
# tourne a 17 Mo/s pour ramener la taille a 90,7 %, le simple stockage a
# 258 Mo/s pour 100 %. Soit une demi-heure de compression contre deux minutes,
# pour economiser 9 % de place -- et une fenetre qui paraissait plantee
# pendant tout ce temps.
EXTENSIONS_DEJA_COMPRESSEES = frozenset(
    {
        # documents bureautiques (conteneurs ZIP) et PDF
        ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".pbix",
        # archives
        ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar",
        # medias
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp3", ".mp4", ".m4a",
        ".mov", ".avi", ".mkv", ".wmv", ".webm",
    }
)

# Frequence des messages de progression : assez rare pour ne pas inonder le
# journal (plusieurs milliers de fichiers), assez frequente pour qu'on voie
# que le travail avance.
INTERVALLE_PROGRESSION_FICHIERS = 200


class CompressionInterrompue(Exception):
    """La compression a ete arretee a la demande de l'utilisateur. Le fichier
    partiel a deja ete supprime : rien d'incomplet n'est laisse sur le disque."""


def _mode_de_compression(nom: str) -> int:
    if os.path.splitext(nom)[1].lower() in EXTENSIONS_DEJA_COMPRESSEES:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


def _fichiers_a_compresser(racine_longue: str, exclus: set) -> list:
    """Inventaire complet avant d'ecrire le moindre octet : il donne le total
    qui rend la progression lisible (« 1200 / 4184 fichiers »), au prix d'un
    parcours de disque de quelques secondes."""
    fichiers = []
    for dossier_courant, sous_dossiers, noms in os.walk(racine_longue):
        sous_dossiers.sort()
        for nom in sorted(noms):
            chemin_absolu = os.path.join(dossier_courant, nom)
            if _normaliser(chemin_absolu) in exclus:
                continue
            fichiers.append((chemin_absolu, os.path.getsize(chemin_absolu)))
    return fichiers


def creer_zip(
    racine: Path,
    destination: Path,
    progression=None,
    annulation=None,
) -> Path:
    """Compresse toute l'archive en un seul fichier ZIP, arborescence
    relative preservee.

    Precautions, chacune couteuse a l'usage si on l'oublie :
    - chemin_long est applique a chaque operation disque, au meme titre que
      dans stockage.ecrire_flux : un dossier archive de plusieurs mois
      depasse facilement la limite de 260 caracteres de Windows.
    - zipfile.ZipFile.write() lit chaque fichier par blocs : une archive de
      plusieurs gigaoctets ne fait jamais gonfler le processus.
    - `destination` est exclue de son propre contenu, ainsi que tout ancien
      ZIP du meme nom pose a la racine de l'archive : sans cela, un ZIP
      precedent serait avale par le suivant, doublant sa taille a chaque
      execution.
    - les formats deja compresses sont stockes tels quels (voir
      EXTENSIONS_DEJA_COMPRESSEES) : quinze fois plus rapide, pour une
      taille quasi identique.
    - ecriture ATOMIQUE, dans un fichier .part renomme a la toute fin. Une
      compression tuee en cours de route -- fenetre fermee, machine
      eteinte -- ne laisse jamais un ZIP tronque qui porte le nom definitif
      et a l'air complet : c'est exactement ce qu'avait produit un arret
      force de la version precedente.

    `progression`, si fourni, est appele avec (fichiers_faits,
    fichiers_total, octets_faits, octets_total) tous les
    INTERVALLE_PROGRESSION_FICHIERS fichiers, puis une derniere fois a la fin.

    `annulation`, un threading.Event facultatif, est lu entre deux fichiers.
    S'il est pose, le fichier partiel est supprime et CompressionInterrompue
    est levee : le bouton Arreter de la fenetre reste ainsi operant pendant
    la compression, qui peut durer plusieurs minutes.
    """
    racine = Path(racine)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    partiel = destination.with_name(destination.name + ".part")
    racine_longue = chemin_long(racine)
    exclus = {
        _normaliser(destination),
        _normaliser(partiel),
        _normaliser(racine / f"{racine.name}.zip"),
        _normaliser(racine / f"{racine.name}.zip.part"),
    }

    fichiers = _fichiers_a_compresser(racine_longue, exclus)
    total_fichiers = len(fichiers)
    total_octets = sum(taille for _chemin, taille in fichiers)
    octets_faits = 0

    try:
        with zipfile.ZipFile(chemin_long(partiel), "w", zipfile.ZIP_DEFLATED) as archive:
            for indice, (chemin_absolu, taille) in enumerate(fichiers, start=1):
                if annulation is not None and annulation.is_set():
                    raise CompressionInterrompue("compression arretee a la demande")
                relatif = os.path.relpath(chemin_absolu, racine_longue).replace("\\", "/")
                archive.write(chemin_absolu, relatif, compress_type=_mode_de_compression(relatif))
                octets_faits += taille
                if progression is not None and indice % INTERVALLE_PROGRESSION_FICHIERS == 0:
                    progression(indice, total_fichiers, octets_faits, total_octets)
    except BaseException:
        # Arret demande, disque plein, fichier illisible : dans tous les cas
        # on ne laisse rien de partiel derriere soi.
        with contextlib.suppress(OSError):
            os.remove(chemin_long(partiel))
        raise

    os.replace(chemin_long(partiel), chemin_long(destination))
    if progression is not None:
        progression(total_fichiers, total_fichiers, total_octets, total_octets)
    return destination
