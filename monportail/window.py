"""Keep the automated browser window out of the way.

Windows may stop rendering a window it considers hidden, which breaks the
dashboard and Playwright clicks. The browser is therefore launched with
occlusion detection disabled, parked in a corner while the dashboard loads,
and only moved off-screen once a render check confirms pages still draw.
"""

import os
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from extracteur.auth import (
    DUREE_AFFICHAGE_PAGE_IDENTIFICATION,
    PAGE_IDENTIFICATION,
    PREFIXE_DOSSIER_PROFIL_TEMPORAIRE,
    SCRIPT_PREFIXE_TITRE,
    URL_DEPART,
    SessionNavigateur,
)

OFF_SCREEN = {"left": -32000, "top": 0}
ON_SCREEN = {"left": 80, "top": 60, "width": 1280, "height": 860}
CORNER_SIZE = (520, 380)

CHROMIUM_ARGS = [
    "--disable-features=CalculateNativeWinOcclusion",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
]

RENDER_CHECK_SCRIPT = """
() => new Promise((resolve) => {
  let drawn = false;
  requestAnimationFrame(() => requestAnimationFrame(() => { drawn = true; resolve(true); }));
  setTimeout(() => { if (!drawn) resolve(false); }, 3000);
})
"""

_corner: dict | None = None


class BrowserSession(SessionNavigateur):
    def ouvrir(self, sommeil=time.sleep) -> None:
        self.dossier_profil = Path(tempfile.mkdtemp(prefix=PREFIXE_DOSSIER_PROFIL_TEMPORAIRE))
        self._playwright = sync_playwright().start()
        self.contexte = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.dossier_profil),
            headless=self.sans_fenetre,
            accept_downloads=True,
            args=CHROMIUM_ARGS,
        )
        self.contexte.add_init_script(SCRIPT_PREFIXE_TITRE)
        self.page = self.contexte.pages[0] if self.contexte.pages else self.contexte.new_page()
        self.page.set_content(PAGE_IDENTIFICATION)
        self._amener_au_premier_plan()
        sommeil(DUREE_AFFICHAGE_PAGE_IDENTIFICATION)
        self.page.goto(URL_DEPART, wait_until="domcontentloaded")


def _set_bounds(session, bounds: dict) -> bool:
    try:
        cdp = session.contexte.new_cdp_session(session.page)
        window_id = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": {"windowState": "normal"}})
        cdp.send("Browser.setWindowBounds", {"windowId": window_id, "bounds": bounds})
        cdp.detach()
        return True
    except Exception:  # noqa: BLE001
        return False


def is_rendering(session) -> bool:
    try:
        return bool(session.page.evaluate(RENDER_CHECK_SCRIPT))
    except Exception:  # noqa: BLE001
        return False


def park_in_corner(session) -> bool:
    global _corner
    try:
        x, y, width, height = session.page.evaluate(
            "() => [screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]"
        )
    except Exception:  # noqa: BLE001
        return False
    w, h = CORNER_SIZE
    _corner = {"left": int(x + width - w - 12), "top": int(y + height - h - 12), "width": w, "height": h}
    return _set_bounds(session, _corner)


def hide(session) -> bool:
    """Move the window off-screen; fall back to the corner if pages stop rendering."""
    if not _set_bounds(session, OFF_SCREEN):
        return False
    if is_rendering(session):
        return True
    _set_bounds(session, _corner or ON_SCREEN)
    return False


def show(session) -> None:
    _set_bounds(session, ON_SCREEN)
    try:
        session.page.bring_to_front()
    except Exception:  # noqa: BLE001
        pass


def focus_terminal() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        console = kernel32.GetConsoleWindow()
        if not console:
            return
        # Under Windows Terminal the console is a hidden window owned by the real one.
        target = user32.GetAncestor(console, 3) or console
        # A synthetic Alt press lifts Windows' foreground lock.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(target)
    except Exception:  # noqa: BLE001
        pass
