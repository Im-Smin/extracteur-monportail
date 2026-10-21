"""Resumable video downloads over the browser session's cookies.

monPortail serves capsules directly and honours range requests, so an
interrupted download resumes from its .part file instead of starting over.
"""

import http.client
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from extracteur.nommage import chemin_long as _windows_long_path

CHUNK_SIZE = 1024 * 1024
TIMEOUT_S = 60
RETRY_DELAYS = (2, 5, 10, 20, 30)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) monportail-archiver"


class Cancelled(Exception):
    pass


class SessionLost(Exception):
    pass


class DownloadFailed(Exception):
    pass


def long_path(path: Path) -> str:
    return _windows_long_path(path) if os.name == "nt" else str(Path(path).resolve())


def _open(cookies: Callable, url: str, start: int = 0, end: int | None = None):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies()))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    if start or end is not None:
        request.add_header("Range", f"bytes={start}-" + ("" if end is None else str(end)))
    return opener.open(request, timeout=TIMEOUT_S)


def _reject_html(response) -> None:
    # An expired session gets the login page instead of the video.
    if "text/html" in (response.headers.get("Content-Type") or "").lower():
        raise SessionLost("le serveur a renvoyé une page web au lieu de la vidéo")


def _total_size(response, start: int) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        total = content_range.rsplit("/", 1)[-1].strip()
        if total.isdigit():
            return int(total)
    length = response.headers.get("Content-Length")
    if length and length.isdigit():
        return int(length) + (start if response.status == 206 else 0)
    return None


def remote_size(cookies: Callable, url: str) -> int | None:
    try:
        with _open(cookies, url, 0, 0) as response:
            _reject_html(response)
            return _total_size(response, 0)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise SessionLost(f"HTTP {error.code}") from error
        return None
    except (OSError, http.client.HTTPException):
        return None


def is_complete(path: Path, size: int | None) -> bool:
    target = long_path(Path(path))
    if not os.path.exists(target):
        return False
    actual = os.path.getsize(target)
    return actual == size if size else actual > 0


def download(
    cookies: Callable,
    url: str,
    destination: Path,
    expected_size: int | None,
    on_progress: Callable[[int], None],
    cancel,
    on_total: Callable[[int], None] | None = None,
    sleep=time.sleep,
) -> int:
    """Download url to destination, resuming any .part file.

    on_progress receives byte deltas (including the bytes already on disk).
    Setting the cancel event stops at the next chunk and keeps the .part.
    """
    destination = Path(destination)
    os.makedirs(long_path(destination.parent), exist_ok=True)
    partial = long_path(destination.with_name(destination.name + ".part"))
    final = long_path(destination)

    done = os.path.getsize(partial) if os.path.exists(partial) else 0
    if expected_size and done > expected_size:
        os.unlink(partial)
        done = 0
    if done:
        on_progress(done)

    attempts = 0
    reauthenticated = False
    total = expected_size
    while True:
        if cancel.is_set():
            raise Cancelled()
        try:
            if total and done >= total:
                break
            with _open(cookies, url, done) as response:
                _reject_html(response)
                if done and response.status != 206:
                    on_progress(-done)
                    done = 0
                total = _total_size(response, done) or total
                if total and on_total:
                    on_total(total)
                with open(partial, "ab" if done else "wb") as out:
                    while chunk := response.read(CHUNK_SIZE):
                        if cancel.is_set():
                            raise Cancelled()
                        out.write(chunk)
                        done += len(chunk)
                        on_progress(len(chunk))
                        attempts = 0
            if total is None or done >= total:
                break
            raise ConnectionError("flux interrompu avant la fin")
        except (Cancelled, SessionLost):
            raise
        except urllib.error.HTTPError as error:
            if error.code == 416 and total and done >= total:
                break
            if error.code in (401, 403):
                if reauthenticated:
                    raise SessionLost(f"HTTP {error.code}") from error
                reauthenticated = True
                continue
            if error.code == 404:
                raise DownloadFailed("HTTP 404 : la vidéo n'existe plus sur le serveur") from error
            if attempts >= len(RETRY_DELAYS):
                raise DownloadFailed(f"HTTP {error.code}") from error
        except (OSError, http.client.HTTPException, socket.timeout, ConnectionError) as error:
            if attempts >= len(RETRY_DELAYS):
                raise DownloadFailed(f"réseau : {error}") from error
        delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS) - 1)]
        attempts += 1
        for _ in range(int(delay * 10)):
            if cancel.is_set():
                raise Cancelled()
            sleep(0.1)

    os.replace(partial, final)
    return done
