"""Interactive capture of a fresh `obe` session value via a real browser login.

Requires the `browser` optional dependency group (`uv sync --group browser`),
kept separate from the core ingestion/API path so a headless server never
needs a Chromium install.

The password never touches this code: you log into coinglass.com yourself, in
a real visible window. We only ever read the `obe` header off a real outgoing
request once you're done -- not a guessed cookie or localStorage key, since
the site only attaches `obe` at all once a session exists (an anonymous
visitor's requests carry no `obe` header, confirmed by recon).
"""
from __future__ import annotations

from . import session

HEATMAP_PAGE = "https://www.coinglass.com/pro/futures/LiquidationHeatMap"
LOGIN_PAGE = "https://www.coinglass.com/login"
CAPI_HOST = "capi.coinglass.com"


def capture() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()

        captured: list[str] = []
        page.on("request", lambda req: (
            captured.append(req.headers.get("obe", ""))
            if CAPI_HOST in req.url and "heatmap" in req.url.lower() else None
        ))

        page.goto(LOGIN_PAGE, wait_until="networkidle")
        input("Log in (email/password or Google), then press Enter here once "
              "you see the CoinGlass dashboard... ")

        captured.clear()
        page.goto(HEATMAP_PAGE, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(3_000)
        browser.close()

        obe = next((v for v in captured if v), "")
        if not obe:
            raise RuntimeError(
                "no obe header seen on the heatmap request -- login may not have "
                "completed, or the site changed how it attaches the session")
        session.write_obe(obe)
        return obe
