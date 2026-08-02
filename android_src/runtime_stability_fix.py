"""Playback/layout stability fixes that must survive Kivy frame stalls.

The audio/video clocks are independent Android MediaPlayer instances.  The
existing synchronizer was driven by Kivy Clock, so a heavy playlist scroll
could temporarily stop sync decisions.  This patch moves those decisions to a
small daemon watchdog and keeps the title/views block tightly packed.
"""
from __future__ import annotations

import sys
import threading
import time
import weakref

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_runtime_stability() -> bool:
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
        if not getattr(player_cls, "_pymusic_video_sync_v2", False):
            return False
        if not getattr(player_cls, "_pymusic_playlist_scroll_v7", False):
            return False
        if getattr(player_cls, "_pymusic_runtime_stability_v1", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def stop_effect(scroll) -> None:
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

        def apply_compact_metadata(self, _dt=0) -> None:
            """Remove the fixed 52dp title viewport and its empty lower half."""
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                if title_view is None or title is None or views is None:
                    return

                width = max(dp(1), float(title_view.width or 0))
                title.text_size = (width, None)
                try:
                    title.texture_update()
                except Exception:
                    pass

                title_text = str(title.text or "").strip()
                title_h = float((title.texture_size or (0, 0))[1] or 0)
                if title_text:
                    # Exact texture height for one line; up to two lines for a
                    # long title. No artificial 27/52dp reserve remains.
                    viewport_h = max(dp(22), min(dp(50), title_h or dp(22)))
                    content_h = max(viewport_h, title_h)
                else:
                    viewport_h = 0
                    content_h = 0

                title_view.height = viewport_h
                title.height = content_h
                title_view.do_scroll_x = False
                title_view.do_scroll_y = content_h > viewport_h + dp(1)
                title_view.scroll_y = 1.0
                try:
                    title_view.always_overscroll = False
                    title_view.bar_width = 0
                except Exception:
                    pass
                stop_effect(title_view)

                views_text = str(views.text or "").strip()
                try:
                    views.texture_update()
                except Exception:
                    pass
                views_h = float((views.texture_size or (0, 0))[1] or 0)
                views.height = max(dp(17), views_h) if views_text else 0

                details = title_view.parent
                if details is not None:
                    try:
                        details.spacing = dp(1)
                    except Exception:
                        pass
                    try:
                        details.padding = (dp(16), dp(4), dp(16), dp(3))
                    except Exception:
                        pass
                    try:
                        trigger = getattr(details, "_trigger_layout", None)
                        if callable(trigger):
                            trigger()
                    except Exception:
                        pass
            except Exception as exc:
                print("[TITLE] compact metadata layout failed:", exc)

        def queue_compact_metadata(self, *_args) -> None:
            try:
                event = getattr(self, "_pymusic_metadata_layout_event", None)
                if event is not None:
                    event.cancel()
            except Exception:
                pass
            self._pymusic_metadata_layout_event = Clock.schedule_once(
                lambda dt: apply_compact_metadata(self, dt), 0
            )

        def bind_compact_metadata(self) -> None:
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                if title_view is None or title is None or views is None:
                    return
                if not getattr(title_view, "_pymusic_metadata_layout_v1", False):
                    title.bind(text=lambda *_a: queue_compact_metadata(self))
                    title.bind(texture_size=lambda *_a: queue_compact_metadata(self))
                    title_view.bind(width=lambda *_a: queue_compact_metadata(self))
                    views.bind(text=lambda *_a: queue_compact_metadata(self))
                    views.bind(texture_size=lambda *_a: queue_compact_metadata(self))
                    title_view._pymusic_metadata_layout_v1 = True
                queue_compact_metadata(self)
                Clock.schedule_once(lambda dt: apply_compact_metadata(self, dt), 0.06)
            except Exception as exc:
                print("[TITLE] compact metadata binding failed:", exc)

        def stop_old_clock_sync(self) -> None:
            event = getattr(self, "_pymusic_video_sync_ev", None)
            if event is not None:
                try:
                    event.cancel()
                except Exception:
                    pass
            self._pymusic_video_sync_ev = None

        def start_sync_watchdog(self) -> None:
            """Run sync decisions outside Kivy's render/scroll event loop."""
            stop_old_clock_sync(self)
            current = getattr(self, "_pymusic_sync_thread", None)
            if current is not None and current.is_alive():
                return

            generation = int(
                getattr(self, "_pymusic_sync_thread_generation", 0)
            ) + 1
            self._pymusic_sync_thread_generation = generation
            owner_ref = weakref.ref(self)

            def worker() -> None:
                next_tick = time.monotonic()
                while True:
                    owner = owner_ref()
                    if owner is None:
                        return
                    if generation != int(
                        getattr(owner, "_pymusic_sync_thread_generation", -1)
                    ):
                        return
                    try:
                        tick = getattr(owner, "_pymusic_video_sync_tick", None)
                        if callable(tick):
                            # Access through the instance returns a bound method.
                            tick(0)
                    except Exception as exc:
                        now = time.monotonic()
                        last = float(
                            getattr(owner, "_pymusic_sync_watchdog_log", 0.0) or 0.0
                        )
                        if now - last >= 8.0:
                            print("[VIDEO] independent sync watchdog failed:", exc)
                            owner._pymusic_sync_watchdog_log = now

                    # 25 Hz. Use an absolute deadline so a slow iteration does
                    # not permanently lower the sampling rate.
                    next_tick += 0.04
                    now = time.monotonic()
                    if next_tick < now - 0.20:
                        next_tick = now
                    time.sleep(max(0.005, next_tick - now))

            thread = threading.Thread(
                target=worker,
                name="pymusic-av-sync-watchdog",
                daemon=True,
            )
            self._pymusic_sync_thread = thread
            thread.start()

        old_init = player_cls.__init__
        old_sync_ui = player_cls._sync_ui_loaded
        old_resume = player_cls.handle_app_resume

        def init_with_stability(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: bind_compact_metadata(self), 0)
            Clock.schedule_once(lambda _dt: bind_compact_metadata(self), 0.12)
            start_sync_watchdog(self)

        def sync_ui_with_compact_metadata(self, *args, **kwargs):
            result = old_sync_ui(self, *args, **kwargs)
            queue_compact_metadata(self)
            return result

        def resume_with_stability(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            start_sync_watchdog(self)
            for delay in (0.0, 0.06, 0.18, 0.40):
                Clock.schedule_once(
                    lambda dt: apply_compact_metadata(self, dt), delay
                )
            return result

        player_cls.__init__ = init_with_stability
        player_cls._sync_ui_loaded = sync_ui_with_compact_metadata
        player_cls.handle_app_resume = resume_with_stability
        player_cls._pymusic_runtime_stability_v1 = True
        _PATCHED = True
        print("[HOTFIX] UI-independent AV sync + compact metadata v1 enabled")
        return True


_patch_runtime_stability()
