"""The page renderer for NDI outputs (#299): a Relay page drawn off screen and handed over as BGRA frames.

**Spike result (2026-11-17, macOS, 1280x720).** Headless Chromium through Playwright took about 30 screenshots a second
with `omit_background` giving real alpha (PNG, RGBA), and about 0.4 s of Python CPU in 4 s of capture. A hidden Tauri
webview was not tried: it cannot give an alpha screenshot off screen and does not exist on the headless Linux service,
so Chromium it is. Playwright and its Chromium are an optional install (`pip install playwright`, then
`playwright install chromium`); without them the outputs page says so and the router still works.

A renderer has `frame()` (BGRA, width x height x 4, as a writable buffer) and `close()`. Tests replace `make`.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Callable

log = logging.getLogger("relay.ndi.render")


def available() -> str:
    """'' when the renderer can run, else the sentence to show the operator."""
    try:
        import numpy  # noqa: F401
        import PIL  # noqa: F401
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return "Page outputs need Playwright: pip install playwright, then playwright install chromium."
    return ""


class PlaywrightRenderer:
    """One headless Chromium page at a fixed size. With `alpha` the page background is transparent, so a lower third
    keys over video; without it frames are JPEG, which is cheaper to capture."""

    def __init__(self, url: str, width: int, height: int, alpha: bool) -> None:
        from playwright.sync_api import sync_playwright

        self.width, self.height, self.alpha = width, height, alpha
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch()
            self._page = self._browser.new_page(viewport={"width": width, "height": height})
            self._page.goto(url, wait_until="load", timeout=15000)
        except Exception:
            self.close()
            raise

    def frame(self) -> bytes:
        import numpy as np
        from PIL import Image

        if self.alpha:
            raw = self._page.screenshot(type="png", omit_background=True)
        else:
            raw = self._page.screenshot(type="jpeg", quality=85)
        rgba = np.asarray(Image.open(io.BytesIO(raw)).convert("RGBA"))
        return rgba[..., [2, 1, 0, 3]].reshape(-1)        # RGBA -> BGRA, the order NDI wants (a writable uint8 array)

    def close(self) -> None:
        for obj, call in ((getattr(self, "_browser", None), "close"), (self._pw, "stop")):
            try:
                if obj is not None:
                    getattr(obj, call)()
            except Exception:
                pass


def make(url: str, width: int, height: int, alpha: bool) -> Any:
    return PlaywrightRenderer(url, width, height, alpha)


Factory = Callable[[str, int, int, bool], Any]
