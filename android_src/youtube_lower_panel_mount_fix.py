"""Final live-screen mount recovery for the YouTube lower player panel.

PyMusic keeps its ScreenManager at ``App.root.sm``. Earlier lower-panel
fallbacks only checked the root itself, so a patch installed after the audio
screen had already been constructed could fail to find the live screen.
"""
from __future__ import annotations

import threading
from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp

_STARTED = False
_LOCK = threading.RLock()


def _find_audio_screen():
    try:
        app = App.get_running_app()
        root = getattr(app, "root", None) if app is not None else None
        if root is None:
            return None

        sm = getattr(root, "sm", None)
        if sm is not None:
            getter = getattr(sm, "get_screen", None)
            if callable(getter):
                try:
                    return getter("audio")
                except Exception:
                    pass
            for screen in list(getattr(sm, "screens", []) or []):
                if getattr(screen, "name", None) == "audio":
                    return screen

        getter = getattr(root, "get_screen", None)
        if callable(getter):
            try:
                return getter("audio")
            except Exception:
                pass

        walker = getattr(root, "walk", None)
        if callable(walker):
            try:
                widgets = walker(restrict=False)
            except TypeError:
                widgets = walker()
            for widget in widgets:
                if getattr(widget, "name", None) == "audio" and hasattr(widget, "ids"):
                    return widget
    except Exception:
        pass
    return None


def _visible(screen) -> bool:
    try:
        panel = getattr(screen, "_yt_lower_v2_panel", None)
        return bool(
            panel is not None
            and getattr(panel, "parent", None) is not None
            and float(getattr(panel, "height", 0) or 0) >= float(dp(180))
            and float(getattr(panel, "opacity", 0) or 0) > 0.5
        )
    except Exception:
        return False


def _render_live() -> bool:
    try:
        from youtube_lower_panel_v2 import install_youtube_lower_panel_v2
        install_youtube_lower_panel_v2()
    except Exception as exc:
        print("[YT-LOWER-MOUNT] v2 install failed:", exc)
        return False

    screen = _find_audio_screen()
    if screen is None:
        return False

    try:
        renderer = getattr(screen, "_render_youtube_lower_v2", None)
        if callable(renderer):
            renderer()
    except Exception as exc:
        print("[YT-LOWER-MOUNT] render failed:", exc)
        return False

    if _visible(screen):
        try:
            outer = screen.ids.get("player_details_scroll")
            if outer is not None:
                outer.do_scroll_y = True
                outer.disabled = False
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass
        except Exception:
            pass
        return True
    return False


def install_youtube_lower_panel_mount_fix() -> bool:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    attempts = {"count": 0}

    def tick(_dt):
        attempts["count"] += 1
        if _render_live():
            print("[YT-LOWER-MOUNT] live panel confirmed")
            return False
        return attempts["count"] < 150

    Clock.schedule_interval(tick, 0.20)
    Clock.schedule_once(lambda _dt: _render_live(), 0)
    return True
