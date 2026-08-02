"""Restore the Kivy player details after Android screen-off/resume.

The original resume path forcibly cleared and rebuilt playlist/recommendation
widgets while the ScrollView still held its old kinetic offset.  On some
Android devices this leaves the details viewport positioned outside its newly
laid-out content, so only the native video SurfaceView remains visible.
"""
from __future__ import annotations

import sys
import threading
import time

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_resume_ui() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        if player_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_playlist_scroll_v7", False):
            return False
        if getattr(player_cls, "_pymusic_resume_ui_v1", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        Window = module.Window
        old_pause = player_cls.handle_app_pause

        def stop_scroll(scroll) -> None:
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
            except Exception:
                pass

        def save_scroll_state(self) -> None:
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    self._pymusic_resume_outer_y = max(
                        0.0, min(1.0, float(outer.scroll_y))
                    )
                    stop_scroll(outer)
            except Exception:
                self._pymusic_resume_outer_y = 1.0
            try:
                playlist = self.ids.get("playlist_scroll")
                if playlist is not None:
                    self._pymusic_resume_playlist_y = max(
                        0.0, min(1.0, float(playlist.scroll_y))
                    )
                    stop_scroll(playlist)
            except Exception:
                self._pymusic_resume_playlist_y = 1.0
            try:
                similar = self.ids.get("similar_scroll")
                if similar is not None:
                    self._pymusic_resume_similar_y = max(
                        0.0, min(1.0, float(similar.scroll_y))
                    )
                    stop_scroll(similar)
            except Exception:
                self._pymusic_resume_similar_y = 1.0

        def pause_with_saved_layout(self):
            save_scroll_state(self)
            return old_pause(self)

        def request_redraw(widget) -> None:
            if widget is None:
                return
            try:
                widget.canvas.ask_update()
            except Exception:
                pass
            try:
                trigger = getattr(widget, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass
            try:
                do_layout = getattr(widget, "do_layout", None)
                if callable(do_layout):
                    do_layout()
            except Exception:
                pass

        def restore_viewport(self, _dt=0, final=False) -> None:
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None:
                    return
                outer.opacity = 1
                outer.disabled = False
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass

                content = outer.children[0] if outer.children else None
                if content is not None:
                    content.opacity = 1
                    content.disabled = False
                    minimum = float(getattr(content, "minimum_height", 0) or 0)
                    if minimum > 0:
                        content.height = minimum
                    request_redraw(content)

                saved = max(
                    0.0,
                    min(1.0, float(getattr(self, "_pymusic_resume_outer_y", 1.0))),
                )
                content_h = float(getattr(content, "height", 0) or 0) if content is not None else 0
                if content_h <= float(outer.height or 0) + 1:
                    saved = 1.0
                outer.scroll_y = saved
                stop_scroll(outer)
                try:
                    trigger_scroll = getattr(outer, "_trigger_update_from_scroll", None)
                    if callable(trigger_scroll):
                        trigger_scroll()
                except Exception:
                    pass
                request_redraw(outer)

                for widget_id, attr in (
                    ("playlist_scroll", "_pymusic_resume_playlist_y"),
                    ("similar_scroll", "_pymusic_resume_similar_y"),
                ):
                    nested = self.ids.get(widget_id)
                    if nested is None:
                        continue
                    try:
                        nested.always_overscroll = False
                    except Exception:
                        pass
                    nested.scroll_y = max(
                        0.0,
                        min(1.0, float(getattr(self, attr, 1.0))),
                    )
                    stop_scroll(nested)
                    request_redraw(nested)

                request_redraw(self)
                try:
                    Window.canvas.ask_update()
                except Exception:
                    pass

                if final:
                    print(
                        "[RESUME] player viewport restored "
                        f"scroll_y={float(outer.scroll_y):.3f} "
                        f"content_h={content_h:.1f} viewport_h={float(outer.height or 0):.1f}"
                    )
            except Exception as exc:
                print("[RESUME] viewport restore failed:", exc)

        def resume_video(self) -> None:
            try:
                video_resume_started = False
                if (
                    self._video_was_active
                    and self._video_resume_url
                    and self._video_resume_url == self._last_video_url
                    and int(self._video_resume_gen) == int(self._load_gen)
                ):
                    self._video_was_active = False
                    if self._last_video_url and self._video_enabled:
                        threading.Thread(
                            target=lambda: self._auto_video_for_current(
                                self._load_gen, sync_start=False
                            ),
                            daemon=True,
                        ).start()
                        video_resume_started = True
                else:
                    self._video_was_active = False

                if (
                    not video_resume_started
                    and self._playback_desired
                    and not self._user_paused
                    and self._video_enabled
                    and self._last_video_url
                    and not self._video_active
                ):
                    threading.Thread(
                        target=lambda: self._auto_video_for_current(
                            self._load_gen, sync_start=False
                        ),
                        daemon=True,
                    ).start()
            except Exception as exc:
                print("[RESUME] video restore failed:", exc)

        def resume_without_destructive_rebuild(self):
            self._app_in_background = False
            self._last_bg_resume_ts = time.time()
            self._last_watch_pos = None
            self._buffer_watchdog_ts = time.time()

            # Keep existing rows/textures alive. Rebuilding them while Android
            # restores the GL surface is what caused the blank details viewport.
            try:
                self._sync_ui_loaded()
            except Exception:
                pass
            try:
                self._render_playlist_ui(force=False)
            except Exception:
                pass
            try:
                self._render_similar_ui(force=False)
            except Exception:
                pass
            try:
                self._sync_thumb_now()
            except Exception:
                pass

            restore_viewport(self)
            for delay in (0.0, 0.04, 0.10, 0.22, 0.45):
                Clock.schedule_once(
                    lambda dt, last=(delay == 0.45): restore_viewport(
                        self, dt, final=last
                    ),
                    delay,
                )

            try:
                self._ensure_video_player()
            except Exception:
                pass
            for delay in (0.05, 0.16, 0.35):
                Clock.schedule_once(self._align_video_to_thumb, delay)
            Clock.schedule_once(lambda _dt: resume_video(self), 0.06)
            try:
                sync_tick = getattr(self, "_pymusic_video_sync_tick", None)
                if callable(sync_tick):
                    Clock.schedule_once(lambda dt: sync_tick(self, dt), 0.40)
            except Exception:
                pass

        player_cls.handle_app_pause = pause_with_saved_layout
        player_cls.handle_app_resume = resume_without_destructive_rebuild
        player_cls._pymusic_resume_ui_v1 = True
        _PATCHED = True
        print("[HOTFIX] screen-wake player UI restore v1 enabled")
        return True


_patch_resume_ui()
