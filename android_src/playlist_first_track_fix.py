"""Prevent skipping the first queued item after fast-opening a playlist.

When a watch+playlist URL starts the current video immediately, the playlist is
installed later with ``start_playback=False``.  Some YouTube playlist/mix
responses omit that already-playing seed video and start with the first *next*
video.  In that case ``playlist.index`` is 0 already, so the normal
``playlist.next()`` advances to 1 and silently skips item 0.

This patch marks that one transition as "play current queue item first".  It is
narrow and does not change normal playlists where the current video is present
in the queue.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlparse

_LOCK = threading.RLock()
_INSTALLED = False
_STARTED = False


def _video_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and all(c.isalnum() or c in "-_" for c in raw):
        return raw
    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query or "")
        vid = str((query.get("v") or [""])[0] or "")
        if vid:
            return vid
        if "youtu.be" in (parsed.netloc or "").lower():
            return (parsed.path or "").strip("/").split("/", 1)[0]
    except Exception:
        pass
    return ""


def _same_video(a: str, b: str) -> bool:
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    a_id = _video_id(a)
    b_id = _video_id(b)
    return bool(a_id and b_id and a_id == b_id)


def _install_now() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            import audio_screen
        except Exception:
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        # Install after the current-video layer because it also owns
        # _advance_to_next_track.
        if not bool(getattr(cls, "_pymusic_current_related_v4", False)):
            return False
        if bool(getattr(cls, "_pymusic_playlist_first_track_v1", False)):
            _INSTALLED = True
            return True

        old_play_playlist = cls.play_playlist
        old_advance = cls._advance_to_next_track

        def play_playlist_fixed(self, tracks, *args, **kwargs):
            start_playback = bool(kwargs.get("start_playback", True))
            current_before = str(getattr(self, "_last_video_url", "") or "")
            result = old_play_playlist(self, tracks, *args, **kwargs)

            # Only the background queue-install path can have an already playing
            # video that is absent from the returned queue.
            pending = False
            if not start_playback and current_before:
                try:
                    queue = list(getattr(getattr(self, "playlist", None), "tracks", []) or [])
                    current_present = any(
                        _same_video(current_before, str((item or {}).get("url") or (item or {}).get("video_id") or ""))
                        for item in queue
                        if isinstance(item, dict)
                    )
                    pending = bool(queue and not current_present)
                except Exception:
                    pending = False

            self._playlist_play_current_before_next_v1 = pending
            if pending:
                try:
                    print(
                        "[PLAYLIST-FIRST] current seed is outside queue; "
                        "first transition will play queue index 0"
                    )
                except Exception:
                    pass
            return result

        def advance_fixed(self) -> bool:
            if bool(getattr(self, "_playlist_play_current_before_next_v1", False)):
                self._playlist_play_current_before_next_v1 = False
                try:
                    playlist = getattr(self, "playlist", None)
                    track = playlist.current() if playlist else None
                    if track:
                        print(
                            f"[PLAYLIST-FIRST] playing first queued track "
                            f"index={getattr(playlist, 'index', 0)} "
                            f"url={track.get('url')!r}"
                        )
                        self.play_audio(
                            track["url"],
                            track.get("title") or "",
                            track.get("channel") or "",
                            track.get("thumb") or "",
                            clear_playlist=False,
                            hard_reset=True,
                        )
                        return True
                except Exception as exc:
                    print("[PLAYLIST-FIRST] first queued track failed:", exc)
            return bool(old_advance(self))

        cls.play_playlist = play_playlist_fixed
        cls._advance_to_next_track = advance_fixed
        cls._pymusic_playlist_first_track_v1 = True
        _INSTALLED = True
        print("[PLAYLIST-FIRST] first queued track skip fix enabled")
        return True


def install_playlist_first_track_fix() -> bool:
    global _STARTED
    if _install_now():
        return True
    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(500):
            if _install_now():
                return
            time.sleep(0.05)
        print("[PLAYLIST-FIRST] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-playlist-first-track-installer",
        daemon=True,
    ).start()
    return True
