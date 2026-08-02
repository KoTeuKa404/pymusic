"""Prevent the player details ScrollViews from getting stuck in overscroll.

The player contains nested ScrollViews.  Kivy's default DampedScrollEffect can
leave the outer content translated downward when a drag reaches the top while
an inner playlist is also handling the gesture.  The result is a large blank
area above the title.  Use the non-elastic ScrollEffect and normalize all
scroll state after gesture/layout changes.
"""
from __future__ import annotations

import sys
import threading

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_scroll_bounds() -> bool:
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
        if not getattr(player_cls, "_pymusic_runtime_stability_v1", False):
            return False
        if getattr(player_cls, "_pymusic_scroll_bounds_v1", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        try:
            from kivy.effects.scroll import ScrollEffect
        except Exception:
            ScrollEffect = None

        def normalize(scroll, _dt=0) -> None:
            if scroll is None:
                return
            if bool(getattr(scroll, "_pymusic_normalizing", False)):
                return
            scroll._pymusic_normalizing = True
            try:
                try:
                    scroll.always_overscroll = False
                except Exception:
                    pass
                try:
                    scroll.smooth_scroll_end = 0
                except Exception:
                    pass

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
                clamped = max(0.0, min(1.0, value))
                if abs(clamped - value) > 0.0001:
                    scroll.scroll_y = clamped

                # Recalculate the child position immediately.  This clears a
                # stale transform even when scroll_y itself already looks valid.
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
            finally:
                scroll._pymusic_normalizing = False

        def queue_normalize(scroll, delay=0.0) -> None:
            try:
                old = getattr(scroll, "_pymusic_normalize_event", None)
                if old is not None:
                    old.cancel()
            except Exception:
                pass
            try:
                scroll._pymusic_normalize_event = Clock.schedule_once(
                    lambda dt: normalize(scroll, dt), delay
                )
            except Exception:
                pass

        def configure(scroll) -> None:
            if scroll is None:
                return
            if not bool(getattr(scroll, "_pymusic_nonelastic_v1", False)):
                try:
                    if ScrollEffect is not None:
                        scroll.effect_cls = ScrollEffect
                except Exception as exc:
                    print("[SCROLL] failed to set non-elastic effect:", exc)
                try:
                    scroll.always_overscroll = False
                except Exception:
                    pass
                try:
                    scroll.smooth_scroll_end = 0
                except Exception:
                    pass
                try:
                    scroll.bind(
                        on_scroll_stop=lambda widget, *_a: queue_normalize(
                            widget, 0
                        )
                    )
                except Exception:
                    pass
                try:
                    scroll.bind(
                        size=lambda widget, *_a: queue_normalize(widget, 0),
                    )
                except Exception:
                    pass
                try:
                    child = scroll.children[0] if scroll.children else None
                    if child is not None:
                        child.bind(
                            height=lambda *_a, s=scroll: queue_normalize(s, 0)
                        )
                        if hasattr(child, "minimum_height"):
                            child.bind(
                                minimum_height=lambda *_a, s=scroll: queue_normalize(
                                    s, 0
                                )
                            )
                except Exception:
                    pass
                scroll._pymusic_nonelastic_v1 = True

            queue_normalize(scroll, 0)
            queue_normalize(scroll, 0.06)

        def configure_all(self, *_args) -> None:
            for widget_id in (
                "player_details_scroll",
                "playlist_scroll",
                "similar_scroll",
                "title_scroll",
            ):
                try:
                    configure(self.ids.get(widget_id))
                except Exception:
                    pass

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def init_with_scroll_bounds(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: configure_all(self), 0)
            Clock.schedule_once(lambda _dt: configure_all(self), 0.15)

        def pre_enter_with_scroll_bounds(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: configure_all(self), 0)
            Clock.schedule_once(lambda _dt: configure_all(self), 0.12)
            return result

        def resume_with_scroll_bounds(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.05, 0.15, 0.35):
                Clock.schedule_once(lambda _dt: configure_all(self), delay)
            return result

        player_cls.__init__ = init_with_scroll_bounds
        player_cls.on_pre_enter = pre_enter_with_scroll_bounds
        player_cls.handle_app_resume = resume_with_scroll_bounds
        player_cls._pymusic_scroll_bounds_v1 = True
        _PATCHED = True
        print("[HOTFIX] non-elastic bounded player scrolling v1 enabled")
        return True


_patch_scroll_bounds()
