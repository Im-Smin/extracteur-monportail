"""Course video inventory, and the page visits that build it."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from extracteur.ena import URL, Ena, NavigateurBloque
from extracteur.extraction import est_commande_adf, onglets_selectionnes_depuis_html
from extracteur.telechargement import SessionExpiree
from monportail import window
from monportail.discovery import ITEM_LOADED_SCRIPT, MUTE_SCRIPT, PLAYER_SCRIPT, PLAYLIST_SCRIPT, page_videos

INVENTORY_FILE = "_inventaire_videos.json"
INVENTORY_VERSION = 2

PAGE_WIDTH_SCRIPT = "() => Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0, 1280)"
PLAYER_READY_SCRIPT = """
() => typeof window.jwplayer !== 'function'
  || document.querySelectorAll('.jwplayer').length === 0
  || [...document.querySelectorAll('.jwplayer')].every((el) => { try { return jwplayer(el.id).getPlaylist(); } catch (e) { return true; } })
"""


@dataclass
class Video:
    url: str
    title: str
    duration: str
    original_name: str
    extension: str
    page_path: list[str]
    rank: int
    size: int | None = None

    @property
    def key(self) -> str:
        return urlsplit(self.url).path


@dataclass
class CourseVideos:
    site_id: str
    session_code: str
    session_label: str
    session_folder: str
    course_folder: str
    videos: list[Video] = field(default_factory=list)
    scanned: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.session_label} · {self.course_folder}"

    def total_size(self) -> int:
        return sum(v.size or 0 for v in self.videos)

    @classmethod
    def for_course(cls, course) -> "CourseVideos":
        return cls(course.id_site, course.session.code, course.session.libelle, course.session.dossier(), course.dossier())


@dataclass
class Inventory:
    courses: list[CourseVideos] = field(default_factory=list)
    date: str = ""

    def save(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.date = datetime.now().isoformat(timespec="seconds")
        data = {"version": INVENTORY_VERSION, "date": self.date, "courses": [asdict(c) for c in self.courses]}
        temporary = folder / (INVENTORY_FILE + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(folder / INVENTORY_FILE)

    @classmethod
    def load(cls, folder: Path) -> "Inventory":
        path = folder / INVENTORY_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != INVENTORY_VERSION:
                return cls()
            courses = []
            for raw in data["courses"]:
                videos = [Video(**v) for v in raw.pop("videos", [])]
                courses.append(CourseVideos(**raw, videos=videos))
            return cls(courses, data.get("date", ""))
        except (OSError, ValueError, TypeError, KeyError):
            return cls()

    def find(self, site_id: str) -> CourseVideos | None:
        return next((c for c in self.courses if c.site_id == site_id), None)

    def get_or_add(self, course) -> CourseVideos:
        record = self.find(course.id_site)
        if record is None:
            record = CourseVideos.for_course(course)
            self.courses.append(record)
        return record


def module_folder(module) -> str:
    rank = (module.rang or 0) + 1
    return f"{rank:02d} - {module.titre}" if module.titre else f"Module {rank:02d}"


class VideoAwareEna(Ena):
    """Ena that also records the videos of every page it visits.

    Recording is on while `captures` is a list. Each capture keeps its context
    (the current module, a section label, or None for the course home page),
    so videos can be found during the document pass without extra visits.
    """

    def __init__(self, session, imprimer=print):
        super().__init__(session, imprimer=imprimer)
        self.captures: list[tuple] | None = None
        self.context = None
        self.known_items: set[str] = set()
        self.missing: list[str] = []
        self.hide_window = False
        self._hidden_page = None
        self.printed_pages = 0
        try:
            session.contexte.add_init_script(MUTE_SCRIPT)
        except Exception:  # noqa: BLE001
            pass

    def start_course(self, record_videos: bool) -> None:
        self.captures = [] if record_videos else None
        self.context = None
        self.known_items = set()

    def pages_du_module(self, module):
        self.context = module
        return super().pages_du_module(module)

    def fichiers_du_module(self, module, id_page=None):
        self.context = module
        if id_page is None:
            return super().fichiers_du_module(module)
        return super().fichiers_du_module(module, id_page)

    def _visiter(self, chemin: str) -> str:
        html = super()._visiter(chemin)
        if self.hide_window and self.session.page is not self._hidden_page:
            # A repaired page opens a new window.
            if not window.hide(self.session):
                self.hide_window = False
            self._hidden_page = self.session.page
        if self.captures is not None:
            videos = self.videos_on_page(html)
            self.captures.append((html, videos, list(self.missing), self.context))
        return html

    def capturer_pdf_page_courante(self, destination: Path) -> None:
        # Course pages are wider than A4; print at the real page width so nothing is cut off.
        self._assurer_authentifie()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._sous_garde(f"impression PDF de {destination.name}"):
            page = self.session.page
            page.emulate_media(media="screen")
            width = page.evaluate(PAGE_WIDTH_SCRIPT)
            margin = "16px"
            page.pdf(
                path=str(destination),
                width=f"{width}px",
                height=f"{round(width * 1.4142)}px",
                print_background=True,
                margin={"top": margin, "bottom": margin, "left": margin, "right": margin},
            )
        self.printed_pages += 1

    def _read_player(self) -> list:
        page = self.session.page
        try:
            page.wait_for_function(PLAYER_READY_SCRIPT, timeout=8000)
        except PlaywrightError:
            pass
        return page.evaluate(PLAYER_SCRIPT)

    def videos_on_page(self, html: str):
        """Every video on the open page, including each capsule of a playlist.

        The player only knows the capsule on display, so each playlist title
        (a read-only navigation link) is clicked until the player loads it.
        """
        page = self.session.page
        results: list = []
        self.missing = []
        try:
            page.evaluate(MUTE_SCRIPT)
        except PlaywrightError:
            pass
        try:
            with self._sous_garde("lecture du lecteur vidéo"):
                results = list(self._read_player())
        except PlaywrightError:
            pass

        try:
            playlist = page.evaluate(PLAYLIST_SCRIPT)
        except PlaywrightError:
            playlist = []
        for entry in playlist:
            item = entry["item"]
            if item in self.known_items or any(f"/item{item}/" in (r.get("url") or "") for r in results):
                continue
            title = entry.get("title") or f"item {item}"
            try:
                with self._sous_garde(f"ouverture de la capsule {title}"):
                    link = page.locator(f'div.Item_AudioVideo:has(img[src*="/item{item}/"]) a.Item_AudioVideo_Titre').first
                    if est_commande_adf(link.get_attribute("id", timeout=5000)):
                        raise PlaywrightError("lien de commande ADF ignoré par sécurité")
                    link.click(timeout=10_000)
                    page.wait_for_function(ITEM_LOADED_SCRIPT, arg=item, timeout=20_000)
                    results += self._read_player()
            except NavigateurBloque:
                self.missing.append(title)
                break
            except PlaywrightError:
                self.missing.append(title)

        videos = page_videos(html, results)
        self.known_items.update(v.item for v in videos if v.item)
        return videos


def _page_path(html: str, context) -> list[str]:
    if context is None:
        return ["Accueil"]
    if isinstance(context, str):
        return [context]
    return [module_folder(context), *[tab.titre for tab in onglets_selectionnes_depuis_html(html)]]


def collect_captures(record: CourseVideos, captures: list[tuple]) -> None:
    seen = {v.key for v in record.videos}
    for html, found, missing, context in captures:
        path = _page_path(html, context)
        for video in found:
            if video.key in seen:
                continue
            seen.add(video.key)
            record.videos.append(Video(video.url, video.title, video.duration, video.original_name,
                                       video.extension, path, len(record.videos) + 1))
        for title in missing:
            message = f"capsule « {title} » ({' › '.join(path)}) : le lecteur ne l'a pas chargée"
            if message not in record.errors:
                record.errors.append(message)
    found_titles = {v.title for v in record.videos if v.title}
    record.errors = [e for e in record.errors if not any(e.startswith(f"capsule « {t} »") for t in found_titles)]


def scan_course(ena: VideoAwareEna, course, record: CourseVideos, on_step=lambda text: None) -> None:
    """Find a course's videos without archiving anything else."""
    record.videos, record.errors = [], []
    ena.start_course(record_videos=True)
    try:
        on_step("Accueil")
        try:
            ena._visiter(URL.accueil(course.id_site))
            ena._assurer_authentifie()
        except SessionExpiree:
            raise
        except Exception as error:  # noqa: BLE001
            record.errors.append(f"accueil : {error}")

        try:
            modules = ena.modules(course)
        except SessionExpiree:
            raise
        except Exception as error:  # noqa: BLE001
            record.errors.append(f"liste des modules : {error}")
            modules = []

        for module in modules:
            on_step(f"Module : {module.titre or module.id_module}")
            try:
                ena.pages_du_module(module)
            except SessionExpiree:
                raise
            except Exception as error:  # noqa: BLE001
                record.errors.append(f"module « {module.titre} » : {error}")

        if not modules:
            def visit_section(label, html):
                ena.captures.append((html, ena.videos_on_page(html), list(ena.missing), label))

            try:
                ena.parcourir_menu(course, visit_section)
            except SessionExpiree:
                raise
            except Exception as error:  # noqa: BLE001
                record.errors.append(f"menu du site : {error}")
    finally:
        captures = ena.captures or []
        ena.start_course(record_videos=False)
    collect_captures(record, captures)
    record.scanned = True
