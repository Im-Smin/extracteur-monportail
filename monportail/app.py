"""Main menu and workflows of the terminal archiver."""

from pathlib import Path

import questionary
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from extracteur.__main__ import ConnexionEchouee, _connecter
from extracteur.manifeste import Manifeste, consolider_notes
from extracteur.modele import Echec
from extracteur.telechargement import SessionExpiree
from monportail import banner, report, transfer, window
from monportail.archiving import CONTENT_TYPES, ContentArchiver
from monportail.downloads import VIDEOS_FOLDER, VideoDownloader, measure_sizes, reconnect, select_videos, video_paths
from monportail.inventory import CourseVideos, Inventory, VideoAwareEna, collect_captures, scan_course
from monportail.report import CANCELLED, DOWNLOADED, FAILED, PRESENT, STATUS_LABELS, VideoRow
from monportail.ui import (
    DEFAULT_FOLDER,
    LAST_LEGACY_SESSION,
    STYLE,
    ask,
    console,
    free_space,
    human_size,
    pick_folder,
    safe_name,
)


class BackToMenu(Exception):
    pass


class StatusProgress(Progress):
    """A progress bar followed by free-form status lines."""

    def __init__(self, *args, **kwargs):
        self.lines: list[Text] = [Text(""), Text("")]
        super().__init__(*args, **kwargs)

    def get_renderables(self):
        yield self.make_tasks_table(self.tasks)
        yield from self.lines


class EventLog:
    """Receives the original archiver's events and shows them live."""

    def __init__(self):
        self.progress = None
        self.course = ""
        self.last = ""

    def attach(self, progress: StatusProgress, archiver, ena) -> None:
        self.progress, self.archiver, self.ena = progress, archiver, ena

    def refresh(self, text: str | None = None) -> None:
        if self.progress is None:
            return
        if text is not None:
            self.last = text
        self.progress.lines[0] = Text.from_markup(f"  [bold]{self.course[:60]}[/] [dim]› {self.last[:70]}[/]")
        result = self.archiver.resultat
        videos = len({v.key for capture in (self.ena.captures or []) for v in capture[1]})
        self.progress.lines[1] = Text.from_markup(
            f"  [green]{result.fichiers_ecrits} fichier(s) archivé(s)[/] · {self.ena.printed_pages} page(s) en PDF"
            f" · {result.fichiers_sautes} déjà là"
            + (f" · [red]{len(result.echecs)} échec(s)[/]" if result.echecs else "")
            + (f" · [cyan]{videos} vidéo(s) repérée(s) dans ce cours[/]" if videos else "")
        )

    def put(self, event) -> None:
        kind, text = event
        if kind in ("visite", "fichier"):
            self.refresh(text)
        elif kind == "saute":
            self.refresh()
        elif kind == "echec":
            if self.progress is not None:
                self.progress.console.print(f"  [red]✗[/] {text}")
            self.refresh()


class App:
    def __init__(self, args):
        self.args = args
        self.session = None
        self.ena: VideoAwareEna | None = None
        self.folder: Path | None = Path(args.destination) if args.destination else None
        self.inventory = Inventory()
        self.courses: list = []
        self.notes: list[str] = []

    def run(self) -> int:
        banner.show(console)
        console.print(Panel.fit(
            "Archive tes anciens sites de cours avant leur fermeture le [bold]1er novembre 2026[/] :\n"
            "vidéos, documents des profs, pages, évaluations, travaux remis, notes, plans de cours.\n"
            "[dim]Ton propre compte, en lecture seule. Le matériel reste la propriété de tes profs : usage personnel seulement.[/]",
            border_style="#e4002b",
        ))
        self._open_folder(self.folder or pick_folder("Où ranger ton archive ?", DEFAULT_FOLDER))

        console.print("\n[bold]Connexion[/] : une fenêtre de navigateur va s'ouvrir. Connecte-toi dedans (MFA compris), puis reviens ici.")
        try:
            self.session = _connecter(session=window.BrowserSession())
        except ConnexionEchouee as error:
            console.print(f"[red]Connexion échouée : {error}[/]")
            return 1
        try:
            self.ena = VideoAwareEna(self.session, imprimer=lambda text: console.print(f"[dim]{text}[/]"))
            # The dashboard must stay rendered while it is read, so the window is only parked in a corner.
            if not self.args.fenetre_visible:
                window.park_in_corner(self.session)
                window.focus_terminal()
            self.courses, self.notes = self._list_courses()
            self._hide_browser()
            return self._menu()
        finally:
            self._save_inventory()
            self.session.fermer()

    def _open_folder(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.inventory = Inventory.load(folder)
        console.print(f"Dossier d'archive : [bold]{folder}[/]")

    def _save_inventory(self) -> None:
        try:
            self.inventory.save(self.folder)
        except OSError:
            pass

    def _hide_browser(self) -> None:
        if self.args.fenetre_visible:
            console.print("[dim]Laisse la fenêtre du navigateur travailler : n'y clique pas et ne la ferme pas.[/]")
            return
        if window.hide(self.session):
            self.ena.hide_window = True
            self.ena._hidden_page = self.session.page
            console.print("[dim]La fenêtre du navigateur est rangée hors de l'écran : elle travaille en arrière-plan. "
                          "Elle revient seulement si tu dois te reconnecter.[/]")
        else:
            console.print("[dim]Windows met en pause les fenêtres cachées sur ton ordinateur : la fenêtre du navigateur reste "
                          "dans le coin de l'écran. Ne la réduis pas, n'y clique pas et ne la ferme pas.[/]")
        window.focus_terminal()

    def _list_courses(self) -> tuple[list, list[str]]:
        notes, found = [], []
        with console.status("Lecture de tes sessions…"):
            sessions = self.ena.sessions_disponibles()
        sessions = sorted((s for s in sessions if s.code <= LAST_LEGACY_SESSION), key=lambda s: s.code)
        with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                      console=console, transient=True) as progress:
            task = progress.add_task("Lecture des cours", total=len(sessions))
            for session in sessions:
                progress.update(task, description=f"Lecture des cours : {session.libelle}")
                try:
                    for course in self.ena.sites_de_session(session):
                        if course.url_externe or not course.id_site:
                            notes.append(f"{session.libelle} · {course.dossier()} est hébergé hors monPortail (Brio) : non concerné par la fermeture.")
                        else:
                            found.append(course)
                except SessionExpiree:
                    raise
                except Exception as error:  # noqa: BLE001
                    notes.append(f"Session {session.libelle} illisible : {error}")
                    console.print(f"[yellow]Session {session.libelle} illisible : {error}[/]")
                progress.advance(task)
        console.print(f"[green]✓[/] {len(found)} anciens sites de cours trouvés sur {len(sessions)} sessions.")
        return found, notes

    # Menu

    def _pending_videos(self) -> list[tuple[CourseVideos, object, Path]]:
        pending = []
        for course in self.inventory.courses:
            paths = video_paths(self.folder, course)
            pending += [(course, v, paths[v.key]) for v in course.videos if not transfer.is_complete(paths[v.key], v.size)]
        return pending

    def _menu(self) -> int:
        actions = {
            "archive": self.archive,
            "videos": self.download_pending,
            "status": self.show_status,
            "verify": self.verify,
            "zip": self.make_zip,
            "folder": self.change_folder,
        }
        while True:
            pending = self._pending_videos()
            choices = [questionary.Choice("Archiver des cours  (choisir les cours et le contenu)", "archive")]
            if pending:
                size = sum(v.size or 0 for _, v, _ in pending)
                choices.append(questionary.Choice(f"Télécharger les vidéos déjà repérées  ({len(pending)} en attente, {human_size(size)})", "videos"))
            choices += [
                questionary.Choice("Voir l'état de mon archive", "status"),
                questionary.Choice("Vérifier l'archive  (fichiers manquants ou incomplets)", "verify"),
                questionary.Choice("Créer un .zip de l'archive", "zip"),
                questionary.Choice(f"Changer de dossier d'archive  (actuel : {self.folder})", "folder"),
                questionary.Choice("Quitter", "quit"),
            ]
            console.print()
            console.rule("[bold #e4002b]Menu principal[/]")
            try:
                action = ask(questionary.select("Que veux-tu faire ?", choices=choices, style=STYLE))
            except KeyboardInterrupt:
                action = "quit"
            if action == "quit":
                console.print(f"À la prochaine ! [dim]Ton archive est dans[/] {self.folder}")
                return 0
            try:
                actions[action]()
            except (KeyboardInterrupt, BackToMenu):
                console.print("\n[yellow]Interrompu. Ce qui est fini est gardé.[/] Retour au menu.")
            except SessionExpiree:
                if not reconnect(self.session, self.ena, console.print):
                    console.print("[red]Reconnexion impossible. Relance le programme.[/]")
                    return 3
            except Exception as error:  # noqa: BLE001
                console.print_exception(max_frames=6)
                console.print(f"[red]Erreur inattendue : {error}[/] Retour au menu (le travail déjà fait est gardé).")
            finally:
                self._save_inventory()

    # Archiving

    def _pick_courses(self) -> list:
        mode = ask(questionary.select(
            "Quels cours ?",
            choices=[questionary.Choice(f"Tous mes anciens cours ({len(self.courses)})", "all"),
                     questionary.Choice("Je choisis les cours", "pick"),
                     questionary.Choice("← Retour", "back")],
            style=STYLE,
        ))
        if mode == "back":
            raise BackToMenu
        if mode == "all":
            return list(self.courses)
        choices, current = [], None
        for course in self.courses:
            if course.session.libelle != current:
                current = course.session.libelle
                choices.append(questionary.Separator(f"── {current} ──"))
            record = self.inventory.find(course.id_site)
            info = f"  ({len(record.videos)} vidéos repérées)" if record and record.scanned else ""
            choices.append(questionary.Choice(f"{course.session.libelle} · {course.dossier()}{info}", course.id_site))
        ids = set(ask(questionary.checkbox(
            "Coche les cours (Espace = cocher, a = tout, i = inverser, Entrée = valider)",
            choices=choices, style=STYLE, validate=lambda picked: True if picked else "Coche au moins un cours.",
        )))
        return [c for c in self.courses if c.id_site in ids]

    def _pick_content(self, courses: list) -> dict[str, set[str]]:
        types = set(ask(questionary.checkbox(
            "Quel contenu ? (tout est coché ; décoche ce que tu ne veux pas)",
            choices=[questionary.Choice(label, key, checked=True) for key, label in CONTENT_TYPES],
            style=STYLE, validate=lambda picked: True if picked else "Coche au moins un type de contenu.",
        )))
        plan = {c.id_site: set(types) for c in courses}
        if len(courses) > 1 and ask(questionary.select(
            "Même contenu pour tous ces cours ?",
            choices=[questionary.Choice("Oui, le même pour tous", False),
                     questionary.Choice("Non, je règle cours par cours", True)],
            style=STYLE,
        )):
            for course in courses:
                plan[course.id_site] = set(ask(questionary.checkbox(
                    f"{course.session.libelle} · {course.dossier()}",
                    choices=[questionary.Choice(label, key, checked=key in types) for key, label in CONTENT_TYPES],
                    style=STYLE,
                )))
        return {site: chosen for site, chosen in plan.items() if chosen}

    def archive(self) -> None:
        courses = self._pick_courses()
        plan = self._pick_content(courses)
        courses = [c for c in courses if c.id_site in plan]
        if not courses:
            console.print("[yellow]Rien à archiver.[/]")
            return

        already_scanned = [c for c in courses if plan[c.id_site] == {"videos"}
                           and (r := self.inventory.find(c.id_site)) and r.scanned]
        rescan = bool(already_scanned) and ask(questionary.select(
            f"{len(already_scanned)} cours ont déjà leurs vidéos repérées.",
            choices=[questionary.Choice("Réutiliser ce repérage (plus rapide)", False),
                     questionary.Choice("Refaire le repérage", True)],
            style=STYLE,
        ))

        summary = Table.grid(padding=(0, 2))
        for key, label in CONTENT_TYPES:
            count = sum(1 for c in courses if key in plan[c.id_site])
            if count:
                summary.add_row(f"[green]✓[/] {label}", f"[dim]{count} cours[/]")
        console.print(Panel(summary, title=f"{len(courses)} cours à archiver", expand=False))

        result, interrupted = self._archive_pass(courses, plan, rescan)
        video_rows = []
        with_videos = {c.id_site for c in courses if "videos" in plan[c.id_site]}
        if with_videos and not interrupted:
            records = [r for r in self.inventory.courses if r.site_id in with_videos and r.scanned]
            video_rows = self._offer_downloads(records)
        self._write_report(with_videos, result, video_rows)

    def _archive_pass(self, courses: list, plan: dict, rescan: bool):
        log = EventLog()
        archiver = ContentArchiver(self.ena, self.session.transport, self.folder, log)
        console.print(Panel.fit(
            "Le programme visite chaque page de chaque cours choisi (environ 2 à 5 minutes par cours selon le contenu).\n"
            "[dim]Ctrl+C pour arrêter : ce qui est fini est gardé, et relancer ne refait rien de déjà téléchargé.[/]",
            title="Archivage",
        ))
        interrupted = False
        printed_before = self.ena.printed_pages
        columns = (SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                   TextColumn("· écoulé"), TimeElapsedColumn(), TextColumn("· reste ≈"), TimeRemainingColumn())
        with StatusProgress(*columns, console=console) as progress:
            overall = progress.add_task("[bold]Cours", total=len(courses))
            log.attach(progress, archiver, self.ena)
            for course in courses:
                types = plan[course.id_site]
                record = self.inventory.get_or_add(course)
                log.course = f"{course.session.libelle} · {course.dossier()}"
                log.refresh("…")
                before = (archiver.resultat.fichiers_ecrits, archiver.resultat.fichiers_sautes, len(archiver.resultat.echecs),
                          self.ena.printed_pages)
                try:
                    self._archive_course_with_retry(archiver, course, record, types, rescan, log, progress)
                except KeyboardInterrupt:
                    interrupted = True
                    self.ena.start_course(record_videos=False)
                    progress.console.print("[yellow]Arrêt demandé.[/]")
                    break
                except SessionExpiree:
                    interrupted = True
                    progress.console.print("[red]Session expirée et reconnexion impossible : arrêt.[/]")
                    break
                except Exception as error:  # noqa: BLE001
                    archiver.resultat.echecs.append(Echec(cours=course.dossier(), element="(cours entier)", cause=str(error)))
                    progress.console.print(f"[red]✗ {course.dossier()} : {error}[/]")
                finally:
                    self._save_inventory()
                progress.console.print(self._course_summary(log.course, archiver, self.ena, before, types, record))
                progress.advance(overall)
            if any("grades" in plan[c.id_site] for c in courses):
                archiver._ecrire_synthese("(archive)", "notes-tous-cours.csv",
                                          lambda: consolider_notes(self.folder, self.folder / "notes-tous-cours.csv"))
        result = archiver.resultat
        console.print(Panel.fit(
            f"[green]{result.fichiers_ecrits}[/] fichier(s) archivé(s) · [green]{self.ena.printed_pages - printed_before}[/] page(s) en PDF · "
            f"{result.fichiers_sautes} déjà présent(s) · "
            + (f"[red]{len(result.echecs)} échec(s)[/]" if result.echecs else "aucun échec"),
            title="Archivage interrompu" if interrupted else "Archivage terminé",
        ))
        return result, interrupted

    @staticmethod
    def _course_summary(label: str, archiver, ena, before: tuple, types: set, record: CourseVideos) -> str:
        written = archiver.resultat.fichiers_ecrits - before[0]
        skipped = archiver.resultat.fichiers_sautes - before[1]
        failed = len(archiver.resultat.echecs) - before[2]
        printed = ena.printed_pages - before[3]
        parts = []
        if types - {"videos"}:
            parts.append(f"{written} fichier(s), {printed} page(s) en PDF" + (f", {skipped} déjà là" if skipped else ""))
        if "videos" in types:
            parts.append(f"{len(record.videos)} vidéo(s) repérée(s) ({human_size(record.total_size())})")
        line = f"  [green]✓[/] {label} : " + " · ".join(parts)
        if failed:
            line += f" [yellow]· {failed} échec(s)[/]"
        if "videos" in types and record.errors:
            line += f" [yellow]· {len(record.errors)} capsule(s)/page(s) non lue(s)[/]"
        return line

    def _archive_course_with_retry(self, archiver, course, record, types, rescan, log, progress) -> None:
        for attempt in range(2):
            try:
                self._archive_course(archiver, course, record, types, rescan, log)
                return
            except SessionExpiree:
                self.ena.start_course(record_videos=False)
                if attempt == 1 or not reconnect(self.session, self.ena, progress.console.print):
                    raise

    def _archive_course(self, archiver, course, record: CourseVideos, types: set, rescan: bool, log: EventLog) -> None:
        wants_videos = "videos" in types
        if types - {"videos"}:
            self.ena.start_course(record_videos=wants_videos)
            try:
                archiver.archive_course(course, types)
            finally:
                captures = self.ena.captures or []
                self.ena.start_course(record_videos=False)
            if wants_videos:
                record.videos, record.errors = [], []
                collect_captures(record, captures)
                record.scanned = True
        elif wants_videos and (rescan or not record.scanned):
            scan_course(self.ena, course, record, on_step=lambda text: log.put(("visite", text)))
        if wants_videos:
            log.put(("visite", "mesure de la taille des vidéos"))
            measure_sizes(self.session, record)

    # Videos

    def _offer_downloads(self, records: list[CourseVideos]) -> list[VideoRow]:
        records = [r for r in records if r.videos]
        if not records:
            console.print("[dim]Aucune vidéo repérée dans ces cours.[/]")
            return []
        count = sum(len(r.videos) for r in records)
        size = sum(r.total_size() for r in records)
        mode = ask(questionary.select(
            f"{count} vidéo(s) repérée(s), {human_size(size)}. Les télécharger ?",
            choices=[questionary.Choice("Tout télécharger", "all"),
                     questionary.Choice("Choisir cours par cours, vidéo par vidéo", "pick"),
                     questionary.Choice("Plus tard (elles restent repérées : menu principal)", "later")],
            style=STYLE,
        ))
        if mode == "later":
            return []
        return self._download(select_videos(records, mode == "pick"), records)

    def download_pending(self) -> None:
        pending = self._pending_videos()
        site_ids = {course.site_id for course, _, _ in pending}
        records = [r for r in self.inventory.courses if r.site_id in site_ids]
        table = Table(title="Vidéos repérées pas encore téléchargées")
        for header, justify in (("Cours", "left"), ("Vidéos", "right"), ("Taille", "right")):
            table.add_column(header, justify=justify)
        for record in records:
            videos = [v for course, v, _ in pending if course is record]
            table.add_row(record.label, str(len(videos)), human_size(sum(v.size or 0 for v in videos)))
        console.print(table)
        mode = ask(questionary.select(
            "Lesquelles ?",
            choices=[questionary.Choice("Toutes celles en attente", "all"),
                     questionary.Choice("Choisir cours par cours, vidéo par vidéo", "pick"),
                     questionary.Choice("← Retour", "back")],
            style=STYLE,
        ))
        if mode == "back":
            return
        if mode == "all":
            selected = [(course, v) for course, v, _ in pending]
        else:
            waiting = {(course.site_id, v.key) for course, v, _ in pending}
            subsets = [CourseVideos(r.site_id, r.session_code, r.session_label, r.session_folder, r.course_folder,
                                    videos=[v for v in r.videos if (r.site_id, v.key) in waiting], scanned=True)
                       for r in records]
            by_site = {r.site_id: r for r in records}
            selected = [(by_site[course.site_id], v) for course, v in select_videos(subsets, True)]
        rows = self._download(selected, records)
        self._write_report(set(), None, rows)

    def _download(self, selected, records: list[CourseVideos]) -> list[VideoRow]:
        if not selected:
            console.print("[yellow]Aucune vidéo sélectionnée.[/]")
            return []
        paths = {}
        for record in records:
            paths.update(video_paths(self.folder, record))
        jobs = [(course, video, paths[video.key]) for course, video in selected]
        remaining = sum(v.size or 0 for _, v, path in jobs if not transfer.is_complete(path, v.size))

        table = Table(title="Récapitulatif des vidéos")
        for header, justify in (("Cours", "left"), ("Vidéos", "right"), ("Taille", "right")):
            table.add_column(header, justify=justify)
        by_course: dict[str, list] = {}
        for course, video, _ in jobs:
            by_course.setdefault(course.label, []).append(video)
        for label, videos in by_course.items():
            table.add_row(label, str(len(videos)), human_size(sum(v.size or 0 for v in videos)))
        table.add_row("[bold]Total", f"[bold]{len(jobs)}", f"[bold]{human_size(sum(v.size or 0 for _, v, _ in jobs))}")
        console.print(table)
        free = free_space(self.folder)
        if free is not None:
            color = "red" if remaining > free else "green"
            console.print(f"Reste à télécharger : [bold]{human_size(remaining)}[/] · espace libre : [{color}]{human_size(free)}[/]")
            if remaining > free:
                console.print("[red bold]Pas assez d'espace disque ![/] Libère de la place ou change de dossier d'archive.")
        if not ask(questionary.confirm("Lancer le téléchargement ?", default=True, style=STYLE)):
            return []

        results = VideoDownloader(self.session, self.ena, self.args.paralleles).run(jobs)
        self._add_to_manifest(jobs, results)

        counts: dict[str, int] = {}
        for status, _ in results.values():
            counts[status] = counts.get(status, 0) + 1
        colors = {DOWNLOADED: "green", PRESENT: "green", FAILED: "red", CANCELLED: "yellow"}
        grid = Table.grid(padding=(0, 2))
        for status, count in sorted(counts.items()):
            grid.add_row(f"[{colors.get(status, 'white')}]{STATUS_LABELS[status]}[/]", str(count))
        console.print(Panel(grid, title="Vidéos", expand=False))
        if counts.get(CANCELLED):
            console.print("[yellow]Les vidéos annulées reprendront là où elles étaient[/] : menu principal → « Télécharger les vidéos déjà repérées ».")

        return [
            VideoRow(results[i][0], course.session_label, course.course_folder, " › ".join(video.page_path),
                     video.title or video.original_name, video.duration, video.size,
                     str(path.relative_to(self.folder)), video.url, results[i][1])
            for i, (course, video, path) in enumerate(jobs)
        ]

    def _add_to_manifest(self, jobs, results) -> None:
        # Listing videos in the original manifest lets "Vérifier l'archive" cover them too.
        try:
            manifest = Manifeste(self.folder)
            known = manifest.charger()
        except Exception:  # noqa: BLE001
            return
        for index, (_, video, path) in enumerate(jobs):
            if results[index][0] not in (DOWNLOADED, PRESENT):
                continue
            relative = str(path.relative_to(self.folder)).replace("\\", "/")
            if relative not in known:
                try:
                    manifest.ajouter(relative, path.stat().st_size, "", video.url)
                except OSError:
                    pass

    def _write_report(self, video_sites: set[str], result, video_rows: list[VideoRow]) -> None:
        notes = list(self.notes)
        for record in self.inventory.courses:
            if record.site_id in video_sites:
                notes += [f"{record.label} : {error}" for error in record.errors]
        summary = ""
        if result is not None:
            summary = f"{result.fichiers_ecrits} fichiers archivés, {result.fichiers_sautes} déjà présents, {len(result.echecs)} échecs"
        path = report.write(self.folder, video_rows, notes, result.echecs if result else [], summary)
        console.print(f"Rapport : [bold]{path}[/]")

    # Archive tools

    def show_status(self) -> None:
        try:
            entries = Manifeste(self.folder).charger()
        except Exception:  # noqa: BLE001
            entries = {}
        table = Table(title=f"État de l'archive · {self.folder}")
        for header, justify in (("Cours", "left"), ("Vidéos", "right"), ("Fichiers", "right"), ("Remises", "center"), ("Notes", "center")):
            table.add_column(header, justify=justify)
        downloaded_total = found_total = files_total = 0
        current = None
        for course in self.courses:
            if course.session.libelle != current:
                current = course.session.libelle
                table.add_row(f"[bold #e5a50a]{current}[/]", "", "", "", "")
            base = self.folder / safe_name(course.session.dossier()) / safe_name(course.dossier())
            prefix = str(base.relative_to(self.folder)).replace("\\", "/") + "/"
            files = sum(1 for path, entry in entries.items()
                        if path.startswith(prefix) and (entry.get("statut") or "ok") == "ok" and f"/{VIDEOS_FOLDER}/" not in path)
            record = self.inventory.find(course.id_site)
            if record and record.scanned:
                paths = video_paths(self.folder, record)
                done = sum(1 for v in record.videos if transfer.is_complete(paths[v.key], v.size))
                color = "green" if done == len(record.videos) else "yellow"
                videos = f"[{color}]{done}/{len(record.videos)}[/]"
                downloaded_total += done
                found_total += len(record.videos)
            else:
                videos = "[dim]non repérées[/]"
            submissions = "[green]✓[/]" if any(base.glob("Évaluations/*/depots.csv")) else "[dim]–[/]"
            grades = "[green]✓[/]" if (base / "notes.csv").exists() else "[dim]–[/]"
            files_total += files
            table.add_row(f"  {course.dossier()}", videos, str(files) if files else "[dim]0[/]", submissions, grades)
        table.add_row("[bold]Total", f"[bold]{downloaded_total}/{found_total}", f"[bold]{files_total}", "", "")
        console.print(table)
        console.print("[dim]Vidéos : téléchargées / repérées. Fichiers : documents, pages PDF, énoncés et remises inscrits au manifeste.[/]")

    def verify(self) -> None:
        from extracteur.verification import verifier

        with console.status("Vérification de chaque fichier de l'archive…"):
            check = verifier(self.folder)
        missing, incomplete = check["manquants"], check["taille_incorrecte"]
        if not missing and not incomplete:
            console.print(Panel.fit(f"[green]✓ Archive complète[/] : {check['inscrits']} fichiers vérifiés.", title="Vérification"))
            return
        table = Table(title=f"{len(missing)} manquant(s), {len(incomplete)} incomplet(s) sur {check['inscrits']}")
        table.add_column("Problème")
        table.add_column("Fichier")
        for path in missing[:200]:
            table.add_row("[red]manquant[/]", path)
        for path in incomplete[:200]:
            table.add_row("[yellow]incomplet[/]", path)
        console.print(table)
        console.print("[dim]Relance « Archiver des cours » sur les cours concernés : seuls les fichiers absents seront retéléchargés.[/]")

    def make_zip(self) -> None:
        from extracteur.verification import creer_zip

        if not ask(questionary.confirm(
            "Le .zip prendra à peu près autant de place que l'archive (les vidéos ne se compressent pas). Continuer ?",
            default=True, style=STYLE,
        )):
            return
        target = pick_folder("Où enregistrer le .zip ?", self.folder.parent) / f"{self.folder.name}.zip"
        with Progress(TextColumn("[bold]Compression"), BarColumn(), DownloadColumn(), TransferSpeedColumn(),
                      TimeRemainingColumn(), console=console) as progress:
            task = progress.add_task("", total=None)
            written = creer_zip(self.folder, target,
                                progression=lambda done, total, bytes_done, bytes_total: progress.update(task, completed=bytes_done, total=bytes_total))
        console.print(f"[green]✓[/] Zip : [bold]{written}[/]")

    def change_folder(self) -> None:
        self._save_inventory()
        self._open_folder(pick_folder("Nouveau dossier d'archive :", self.folder))
