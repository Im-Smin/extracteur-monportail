"""End-of-run report: an HTML page to read and a CSV for spreadsheets."""

import csv
import html
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from monportail.ui import human_size

CSV_NAME = "_videos.csv"
HTML_NAME = "_rapport_archiveur.html"

DOWNLOADED, PRESENT, FAILED, CANCELLED, SKIPPED = "downloaded", "present", "failed", "cancelled", "skipped"
STATUS_LABELS = {
    DOWNLOADED: "téléchargée",
    PRESENT: "déjà présente",
    FAILED: "échec",
    CANCELLED: "annulée",
    SKIPPED: "ignorée",
}

STYLE = """
body{font-family:system-ui,sans-serif;margin:24px;color:#222} h1{font-size:22px}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#f3f3f3;position:sticky;top:0}
tr.failed{background:#fde2e2} tr.cancelled{background:#fff4d6} tr.skipped{color:#888}
li{margin:4px 0}
"""

VIDEO_COLUMNS = ("Statut", "Session", "Cours", "Page", "Titre", "Durée", "Taille", "Fichier", "Détail")


@dataclass
class VideoRow:
    status: str
    session: str
    course: str
    page: str
    title: str
    duration: str
    size: int | None
    file: str
    url: str
    detail: str = ""


def _e(value) -> str:
    return html.escape(str(value or ""))


def _table(headers, rows) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    return f"<table><tr>{head}</tr>{''.join(rows)}</table>"


def _video_row(row: VideoRow) -> str:
    cells = (STATUS_LABELS.get(row.status, row.status), row.session, row.course, row.page, row.title,
             row.duration, human_size(row.size), row.file, row.detail)
    return f"<tr class='{row.status}'>" + "".join(f"<td>{_e(c)}</td>" for c in cells) + "</tr>"


def write(folder: Path, videos: list[VideoRow], notes: list[str], failures: list | None = None, summary: str = "") -> Path:
    failures = failures or []
    folder.mkdir(parents=True, exist_ok=True)

    with open(folder / CSV_NAME, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["statut", "session", "cours", "page", "titre", "duree", "taille_octets", "fichier", "detail", "url"])
        for r in videos:
            writer.writerow([STATUS_LABELS.get(r.status, r.status), r.session, r.course, r.page, r.title,
                             r.duration, r.size or "", r.file, r.detail, r.url])

    counts: dict[str, int] = {}
    for r in videos:
        counts[r.status] = counts.get(r.status, 0) + 1
    video_summary = " · ".join(f"{n} {STATUS_LABELS.get(s, s)}" for s, n in sorted(counts.items()))
    problems = [r for r in videos if r.status in (FAILED, CANCELLED)]

    sections = [f"<h1>Rapport archiveur monPortail</h1><p>Généré le {datetime.now():%Y-%m-%d %H:%M}."
                + (f" Documents : {_e(summary)}." if summary else "")
                + (f" Vidéos : {_e(video_summary)}." if videos else "") + "</p>"]
    if failures:
        rows = [f"<tr class='failed'><td>{_e(f.cours)}</td><td>{_e(f.element)}</td><td>{_e(f.cause)}</td><td>{_e(f.url)}</td></tr>"
                for f in failures]
        sections.append(f"<h2>Documents, pages et évaluations : échecs ({len(failures)})</h2>"
                        + _table(("Cours", "Élément", "Cause", "URL"), rows))
    if notes:
        sections.append("<h2>À regarder</h2><ul>" + "".join(f"<li>{_e(n)}</li>" for n in notes) + "</ul>")
    if videos:
        if problems:
            sections.append(f"<h2>Vidéos : problèmes ({len(problems)})</h2>" + _table(VIDEO_COLUMNS, map(_video_row, problems)))
        sections.append(f"<h2>Vidéos ({len(videos)})</h2>" + _table(VIDEO_COLUMNS, map(_video_row, videos)))

    page = (f"<!doctype html><html lang='fr'><head><meta charset='utf-8'><title>Rapport archiveur monPortail</title>"
            f"<style>{STYLE}</style></head><body>{''.join(sections)}</body></html>")
    path = folder / HTML_NAME
    path.write_text(page, encoding="utf-8")
    return path
