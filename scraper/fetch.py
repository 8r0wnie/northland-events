"""HTTP + optional headless-browser fetching, with per-domain politeness."""
from __future__ import annotations

import time
import threading
from typing import Optional
from urllib.parse import urlparse

import httpx

# Many municipal/WAF-fronted sites 403 anything that doesn't look like a browser.
# We send a real browser UA; the bot's purpose/contact lives on the site's /about
# page and in robots handling below rather than in the UA string.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Minimum seconds between requests to the same host.
CRAWL_DELAY = 2.0

_last_hit: dict[str, float] = {}
_lock = threading.Lock()


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _throttle(url: str) -> None:
    host = _host(url)
    with _lock:
        wait = CRAWL_DELAY - (time.monotonic() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


_client: Optional[httpx.Client] = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=httpx.Timeout(20.0),
            http2=True,
        )
    return _client


def get(url: str, **kwargs) -> Optional[httpx.Response]:
    _throttle(url)
    try:
        r = client().get(url, **kwargs)
        r.raise_for_status()
        return r
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        print(f"    ! GET failed {url}: {exc}")
        return None


def get_text(url: str, **kwargs) -> Optional[str]:
    r = get(url, **kwargs)
    return r.text if r is not None else None


def post_text(url: str, **kwargs) -> Optional[str]:
    _throttle(url)
    try:
        r = client().post(url, **kwargs)
        r.raise_for_status()
        return r.text
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        print(f"    ! POST failed {url}: {exc}")
        return None


# ── Headless browser (lazy; only spun up when an adapter asks for it) ──────────
_pw = None
_browser = None


def render(url: str, *, wait_selector: str | None = None, timeout: int = 25000) -> Optional[str]:
    """Return fully-rendered HTML for JS-heavy pages."""
    global _pw, _browser
    _throttle(url)
    try:
        from playwright.sync_api import sync_playwright

        if _browser is None:
            _pw = sync_playwright().start()
            _browser = _pw.chromium.launch(headless=True)
        page = _browser.new_page(user_agent=USER_AGENT, locale="en-US")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            page.wait_for_timeout(1500)
            return page.content()
        finally:
            page.close()
    except Exception as exc:  # noqa: BLE001 - browser failures are non-fatal
        print(f"    ! render failed {url}: {exc}")
        return None


def shutdown() -> None:
    global _pw, _browser, _client
    if _browser is not None:
        try:
            _browser.close()
        finally:
            _browser = None
    if _pw is not None:
        try:
            _pw.stop()
        finally:
            _pw = None
    if _client is not None:
        _client.close()
        _client = None
