"""Video file layout, selection and parallel downloads."""

import queue
import threading
import time
from pathlib import Path

import questionary
from rich.panel import Panel
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

from extracteur.auth import est_page_authentifiee
from extracteur.ena import BASE, URL
from extracteur.nommage import nom_sur, tronquer
from monportail import transfer, window
from monportail.inventory import CourseVideos, Video, VideoAwareEna
from monportail.report import CANCELLED, DOWNLOADED, FAILED, PRESENT
from monportail.ui import STYLE, ask, console, human_size, safe_name

VIDEOS_FOLDER = "Vidéos"
KEEP_ALIVE_S = 240
MAX_SESSION_RETRIES = 3


def video_paths(root: Path, course: CourseVideos) -> dict[str, Path]:
    """<session>/<course>/Vidéos/<module>/<tab>/<title>.mp4, stable across runs so .part files resume."""
    base = root / safe_name(course.session_folder) / safe_name(course.course_folder) / VIDEOS_FOLDER
    taken: set[str] = set()
    paths = {}
    for video in course.videos:
        folder = base.joinpath(*map(safe_name, video.page_path))
        title = video.title or video.original_name or f"video {video.rank}"
        if not title[:1].isdigit():
            title = f"{video.rank:02d} - {title}"
        stem, extension = tronquer(nom_sur(f"{title}.{video.extension}"), 90).rsplit(".", 1)
        name, n = f"{stem}.{extension}", 2
        while str(folder / name).lower() in taken:
            name, n = f"{stem} ({n}).{extension}", n + 1
        taken.add(str(folder / name).lower())
        paths[video.key] = folder / name
    return paths


def measure_sizes(session, course: CourseVideos) -> None:
    jar = session._cookiejar()
    for video in course.videos:
        if video.size is None:
            try:
                video.size = transfer.remote_size(lambda: jar, video.url)
            except transfer.SessionLost:
                pass


def _video_label(video: Video) -> str:
    parts = [f"{' › '.join(video.page_path)} › {video.title or video.original_name}"]
    if video.duration:
        parts.append(video.duration)
    parts.append(human_size(video.size))
    return " · ".join(parts)


def select_videos(courses: list[CourseVideos], manual: bool) -> list[tuple[CourseVideos, Video]]:
    selected = []
    for course in courses:
        if not course.videos:
            continue
        if not manual:
            selected += [(course, v) for v in course.videos]
            continue
        console.print()
        choice = ask(questionary.select(
            f"{course.label} — {len(course.videos)} vidéo(s), {human_size(course.total_size())}",
            choices=[
                questionary.Choice("Tout garder", "all"),
                questionary.Choice("Choisir les vidéos une par une", "pick"),
                questionary.Choice("Ignorer ce cours", "skip"),
            ],
            style=STYLE,
        ))
        if choice == "all":
            selected += [(course, v) for v in course.videos]
        elif choice == "pick":
            kept = set(ask(questionary.checkbox(
                "Décoche ce que tu ne veux pas (Espace = cocher/décocher, a = tout, i = inverser, Entrée = valider)",
                choices=[questionary.Choice(_video_label(v), v.key, checked=True) for v in course.videos],
                style=STYLE,
            )))
            selected += [(course, v) for v in course.videos if v.key in kept]
    return selected


def reconnect(session, ena: VideoAwareEna, echo) -> bool:
    try:
        session.page.goto(f"{BASE}/ena/site/accueil?idSite=0", wait_until="domcontentloaded", timeout=60_000)
        session.page.wait_for_timeout(3000)
        if est_page_authentifiee(session.page) and "microsoftonline" not in session.page.url:
            return True
    except Exception:  # noqa: BLE001
        pass
    echo("[bold yellow]La session monPortail a expiré. Reconnecte-toi dans la fenêtre du navigateur (5 minutes max).[/]")
    window.show(session)
    if not session.attendre_connexion(imprimer=lambda text: echo(f"[dim]{text}[/]")):
        return False
    if ena.hide_window and window.hide(session):
        ena._hidden_page = session.page
    window.focus_terminal()
    echo("[green]Reconnecté, ça reprend.[/]")
    return True


class VideoDownloader:
    def __init__(self, session, ena: VideoAwareEna, workers: int):
        self.session = session
        self.ena = ena
        self.workers = max(1, workers)
        self.jar = session._cookiejar()
        self.cancel = threading.Event()
        self.session_ok = threading.Event()
        self.session_ok.set()
        self.lock = threading.Lock()

    def _keep_alive(self, site_id: str) -> None:
        try:
            self.session.page.request.get(f"{BASE}{URL.accueil(site_id)}", timeout=30_000)
        except Exception:  # noqa: BLE001
            pass
        self.jar = self.session._cookiejar()

    def run(self, jobs: list[tuple[CourseVideos, Video, Path]]) -> dict[int, tuple[str, str]]:
        results: dict[int, tuple[str, str]] = {}
        session_failures: dict[int, int] = {}
        pending: queue.Queue = queue.Queue()
        for index in range(len(jobs)):
            pending.put(index)

        known_total = sum(v.size or 0 for _, v, _ in jobs)
        progress = Progress(
            TextColumn("[bold]{task.description}"), BarColumn(bar_width=None), TextColumn("{task.percentage:>5.1f}%"),
            DownloadColumn(), TransferSpeedColumn(), TextColumn("· reste"), TimeRemainingColumn(compact=True),
            console=console, refresh_per_second=4, speed_estimate_period=60,
        )
        overall = progress.add_task(f"Total 0/{len(jobs)}", total=known_total or None)
        finished = [0]

        def mark_finished():
            with self.lock:
                finished[0] += 1
                progress.update(overall, description=f"Total {finished[0]}/{len(jobs)}")

        def worker():
            while not self.cancel.is_set():
                try:
                    index = pending.get_nowait()
                except queue.Empty:
                    return
                _, video, destination = jobs[index]
                if transfer.is_complete(destination, video.size):
                    progress.advance(overall, video.size or 0)
                    results[index] = (PRESENT, "")
                    mark_finished()
                    continue
                name = destination.name if len(destination.name) <= 48 else destination.name[:45] + "…"
                task = progress.add_task(f"  {name}", total=video.size)
                size_counted = [video.size is not None]

                def on_progress(delta, task=task):
                    progress.advance(task, delta)
                    progress.advance(overall, delta)

                def on_total(total, task=task, counted=size_counted):
                    progress.update(task, total=total)
                    if not counted[0]:
                        counted[0] = True
                        with self.lock:
                            progress.update(overall, total=(progress.tasks[overall].total or 0) + total)

                try:
                    transfer.download(lambda: self.jar, video.url, destination, video.size, on_progress, self.cancel, on_total)
                    results[index] = (DOWNLOADED, "")
                    mark_finished()
                except transfer.Cancelled:
                    results[index] = (CANCELLED, "reprendra au prochain lancement")
                except transfer.SessionLost as error:
                    progress.advance(overall, -progress.tasks[task].completed)
                    session_failures[index] = session_failures.get(index, 0) + 1
                    if session_failures[index] >= MAX_SESSION_RETRIES:
                        results[index] = (FAILED, f"accès refusé : {error}")
                        mark_finished()
                    else:
                        self.session_ok.clear()
                        pending.put(index)
                        while not self.session_ok.wait(0.5) and not self.cancel.is_set():
                            pass
                except transfer.DownloadFailed as error:
                    results[index] = (FAILED, str(error))
                    mark_finished()
                except Exception as error:  # noqa: BLE001
                    results[index] = (FAILED, f"{type(error).__name__} : {error}")
                    mark_finished()
                finally:
                    progress.remove_task(task)

        console.print(Panel.fit(
            f"{len(jobs)} vidéo(s) · {human_size(known_total)} · {self.workers} téléchargement(s) en parallèle\n"
            "[dim]Ctrl+C pour annuler : ce qui est fini est gardé, la vidéo en cours reprendra au prochain lancement.\n"
            "La fenêtre du navigateur doit rester ouverte : elle garde ta session active.[/]",
            title="Téléchargement des vidéos",
        ))
        last_ping = time.monotonic()
        ping_site = jobs[0][0].site_id if jobs else "0"
        with progress:
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(self.workers)]
            for thread in threads:
                thread.start()
            try:
                while any(t.is_alive() for t in threads):
                    time.sleep(0.5)
                    if not self.session_ok.is_set():
                        if reconnect(self.session, self.ena, progress.console.print):
                            self.jar = self.session._cookiejar()
                        else:
                            progress.console.print("[red]Reconnexion impossible : arrêt. Relance pour reprendre.[/]")
                            self.cancel.set()
                        self.session_ok.set()
                        last_ping = time.monotonic()
                    elif time.monotonic() - last_ping > KEEP_ALIVE_S:
                        self._keep_alive(ping_site)
                        last_ping = time.monotonic()
            except KeyboardInterrupt:
                progress.console.print("[yellow]Annulation demandée…[/]")
                self.cancel.set()
                for thread in threads:
                    thread.join(timeout=30)
        for index in range(len(jobs)):
            results.setdefault(index, (CANCELLED, "pas commencée"))
        return results
