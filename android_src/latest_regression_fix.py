"""Small regression fixes for the stable player UI.

1. Prevent the outer player details ScrollView from elastically moving beyond
   its real bounds (the large white gap above the metadata).
2. Treat YouTube `RD...` / `start_radio=1` watch URLs as a single video rather
   than a real playlist. The normal related-video loader then owns the lower
   section and shows `Схожі відео`.

This module deliberately does not rebuild the KV/UI tree.
"""
from __future__ import annotations

import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

from kivy.app import App
from kivy.clock import Clock

try:
    from kivy.effects.scroll import ScrollEffect
except Exception:
    ScrollEffect = None


_PLAYER_PATCHED = False
_MAIN_PATCHED = False
_LOCK = threading.RLock()


def _mix_video_url(value: str) -> tuple[str, str] | None:
    """Return (canonical_watch_url, video_id) only for YouTube radio/mix URLs."""
    try:
        raw = str(value or "").strip()
        if not raw:
            return None
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        if "youtube.com" not in host and "youtu.be" not in host:
            return None
        query = parse_qs(parsed.query or "")
        playlist_id = str((query.get("list") or [""])[0] or "")
        start_radio = str((query.get("start_radio") or [""])[0] or "")
        video_id = str((query.get("v") or [""])[0] or "")
        if not video_id and "youtu.be" in host:
            video_id = (parsed.path or "").strip("/").split("/")[0]
        is_mix = bool(playlist_id.upper().startswith("RD") or start_radio == "1")
        if not is_mix or not video_id:
            return None
        return f"https://www.youtube.com/watch?v={video_id}", video_id
    except Exception:
        return None


def _normalize_scroll(scroll) -> None:
    if scroll is None:
        return
    try:
        effect = getattr(scroll, "effect_y", None)
        if effect is not None:
            try:
                effect.velocity = 0
            except Exception:
                pass
            try:
                effect.is_manual = False
            except Exception:
                pass
            try:
                low = min(float(effect.min), float(effect.max))
                high = max(float(effect.min), float(effect.max))
                value = float(effect.value)
                if value < low or value > high:
                    effect.value = max(low, min(high, value))
            except Exception:
                pass

        try:
            value = float(scroll.scroll_y)
        except Exception:
            value = 1.0
        scroll.scroll_y = max(0.0, min(1.0, value))

        try:
            update = getattr(scroll, "_update_from_scroll", None)
            if callable(update):
                update()
        except Exception:
            pass
        try:
            scroll.canvas.ask_update()
        except Exception:
            pass
    except Exception:
        pass


def _configure_player_scroll(owner) -> None:
    try:
        outer = owner.ids.get("player_details_scroll")
        if outer is None:
            return

        try:
            if ScrollEffect is not None:
                outer.effect_cls = ScrollEffect
        except Exception:
            pass
        try:
            outer.always_overscroll = False
        except Exception:
            pass
        try:
            outer.smooth_scroll_end = 0
        except Exception:
            pass
        try:
            outer.do_scroll_x = False
            outer.do_scroll_y = True
        except Exception:
            pass

        if not bool(getattr(outer, "_pymusic_hard_bounds_v1", False)):
            try:
                outer.bind(
                    on_scroll_stop=lambda widget, *_args: Clock.schedule_once(
                        lambda _dt: _normalize_scroll(widget), 0
                    )
                )
            except Exception:
                pass
            outer._pymusic_hard_bounds_v1 = True

        _normalize_scroll(outer)
    except Exception as exc:
        try:
            print("[LATEST-FIX] player scroll config failed:", exc)
        except Exception:
            pass


def _patch_player() -> bool:
    global _PLAYER_PATCHED
    with _LOCK:
        if _PLAYER_PATCHED:
            return True
        try:
            import audio_screen

            cls = getattr(audio_screen, "AudioPlayerScreen", None)
            if cls is None:
                return False
            if bool(getattr(cls, "_pymusic_latest_scroll_fix_v1", False)):
                _PLAYER_PATCHED = True
                return True

            old_init = cls.__init__
            old_pre_enter = cls.on_pre_enter
            old_resume = cls.handle_app_resume
            old_play_audio = cls.play_audio

            def init_fixed(self, *args, **kwargs):
                old_init(self, *args, **kwargs)
                for delay in (0.0, 0.08, 0.25):
                    Clock.schedule_once(
                        lambda _dt, owner=self: _configure_player_scroll(owner),
                        delay,
                    )

            def pre_enter_fixed(self, *args, **kwargs):
                result = old_pre_enter(self, *args, **kwargs)
                for delay in (0.0, 0.06, 0.18):
                    Clock.schedule_once(
                        lambda _dt, owner=self: _configure_player_scroll(owner),
                        delay,
                    )
                return result

            def resume_fixed(self, *args, **kwargs):
                result = old_resume(self, *args, **kwargs)
                for delay in (0.0, 0.08, 0.22):
                    Clock.schedule_once(
                        lambda _dt, owner=self: _configure_player_scroll(owner),
                        delay,
                    )
                return result

            def play_audio_fixed(self, video_url, *args, **kwargs):
                mix = _mix_video_url(video_url)
                if mix is not None:
                    video_url = mix[0]
                    # A radio/mix URL is a single video in PyMusic. Ensure a
                    # previously opened queue cannot remain visible underneath.
                    kwargs["clear_playlist"] = True
                return old_play_audio(self, video_url, *args, **kwargs)

            cls.__init__ = init_fixed
            cls.on_pre_enter = pre_enter_fixed
            cls.handle_app_resume = resume_fixed
            cls.play_audio = play_audio_fixed
            cls._pymusic_latest_scroll_fix_v1 = True
            _PLAYER_PATCHED = True
            print("[LATEST-FIX] hard player scroll bounds + Mix URL canonicalization enabled")
            return True
        except Exception as exc:
            try:
                print("[LATEST-FIX] player patch failed:", exc)
            except Exception:
                pass
            return False


def _open_audio_screen(screen) -> None:
    try:
        app = App.get_running_app()
        root = getattr(app, "root", None)
        if root is not None and hasattr(root, "open_audio"):
            root.open_audio()
            return
    except Exception:
        pass
    try:
        if screen.manager is not None:
            screen.manager.current = "audio"
    except Exception:
        pass


def _find_main_classes():
    """Find the app classes whether p4a runs main.py as __main__ or as main."""
    seen = set()
    for module_name in ("main", "__main__"):
        module = sys.modules.get(module_name)
        if module is None or id(module) in seen:
            continue
        seen.add(id(module))
        search_cls = getattr(module, "YoutubeSearchScreen", None)
        web_cls = getattr(module, "YoutubeWebScreen", None)
        if search_cls is not None and web_cls is not None:
            return search_cls, web_cls
    return None, None


def _patch_main_classes() -> bool:
    global _MAIN_PATCHED
    with _LOCK:
        if _MAIN_PATCHED:
            return True

        search_cls, web_cls = _find_main_classes()
        if search_cls is None or web_cls is None:
            return False
        if bool(getattr(search_cls, "_pymusic_mix_single_v1", False)):
            _MAIN_PATCHED = True
            return True

        old_fetch = search_cls._fetch_results_thread
        old_web_play = web_cls._webview_play

        def fetch_results_mix_safe(self, query):
            mix = _mix_video_url(query)
            if mix is None:
                return old_fetch(self, query)
            canonical, video_id = mix
            print(f"[LATEST-FIX] YouTube Mix search URL -> single video {video_id}")
            Clock.schedule_once(
                lambda _dt: self.play_audio(
                    canonical,
                    f"Video {video_id}",
                    "",
                    "",
                ),
                0,
            )
            return None

        def web_play_mix_safe(self, url):
            mix = _mix_video_url(url)
            if mix is None:
                return old_web_play(self, url)
            canonical, video_id = mix
            print(f"[LATEST-FIX] YouTube Mix WebView URL -> single video {video_id}")
            try:
                audio = self.manager.get_screen("audio")
                audio.play_audio(canonical, clear_playlist=True)
                _open_audio_screen(self)
            except Exception as exc:
                try:
                    print("[LATEST-FIX] Mix WebView dispatch failed:", exc)
                except Exception:
                    pass
            return None

        search_cls._fetch_results_thread = fetch_results_mix_safe
        web_cls._webview_play = web_play_mix_safe
        search_cls._pymusic_mix_single_v1 = True
        web_cls._pymusic_mix_single_v1 = True
        _MAIN_PATCHED = True
        print("[LATEST-FIX] YouTube Mix/RD URLs now open as single videos")
        return True


def install_latest_regression_fix() -> bool:
    _patch_player()

    # search_utils is imported before YoutubeSearchScreen/YoutubeWebScreen are
    # defined in main.py, so wait briefly and patch them once their class bodies
    # exist. No UI polling is performed after installation.
    def waiter():
        for _attempt in range(400):
            if _patch_main_classes():
                return
            time.sleep(0.05)
        try:
            print("[LATEST-FIX] main class patch timeout")
        except Exception:
            pass

    threading.Thread(
        target=waiter,
        name="pymusic-latest-regression-fix",
        daemon=True,
    ).start()
    return True
