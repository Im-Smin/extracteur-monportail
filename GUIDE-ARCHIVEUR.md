# Archiveur monPortail (terminal)

Une interface en terminal par-dessus le moteur de l'extracteur (l'interface fenêtre d'origine reste disponible : `python -m extracteur`) : tu choisis **quels cours** et **quel contenu**, tu vois la progression et le temps restant, et un menu revient après chaque action. Les **vidéos** des capsules (lecteur JW Player) sont aussi archivées.

## Lancer

**Windows, sans rien installer :** télécharge `Archiveur-monPortail-terminal.exe` sur la page [Releases](../../releases/latest), puis double-clique dessus. Le navigateur est inclus ; le premier lancement le prépare (environ 30 secondes). Windows peut afficher « Windows a protégé votre ordinateur » : l'exécutable n'est pas signé, clique sur *Informations complémentaires*, puis *Exécuter quand même*.

**Avec Python :** double-clique sur `Lancer-archiveur.bat` (la première fois, il installe ce qu'il faut).

**Dans un terminal**, dans le dossier du projet :

```
pip install -e .                      # une fois (et après chaque mise à jour du projet)
python -m playwright install chromium # une fois
python -m monportail
```

## Le menu principal

- **Archiver des cours** : tous tes anciens cours ou ceux que tu coches, puis le contenu :
  vidéos · documents déposés par le prof · pages des modules en PDF · évaluations (énoncés, rétroactions) · mes travaux remis · notes · plan de cours.
  Même contenu pour tous les cours, ou réglé cours par cours. Un seul passage par cours : les vidéos sont repérées pendant que les documents sont archivés.
- **Télécharger les vidéos déjà repérées** : apparaît quand des vidéos repérées ne sont pas encore téléchargées (choix « Plus tard », annulation, nouvelle sélection).
- **Voir l'état de mon archive** : par cours, vidéos téléchargées / repérées, fichiers archivés, remises, notes.
- **Vérifier l'archive** : chaque fichier inscrit au manifeste est-il là et complet ?
- **Créer un .zip de l'archive**.
- **Changer de dossier d'archive**.

## Où vont les fichiers

```
<Session>/<Cours>/
  Plan de cours/   Documents/   Pages/   Évaluations/   notes.csv     (moteur d'origine)
  Vidéos/<Module>/<Onglet>/01. Titre de la capsule.mp4                 (vidéos)
```

À la racine : `manifeste.csv` (tout ce qui a été archivé, vidéos comprises), `notes-tous-cours.csv`, `_rapport_archiveur.html` (échecs et remarques de la dernière action), `_inventaire_videos.json` (vidéos repérées).

## Annuler, reprendre

- **Ctrl+C** pendant une action ramène au menu. Ce qui est fini est gardé.
- Relancer une action ne retélécharge rien de déjà présent ; une vidéo interrompue reprend là où elle s'était arrêtée.
- Si la session monPortail expire, la fenêtre du navigateur revient pour que tu te reconnectes, puis le travail continue.

## La fenêtre du navigateur

Une fois connecté, elle est rangée hors de l'écran : elle travaille en arrière-plan et garde ta session. Ne la ferme pas. `--fenetre-visible` la garde à l'écran.

## Options

```
python -m monportail --destination "D:\Archive monPortail"   # sans demander le dossier
python -m monportail --paralleles 3                          # vidéos simultanées (défaut : 2)
python -m monportail --fenetre-visible
```

## Limites connues

- Seuls les anciens sites (sessions avant l'automne 2026) sont parcourus : les cours Brio ne ferment pas.
- Les vidéos YouTube/Vimeo intégrées par un prof ne sont pas téléchargées. Annonces et forums non plus.

Le matériel reste la propriété de tes enseignants : archive pour ton usage personnel, ne redistribue pas.

## Construire l'exécutable

L'exécutable est construit par GitHub Actions (`.github/workflows/terminal-executable.yml`) à chaque étiquette `v*`, et publié dans une Release. Pour le construire à la main, suivre la procédure du README avec `--interface terminal` :

```
python construction\construire.py --interface terminal --sortie C:\build\sortie
```
