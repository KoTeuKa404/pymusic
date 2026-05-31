from __future__ import annotations

import re
import sys
import threading

_done = False


def _log(*args):
    try:
        print("[PYMUSIC_FIX]", *args)
    except Exception:
        pass


def _video_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    for pattern in (
        r"(?:v=|youtu\.be/|/vi/|/shorts/|/live/)([A-Za-z0-9_-]{11})",
        r"([A-Za-z0-9_-]{11})",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _static_thumb(video_id: str) -> str:
    return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg" if video_id else ""


def install():
    _retry_later(0.05)


def _retry_later(delay: float):
    try:
        timer = threading.Timer(delay, _apply_when_ready)
        timer.daemon = True
        timer.start()
    except Exception:
        pass


def _apply_when_ready():
    global _done
    try:
        main_mod = sys.modules.get("main")
        video_mod = sys.modules.get("video_player")

        if main_mod is not None and not getattr(main_mod, "_pymusic_playlist_fix", False):
            _fix_main(main_mod)
            main_mod._pymusic_playlist_fix = True
            _log("playlist fix installed")

        if video_mod is not None and not getattr(video_mod, "_pymusic_controls_fix", False):
            _fix_video_player(video_mod)
            video_mod._pymusic_controls_fix = True
            _log("controls fix installed")

        _done = bool(main_mod is not None and video_mod is not None)
    except Exception as exc:
        _log("install error", exc)

    if not _done:
        _retry_later(0.35)


def _fix_main(main_mod):
    screen_cls = getattr(main_mod, "YoutubeSearchScreen", None)
    if screen_cls is None:
        return

    original_open_playlist = screen_cls.open_playlist
    original_attach_playlist = screen_cls._open_playlist_on_ui
    Clock = main_mod.Clock

    def open_playlist_fast(self, playlist_url, playlist_title, start_video_id=None, start_after=False, fallback_url=None):
        # For 200+ item YouTube playlists: start the selected/current video now,
        # while the normal background thread continues loading the full queue.
        try:
            fast_url = str(fallback_url or "").strip()
            fast_id = str(start_video_id or "").strip() or _video_id(fast_url)
            if not fast_url and fast_id:
                fast_url = _watch_url(fast_id)
            if fast_url:
                Clock.schedule_once(lambda dt: self._open_single_on_ui(fast_url), 0)
        except Exception:
            pass
        return original_open_playlist(self, playlist_url, playlist_title, start_video_id, start_after, fallback_url)

    def attach_playlist_with_thumbs(self, tracks, playlist_title, start_index=0, playlist_url=None):
        fixed_tracks = []
        for item in tracks or []:
            if isinstance(item, (tuple, list)):
                values = list(item) + [""] * 6
                url, title, channel, thumb, duration, vid = values[:6]
                vid = _video_id(vid) or _video_id(url) or _video_id(thumb)
                if not thumb and vid:
                    thumb = _static_thumb(vid)
                fixed_tracks.append((url, title, channel, thumb, duration, vid))
            elif isinstance(item, dict):
                vid = _video_id(item.get("video_id")) or _video_id(item.get("url")) or _video_id(item.get("thumb"))
                if vid and not item.get("thumb"):
                    item = dict(item)
                    item["video_id"] = vid
                    item["thumb"] = _static_thumb(vid)
                fixed_tracks.append(item)
            else:
                fixed_tracks.append(item)
        return original_attach_playlist(self, fixed_tracks, playlist_title, start_index, playlist_url)

    screen_cls.open_playlist = open_playlist_fast
    screen_cls._open_playlist_on_ui = attach_playlist_with_thumbs


def _fix_video_player(video_mod):
    player_cls = getattr(video_mod, "AndroidVideoPlayer", None)
    if player_cls is None:
        return

    original_visible = player_cls.set_native_controls_visible

    def set_native_controls_visible_strong(self, visible: bool) -> None:
        original_visible(self, visible)
        try:
            overlay = getattr(self, "controls_overlay", None)
            if overlay is not None and visible:
                overlay.setAlpha(1.0)
                overlay.bringToFront()
                overlay.invalidate()
            color = video_mod.Color
            for view in list(getattr(self, "_controls_touch_views", []) or []):
                try:
                    if hasattr(view, "setTextColor"):
                        view.setTextColor(color.WHITE)
                        view.setAlpha(1.0)
                        view.setBackgroundColor(color.argb(220, 0, 0, 0))
                        view.bringToFront()
                except Exception:
                    pass
        except Exception:
            pass

    player_cls.set_native_controls_visible = set_native_controls_visible_strong
