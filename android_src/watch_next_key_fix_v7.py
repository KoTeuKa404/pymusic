"""Reliable public Innertube key fallback for YouTube watch-next.

Modern yt-dlp client profiles no longer embed INNERTUBE_API_KEY.  YouTube still
publishes the current anonymous web key in its public page bootstrap.  Scrape it
over normal verified HTTPS only when the watch page did not provide one, cache
it in memory, and feed it to related_videos._merge_page_config.
"""
from __future__ import annotations

import re
import threading
import time

import httpx

_INSTALLED = False
_LOCK = threading.RLock()
_KEY_LOCK = threading.Lock()
_CACHED_KEY = ""
_CACHED_AT = 0.0

_UA = (
    "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
)


def _extract_key(text: str) -> str:
    for pattern in (
        r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"',
        r'"innertubeApiKey"\s*:\s*"([^"]+)"',
    ):
        match = re.search(pattern, text or "")
        if match:
            value = str(match.group(1) or "").strip()
            if value:
                return value
    return ""


def _scrape_public_key(force: bool = False) -> str:
    global _CACHED_KEY, _CACHED_AT
    now = time.time()
    if _CACHED_KEY and not force and (now - _CACHED_AT) < 6 * 3600:
        return _CACHED_KEY

    with _KEY_LOCK:
        now = time.time()
        if _CACHED_KEY and not force and (now - _CACHED_AT) < 6 * 3600:
            return _CACHED_KEY

        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
            "Cookie": "SOCS=CAI; PREF=hl=uk&gl=UA",
        }
        timeout = httpx.Timeout(8.0, connect=6.0)

        for url in (
            "https://www.youtube.com/?hl=uk&gl=UA",
            "https://m.youtube.com/?hl=uk&gl=UA",
            "https://www.youtube.com/?hl=en&gl=US",
        ):
            try:
                response = httpx.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                key = _extract_key(response.text)
                if key:
                    _CACHED_KEY = key
                    _CACHED_AT = time.time()
                    print("[WATCH-NEXT-V7] scraped current YouTube Innertube key")
                    return key
            except Exception as exc:
                print(f"[WATCH-NEXT-V7] key bootstrap failed for {url}: {exc}")

        return ""


def install_watch_next_key_fix_v7() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            import related_videos as related
        except Exception as exc:
            print("[WATCH-NEXT-V7] related import failed:", exc)
            return False

        if bool(getattr(related, "_pymusic_watch_next_key_v7", False)):
            _INSTALLED = True
            return True

        old_merge = getattr(related, "_merge_page_config", None)
        if not callable(old_merge):
            return False

        def merge_with_scraped_key(profile, ytcfg):
            context, visitor, api_key = old_merge(profile, ytcfg)
            if not api_key:
                api_key = _scrape_public_key(force=False)
            return context, visitor, api_key

        related._merge_page_config = merge_with_scraped_key
        related._pymusic_watch_next_key_v7 = True
        _INSTALLED = True
        print("[WATCH-NEXT-V7] verified-HTTPS Innertube key fallback enabled")
        return True
