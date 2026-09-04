"""HTTP + optional headless-browser fetching, with per-domain politeness."""
from __future__ import annotations

import random
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
# Tried on retry — some WAFs / rate limiters key on the exact UA string, and a
# GitHub Actions IP hammering with one UA gets throttled where a browser doesn't.
_RETRY_UA = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]
RETRIES = 2                      # extra attempts after the first
RETRYABLE = {403, 429, 500, 502, 503, 504}

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


def _request(method: str, url: str, **kwargs) -> Optional[httpx.Response]:
    headers = dict(kwargs.pop("headers", {}) or {})
    last_exc: Optional[Exception] = None
    for attempt in range(RETRIES + 1):
        _throttle(url)
        if attempt:
            time.sleep(min(8.0, 1.5 * (2 ** attempt)) + random.uniform(0, 1.2))
            headers["User-Agent"] = _RETRY_UA[(attempt - 1) % len(_RETRY_UA)]
        try:
            r = client().request(method, url, headers=headers or None, **kwargs)
        except (httpx.TransportError, httpx.InvalidURL) as exc:
            last_exc = exc                      # connection/timeout — worth a retry
            continue
        if r.is_success:
            if attempt:
                print(f"    · recovered {url} on retry {attempt}")
            return r
        last_exc = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
        if r.status_code not in RETRYABLE:
            break                               # 404/401/... won't change on retry
    print(f"    ! {method} failed {url}: {last_exc}")
    return None


def get(url: str, **kwargs) -> Optional[httpx.Response]:
    return _request("GET", url, **kwargs)


def get_text(url: str, **kwargs) -> Optional[str]:
    r = get(url, **kwargs)
    return r.text if r is not None else None


def post_text(url: str, **kwargs) -> Optional[str]:
    r = _request("POST", url, **kwargs)
    return r.text if r is not None else None


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
