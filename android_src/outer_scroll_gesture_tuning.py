"""Tune the one real player ScrollView for Android finger gestures.

After the nested playlist ScrollView was removed, playlist rows are direct
children of the outer player_details_scroll. Each row is a ButtonBehavior.
Kivy ScrollView decides whether a gesture is a scroll only while the touch is
inside scroll_timeout and before it hands the touch to the child. The stock
Android/Kivy defaults are too aggressive for these tappable rows: a normal
finger drag can time out and become a row tap/grab, while a very fast flick or
a touch that starts in the tiny spacing between rows still scrolls.

This module only tunes gesture classification on the single outer ScrollView.
It does not wrap touch methods, move scroll_y manually, or reintroduce nested
scrolling.
"""
from __future__ import annotations

import threading

_PATCHED = False
_LOCK = threading.RLock()


def install_outer_scroll_gesture_tuning() -> bool:
    global _PATCHED

    with _LOCK:
        if _PATCHED:
            return True

        try:
            import audio_screen
            from kivy.clock import Clock
            from kivy.metrics import dp
        except Exception:
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if not bool(getattr(cls, "_pymusic_clean_single_scroll_v1", False)):
            return False
        if bool(getattr(cls, "_pymusic_outer_scroll_gesture_v1", False)):
            _PATCHED = True
            return True

        def configure(owner, *, log=False):
            try:
                outer = owner.ids.get("player_details_scroll")
                if outer is None:
                    return False

                # Give the finger enough time to be recognised as a drag before
                # ButtonBehavior rows receive/grab the touch. A small distance
                # makes ordinary vertical movement win quickly without making
                # stationary taps feel like scrolls.
                outer.scroll_timeout = 220
                outer.scroll_distance = dp(5)
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                outer.disabled = False
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass

                if log:
                    print(
                        "[OUTER-SCROLL] Android gesture tuning active "
                        f"timeout={float(outer.scroll_timeout):.0f}ms "
                        f"distance={float(outer.scroll_distance):.1f}px"
                    )
                return True
            except Exception as exc:
                print("[OUTER-SCROLL] configure failed:", exc)
                return False

        old_on_kv_post = cls.on_kv_post
        old_pre_enter = cls.on_pre_enter

        def on_kv_post_tuned(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            for delay in (0.0, 0.05, 0.20):
                Clock.schedule_once(
                    lambda _dt, owner=self, final=(delay == 0.20): configure(owner, log=final),
                    delay,
                )
            return result

        def pre_enter_tuned(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt, owner=self: configure(owner, log=True), 0)
            return result

        cls.on_kv_post = on_kv_post_tuned
        cls.on_pre_enter = pre_enter_tuned
        cls._pymusic_outer_scroll_gesture_v1 = True

        _PATCHED = True
        print("[OUTER-SCROLL] gesture classifier tuning installed")
        return True
