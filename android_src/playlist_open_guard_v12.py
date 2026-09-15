"""Prevent playlist/container URLs from entering the single-track fast path.

``YoutubeSearchScreen.open_playlist`` starts ``fallback_url`` immediately while
playlist extraction continues in the background.  That is correct for a
watch+playlist URL whose fallback contains a real video id, but a plain
``/playlist?list=...`` or ``/browse/...`` URL is a container, not a playable
track.  Starting such a URL through ``play_audio`` makes yt-dlp fail and the
normal extract-failure auto-skip advances the freshly installed queue from
index 0 to index 1.

V12 permits fast start only when the fallback resolves to an actual YouTube
video id.  Plain playlist/album containers wait for extraction and then
``play_playlist(..., start_playback=True)`` starts the requested queue index.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

_LOCK = threading.RLock()
_PATCHED = False
_STARTED = False


def _valid_video_id(value: str) -> str:
    value = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    return ""


def _video_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    direct = _valid_video_id(raw)
    if direct:
        return direct

    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if not host and raw.startswith(("www.youtube.com/", "youtube.com/", "youtu.be/")):
            parsed = urlparse("https://" + raw)
            host = (parsed.netloc or "").lower()
            path = parsed.path or ""

        if "youtu.be" in host:
            return _valid_video_id(path.strip("/").split("/", 1)[0])

        if "youtube.com" not in host:
            return ""

        query = parse_qs(parsed.query or "")
        vid = _valid_video_id(str((query.get("v") or [""])[0] or ""))
        if vid:
            return vid

        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0].lower() in ("shorts", "live", "embed"):
            return _valid_video_id(parts[1])
    except Exception:
        pass
    return ""


def _find_search_class():
    seen = set()
    for module_name in ("main", "__main__"):
        module = sys.modules.get(module_name)
        if module is None or id(module) in seen:
            continue
        seen.add(id(module))
        cls = getattr(module, "YoutubeSearchScreen", None)
        if cls is not None:
            return cls
    return None


def _patch_now() -> bool:
    global _PATCHED

    with _LOCK:
        if _PATCHED:
            return True

        cls = _find_search_class()
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_playlist_open_guard_v12", False)):
            _PATCHED = True
            return True

        old_open_playlist = cls.open_playlist

        def open_playlist_guarded(
            self,
            playlist_url,
            playlist_title,
            start_video_id=None,
            start_after=False,
            fallback_url=None,
        ):
            raw_fallback = str(fallback_url or "").strip()
            fallback_vid = _video_id(raw_fallback)
            seed_vid = _video_id(str(start_video_id or ""))
            safe_fallback = raw_fallback or None

            if raw_fallback and not fallback_vid:
                # A container URL cannot be decoded by the single-track audio
                # extractor. Preserve a real seed if one exists; otherwise wait
                # for the queue and let play_playlist start index 0 normally.
                if seed_vid:
                    safe_fallback = f"https://www.youtube.com/watch?v={seed_vid}"
                    print(
                        "[PLAYLIST-OPEN-V12] replaced container fallback with "
                        f"seed video {seed_vid}"
                    )
                else:
                    safe_fallback = None
                    print(
                        "[PLAYLIST-OPEN-V12] ignored non-video fast fallback "
                        f"{raw_fallback!r}"
                    )

            return old_open_playlist(
                self,
                playlist_url,
                playlist_title,
                start_video_id=start_video_id,
                start_after=start_after,
                fallback_url=safe_fallback,
            )

        cls.open_playlist = open_playlist_guarded
        cls._pymusic_playlist_open_guard_v12 = True
        _PATCHED = True
        print("[PLAYLIST-OPEN-V12] container URLs no longer fast-start as audio")
        return True


def install_playlist_open_guard_v12() -> bool:
    global _STARTED

    if _patch_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(500):
            if _patch_now():
                return
            time.sleep(0.05)
        print("[PLAYLIST-OPEN-V12] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-playlist-open-v12",
        daemon=True,
    ).start()
    return True


install_playlist_open_guard_v12()
