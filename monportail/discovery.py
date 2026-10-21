"""Find the videos embedded in a monPortail course page."""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

BASE_URL = "https://sitescours.monportail.ulaval.ca"
VIDEO_TRACKER_PATH = "/analytique/evenement/multimedia.mp4"
VIDEO_EXTENSIONS = ("mp4", "m4v", "mov", "webm", "mkv", "avi", "mp3", "m4a", "wav")
IGNORED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".vtt", ".srt")

CONTENT_URL = re.compile(
    r"(?:https?://sitescours\.monportail\.ulaval\.ca)?"
    r"(/contenu/sitescours/[^\"'\s<>()\\]+?\.(?:" + "|".join(VIDEO_EXTENSIONS) + r")"
    r"(?:\?identifiant=[0-9a-fA-F]+)?)(?=[\"'\s<>()\\&]|$)",
    re.IGNORECASE,
)
ITEM = re.compile(r"/item(\d+)/")
SOURCE_FOLDER = re.compile(r"/dossier\.([^/]+?)/")
DURATION = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:minutes?|min|h)?\b", re.IGNORECASE)

PLAYER_SCRIPT = r"""
() => {
  const absolute = (u) => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const found = [];
  try {
    if (typeof window.jwplayer === 'function') {
      const seen = new Set();
      document.querySelectorAll('.jwplayer, [id^="jwplayer"], div[data-jwplayer-id]').forEach((el) => {
        if (!el.id || seen.has(el.id)) return;
        seen.add(el.id);
        let player = null;
        try { player = window.jwplayer(el.id); } catch (e) { return; }
        if (!player || typeof player.getPlaylist !== 'function') return;
        let playlist = [];
        try { playlist = player.getPlaylist() || []; } catch (e) { playlist = []; }
        playlist.forEach((item) => {
          const sources = (item.sources || []).map((s) => absolute(s.file)).filter(Boolean);
          const url = item.file ? absolute(item.file) : (sources[0] || null);
          if (url) found.push({source: 'jwplayer', url, sources, title: item.title || '', duration: item.duration || 0});
        });
      });
    }
  } catch (e) {}
  document.querySelectorAll('video').forEach((v) => {
    const src = v.currentSrc || v.getAttribute('src') || (v.querySelector('source') && v.querySelector('source').getAttribute('src'));
    if (src && !src.startsWith('blob:')) found.push({source: 'video', url: absolute(src), title: '', duration: v.duration || 0});
  });
  return found;
}
"""

# Each capsule listed under the player: a thumbnail whose URL holds item<N>, and a title link.
PLAYLIST_SCRIPT = r"""
() => [...document.querySelectorAll('div.Item_AudioVideo')].map((div) => {
  const img = div.querySelector('img[src*="/item"]');
  const match = img && img.getAttribute('src').match(/\/item(\d+)\//);
  const link = div.querySelector('a.Item_AudioVideo_Titre');
  return match ? {item: match[1], title: link ? link.textContent.trim() : ''} : null;
}).filter(Boolean)
"""

ITEM_LOADED_SCRIPT = r"""
(item) => {
  const key = '/item' + item + '/';
  const inPlayer = [...document.querySelectorAll('.jwplayer')].some((el) => {
    try {
      return (jwplayer(el.id).getPlaylist() || []).some((i) =>
        (i.file || '').includes(key) || (i.sources || []).some((s) => (s.file || '').includes(key)));
    } catch (e) { return false; }
  });
  return inPlayer || [...document.querySelectorAll('video')].some((v) => (v.currentSrc || v.src || '').includes(key));
}
"""

# Opening a capsule starts playback; only its URL matters, so keep everything silent.
MUTE_SCRIPT = r"""
(() => {
  const silence = (event) => {
    const media = event.target;
    if (media && /^(VIDEO|AUDIO)$/.test(media.tagName || '')) {
      try { media.muted = true; media.volume = 0; media.pause(); } catch (e) {}
    }
  };
  document.addEventListener('play', silence, true);
  document.addEventListener('playing', silence, true);
  document.addEventListener('volumechange', (event) => {
    const media = event.target;
    if (media && !media.muted) { try { media.muted = true; } catch (e) {} }
  }, true);
  document.querySelectorAll('video, audio').forEach((m) => { try { m.muted = true; m.pause(); } catch (e) {} });
})()
"""


@dataclass
class FoundVideo:
    url: str
    title: str = ""
    duration: str = ""
    source: str = ""

    @property
    def key(self) -> str:
        return urlsplit(self.url).path

    @property
    def item(self) -> str | None:
        match = ITEM.search(self.url)
        return match.group(1) if match else None

    @property
    def original_name(self) -> str:
        match = SOURCE_FOLDER.search(unquote(self.url))
        if match:
            return re.sub(r"\.(?:" + "|".join(VIDEO_EXTENSIONS) + r")$", "", match.group(1), flags=re.I)
        return unquote(urlsplit(self.url).path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]

    @property
    def extension(self) -> str:
        path = urlsplit(self.url).path.lower()
        return next((ext for ext in VIDEO_EXTENSIONS if path.endswith("." + ext)), "mp4")


def absolute_url(url: str) -> str:
    return urljoin(BASE_URL + "/", url)


def has_token(url: str) -> bool:
    return "identifiant=" in url


def format_duration(seconds) -> str:
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _tracker_target(href: str) -> str | None:
    match = re.search(r"[?&]url=([^&]+)", href)
    return unquote(match.group(1)) if match else None


def _clean(text: str) -> str:
    return " ".join(text.split())


def _split_duration(text: str) -> tuple[str, str]:
    match = DURATION.search(text)
    if not match:
        return text, ""
    title = _clean(text[: match.start()] + text[match.end():])
    title = re.sub(r"\b(minutes?|min)\b\s*$", "", title, flags=re.I).strip(" -·|")
    return title, match.group(1)


def playlist_entries(html: str) -> dict[str, dict]:
    """Title and duration of each capsule listed under a player, by item id."""
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for block in soup.select("div.Item_AudioVideo"):
        thumbnail = block.find("img", src=ITEM)
        link = block.find("a", class_="Item_AudioVideo_Titre")
        if thumbnail is None or link is None:
            continue
        info = block.find(class_="ContenuRiche_InfoSecondaire")
        match = DURATION.search(info.get_text(" ", strip=True)) if info else None
        entries[ITEM.search(thumbnail["src"]).group(1)] = {
            "title": _clean(link.get_text(" ", strip=True)),
            "duration": match.group(1) if match else "",
        }
    if entries:
        return entries
    return _guess_entries(soup)


def _guess_entries(soup) -> dict[str, dict]:
    """Fallback for unknown layouts: the shortest text near anything that mentions an item."""
    candidates: dict[str, str] = {}
    for element in soup.find_all(True):
        attributes = " ".join(str(v) for k, v in element.attrs.items() if k in ("href", "src", "style", "data-src", "poster"))
        match = ITEM.search(unquote(attributes))
        if not match:
            continue
        node, text = element, ""
        for _ in range(8):
            text = _clean(node.get_text(" ", strip=True))
            if text or node.parent is None:
                break
            node = node.parent
        # A block with several durations describes several capsules, not this one.
        if not text or len(text) > 250 or len(DURATION.findall(text)) > 1:
            continue
        item = match.group(1)
        if item not in candidates or len(text) < len(candidates[item]):
            candidates[item] = text
    return {item: dict(zip(("title", "duration"), _split_duration(text))) for item, text in candidates.items()}


def videos_from_html(html: str) -> list[FoundVideo]:
    found = []
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.find_all("a", href=True):
        if urlsplit(link["href"]).path.startswith(VIDEO_TRACKER_PATH):
            target = _tracker_target(link["href"])
            if target:
                found.append(FoundVideo(absolute_url(target), _clean(link.get_text(" ", strip=True)), source="tracker"))
    text = html.replace("\\/", "/").replace("&amp;", "&")
    for match in CONTENT_URL.finditer(text):
        if "/vignette" not in match.group(1).lower():
            found.append(FoundVideo(absolute_url(match.group(1)), source="html"))
    return found


def videos_from_player(results: list[dict]) -> list[FoundVideo]:
    found = []
    for result in results or []:
        url = result.get("url")
        if not url:
            continue
        if not has_token(url):
            url = next((s for s in result.get("sources") or [] if s and has_token(s)), url)
        title = (result.get("title") or "").strip()
        if title.lower() in ("unknown title", "untitled"):
            title = ""
        found.append(FoundVideo(absolute_url(url), title, format_duration(result.get("duration")), result.get("source", "player")))
    return found


def merge(*groups: list[FoundVideo], entries: dict[str, dict] | None = None) -> list[FoundVideo]:
    """Deduplicate by path, keeping first-seen order and the most complete information."""
    by_key: dict[str, FoundVideo] = {}
    for group in groups:
        for video in group:
            if urlsplit(video.url).path.lower().endswith(IGNORED_EXTENSIONS):
                continue
            known = by_key.get(video.key)
            if known is None:
                by_key[video.key] = FoundVideo(video.url, video.title, video.duration, video.source)
                continue
            if has_token(video.url) and not has_token(known.url):
                known.url = video.url
            known.title = known.title or video.title
            known.duration = known.duration or video.duration
    for video in by_key.values():
        entry = (entries or {}).get(video.item or "")
        if entry:
            video.title = video.title or entry["title"]
            video.duration = video.duration or entry["duration"]
    return list(by_key.values())


def page_videos(html: str, player_results: list[dict] | None) -> list[FoundVideo]:
    return merge(videos_from_player(player_results or []), videos_from_html(html), entries=playlist_entries(html))
