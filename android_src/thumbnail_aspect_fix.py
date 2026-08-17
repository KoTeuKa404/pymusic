"""Keep preview aspect ratio and bootstrap final lower-player UI patches."""
from __future__ import annotations

import threading
import time

from kivy.clock import Clock

_PATCHED = False
_STARTED = False
_LOCK = threading.RLock()


def _start_lower_panel() -> None:
    """Install data hooks, then render the UI in the permanent KV slot."""
    try:
        from youtube_lower_panel_fix import install_youtube_lower_panel_fix
        if not install_youtube_lower_panel_fix():
            print("[YT-LOWER] install returned false")
    except Exception as exc:
        print("[YT-LOWER] install failed:", exc)

    # V3 deliberately replaces the old runtime-mounted V2 panel.  The player
    # already owns similar_scroll/similar_list in youtube_gui.kv, so using that
    # static slot avoids lifecycle races and ScrollView child-order problems.
    try:
        from youtube_lower_panel_static_v3 import install_youtube_lower_panel_static_v3
        if not install_youtube_lower_panel_static_v3():
            print("[YT-LOWER-V3] install returned false")
    except Exception as exc:
        print("[YT-LOWER-V3] install failed:", exc)


def _apply_thumb_ratio(owner) -> None:
    try:
        thumb = owner.ids.get("audio_thumbnail")
        if thumb is None:
            return

        thumb.allow_stretch = True
        try:
            thumb.keep_ratio = True
        except Exception:
            pass
        try:
            if hasattr(thumb, "fit_mode"):
                thumb.fit_mode = "contain"
        except Exception:
            pass

        if not bool(getattr(thumb, "_pymusic_ratio_guard_v1", False)):
            def keep_ratio_guard(instance, value):
                if bool(value):
                    return
                Clock.schedule_once(
                    lambda _dt, widget=instance: setattr(widget, "keep_ratio", True),
                    0,
                )

            try:
                thumb.bind(keep_ratio=keep_ratio_guard)
            except Exception:
                pass
            thumb._pymusic_ratio_guard_v1 = True

        try:
            thumb.canvas.ask_update()
        except Exception:
            pass
    except Exception as exc:
        print("[THUMB-ASPECT] apply failed:", exc)


def _install_now() -> bool:
    global _PATCHED
    with _LOCK:
        if _PATCHED:
            _start_lower_panel()
            return True

        try:
            import audio_screen
            cls = getattr(audio_screen, "AudioPlayerScreen", None)
            if cls is None:
                return False
            if not bool(getattr(cls, "_pymusic_final_player_v2", False)):
                return False

            if bool(getattr(cls, "_pymusic_thumb_aspect_v1", False)):
                _PATCHED = True
                _start_lower_panel()
                return True

            old_init = cls.__init__
            old_pre_enter = cls.on_pre_enter
            old_resume = cls.handle_app_resume
            old_sync_loaded = cls._sync_ui_loaded
            old_sync_loading = cls._sync_ui_loading
            old_align = cls._align_video_to_thumb

            def queue(owner):
                for delay in (0.0, 0.03, 0.12, 0.52):
                    Clock.schedule_once(
                        lambda _dt, current=owner: _apply_thumb_ratio(current),
                        delay,
                    )

            def init_fixed(self, *args, **kwargs):
                result = old_init(self, *args, **kwargs)
                queue(self)
                return result

            def pre_enter_fixed(self, *args, **kwargs):
                result = old_pre_enter(self, *args, **kwargs)
                queue(self)
                return result

            def resume_fixed(self, *args, **kwargs):
                result = old_resume(self, *args, **kwargs)
                queue(self)
                return result

            def sync_loaded_fixed(self, *args, **kwargs):
                result = old_sync_loaded(self, *args, **kwargs)
                _apply_thumb_ratio(self)
                return result

            def sync_loading_fixed(self, *args, **kwargs):
                result = old_sync_loading(self, *args, **kwargs)
                _apply_thumb_ratio(self)
                return result

            def align_fixed(self, *args, **kwargs):
                result = old_align(self, *args, **kwargs)
                _apply_thumb_ratio(self)
                return result

            cls.__init__ = init_fixed
            cls.on_pre_enter = pre_enter_fixed
            cls.handle_app_resume = resume_fixed
            cls._sync_ui_loaded = sync_loaded_fixed
            cls._sync_ui_loading = sync_loading_fixed
            cls._align_video_to_thumb = align_fixed
            cls._pymusic_thumb_aspect_v1 = True

            _PATCHED = True
            print("[THUMB-ASPECT] original preview proportions enabled")
            _start_lower_panel()
            return True
        except Exception as exc:
            print("[THUMB-ASPECT] install failed:", exc)
            return False


def install_thumbnail_aspect_fix() -> bool:
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
        print("[THUMB-ASPECT] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-thumbnail-aspect",
        daemon=True,
    ).start()
    return True
