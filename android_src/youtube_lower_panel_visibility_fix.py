"""Keep the YouTube-style lower player panel visible on KivyMD 1.2.

The legacy KV declares ``similar_scroll`` with ``height: 0`` and older runtime
patches may temporarily set it back to zero while recommendations refresh.  The
new lower panel reuses that slot, so on some devices its children exist but the
viewport remains 0 px tall.  This patch makes the list own its minimum height
and guards the outer slot from being collapsed while a video is open.
"""
from __future__ import annotations

import threading
import time

from kivy.clock import Clock
from kivy.metrics import dp

_INSTALLED = False
_STARTED = False
_LOCK = threading.RLock()


def _apply_geometry(owner) -> None:
    try:
        video_url = str(getattr(owner, "_last_video_url", "") or "")
        scroll = owner.ids.get("similar_scroll")
        listing = owner.ids.get("similar_list")
        header = owner.ids.get("similar_header_row")
        if scroll is None or listing is None:
            return

        if header is not None:
            header.height = 0
            header.opacity = 0
            header.disabled = True

        if not video_url:
            return

        # MDList inside a fixed-height MDScrollView may leave minimum_height
        # correct while its actual height stays zero on KivyMD 1.2.
        try:
            listing.size_hint_y = None
        except Exception:
            pass

        minimum = 0.0
        try:
            minimum = float(listing.minimum_height or 0)
        except Exception:
            minimum = 0.0

        # Even before async comments/recommendations arrive the autoplay +
        # section headers/loading rows need a real visible viewport.
        target = max(minimum, float(dp(250)))
        owner._yt_lower_target_height = target

        try:
            listing.height = target
        except Exception:
            pass
        try:
            scroll.size_hint_y = None
            scroll.height = target
            scroll.opacity = 1
            scroll.disabled = False
            scroll.do_scroll_x = False
            # The outer player_details_scroll is the only vertical scroller.
            scroll.do_scroll_y = False
            scroll.bar_width = 0
        except Exception:
            pass

        if not bool(getattr(listing, "_yt_lower_min_bound_v2", False)):
            def on_minimum(_instance, value):
                try:
                    if not str(getattr(owner, "_last_video_url", "") or ""):
                        return
                    desired = max(float(value or 0), float(dp(250)))
                    owner._yt_lower_target_height = desired
                    listing.height = desired
                    scroll.height = desired
                    scroll.opacity = 1
                    scroll.disabled = False
                except Exception:
                    pass

            listing.bind(minimum_height=on_minimum)
            listing._yt_lower_min_bound_v2 = True

        if not bool(getattr(scroll, "_yt_lower_height_guard_v2", False)):
            def guard_height(_instance, value):
                try:
                    if not str(getattr(owner, "_last_video_url", "") or ""):
                        return
                    if float(value or 0) >= float(dp(120)):
                        return
                    desired = max(
                        float(getattr(owner, "_yt_lower_target_height", 0) or 0),
                        float(getattr(listing, "minimum_height", 0) or 0),
                        float(dp(250)),
                    )
                    Clock.schedule_once(
                        lambda _dt: _restore(owner, scroll, listing, desired), 0
                    )
                except Exception:
                    pass

            scroll.bind(height=guard_height)
            scroll._yt_lower_height_guard_v2 = True
    except Exception as exc:
        try:
            print("[YT-LOWER-VIS] geometry failed:", exc)
        except Exception:
            pass


def _restore(owner, scroll, listing, desired: float) -> None:
    try:
        if not str(getattr(owner, "_last_video_url", "") or ""):
            return
        desired = max(float(desired or 0), float(dp(250)))
        owner._yt_lower_target_height = desired
        listing.size_hint_y = None
        listing.height = desired
        scroll.size_hint_y = None
        scroll.height = desired
        scroll.opacity = 1
        scroll.disabled = False
        scroll.do_scroll_y = False
        scroll.bar_width = 0
    except Exception:
        pass


def _queue(owner) -> None:
    for delay in (0.0, 0.03, 0.12, 0.35, 0.8, 1.6, 3.0):
        Clock.schedule_once(lambda _dt, current=owner: _apply_geometry(current), delay)


def _install_now() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            from youtube_lower_panel_fix import install_youtube_lower_panel_fix
            if not install_youtube_lower_panel_fix():
                return False

            import audio_screen
            cls = getattr(audio_screen, "AudioPlayerScreen", None)
            if cls is None:
                return False
            if not bool(getattr(cls, "_pymusic_youtube_lower_panel_v1", False)):
                return False
            if bool(getattr(cls, "_pymusic_youtube_lower_visibility_v2", False)):
                _INSTALLED = True
                return True

            old_render = cls._render_similar_ui
            old_play_audio = cls.play_audio
            old_pre_enter = cls.on_pre_enter
            old_resume = cls.handle_app_resume

            def render_visible(self, *args, **kwargs):
                result = old_render(self, *args, **kwargs)
                _queue(self)
                return result

            def play_visible(self, *args, **kwargs):
                result = old_play_audio(self, *args, **kwargs)
                _queue(self)
                return result

            def pre_enter_visible(self, *args, **kwargs):
                result = old_pre_enter(self, *args, **kwargs)
                _queue(self)
                return result

            def resume_visible(self, *args, **kwargs):
                result = old_resume(self, *args, **kwargs)
                _queue(self)
                return result

            cls._render_similar_ui = render_visible
            cls.play_audio = play_visible
            cls.on_pre_enter = pre_enter_visible
            cls.handle_app_resume = resume_visible
            cls._pymusic_youtube_lower_visibility_v2 = True

            _INSTALLED = True
            print("[YT-LOWER-VIS] lower panel height guard enabled")
            return True
        except Exception as exc:
            try:
                print("[YT-LOWER-VIS] install failed:", exc)
            except Exception:
                pass
            return False


def install_youtube_lower_panel_visibility_fix() -> bool:
    global _STARTED
    if _install_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(400):
            if _install_now():
                return
            time.sleep(0.05)
        try:
            print("[YT-LOWER-VIS] install timeout")
        except Exception:
            pass

    threading.Thread(
        target=waiter,
        name="pymusic-youtube-lower-visibility",
        daemon=True,
    ).start()
    return True
