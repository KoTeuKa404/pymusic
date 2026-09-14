"""Keep complete playlist swipe gestures inside the nested ScrollView.

The v9 playlist patch correctly avoids letting the outer player ScrollView own
``on_touch_down`` when a gesture starts inside ``playlist_scroll``.  However,
``on_touch_move`` and ``on_touch_up`` were still handled by the outer
ScrollView.  On Android that can make the parent steal/cancel the gesture after
the initial touch, leaving the playlist visible but impossible to swipe.

This patch keeps the entire DOWN -> MOVE -> UP sequence on the normal Widget
child-dispatch path whenever it starts inside a scrollable playlist.  It does
not touch the native video SurfaceView or its transport controls.
"""
from __future__ import annotations

import sys
import threading
from types import MethodType

_PATCHED = False
_LOCK = threading.RLock()
_TOUCH_KEY = "_pymusic_playlist_gesture_v10"


def _patch_playlist_gesture() -> bool:
    global _PATCHED

    with _LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False

        player_cls = getattr(module, "AudioPlayerScreen", None)
        if player_cls is None:
            return False

        # v10 is deliberately a follow-up to the existing bounded playlist v9.
        if not bool(getattr(player_cls, "_pymusic_playlist_scroll_v9", False)):
            return False
        if bool(getattr(player_cls, "_pymusic_playlist_gesture_v10", False)):
            _PATCHED = True
            return True

        try:
            from kivy.uix.widget import Widget
        except Exception:
            return False

        dp = module.dp

        def _playlist_can_own(self, touch) -> bool:
            try:
                inner = self.ids.get("playlist_scroll")
                return bool(
                    inner is not None
                    and not bool(getattr(inner, "disabled", False))
                    and bool(getattr(inner, "do_scroll_y", False))
                    and float(getattr(inner, "height", 0) or 0) > dp(1)
                    and inner.collide_point(*touch.pos)
                )
            except Exception:
                return False

        def bind_complete_gesture(self) -> None:
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None:
                    return
                if bool(getattr(outer, "_pymusic_playlist_nested_v10", False)):
                    return

                old_down = outer.on_touch_down
                old_move = outer.on_touch_move
                old_up = outer.on_touch_up

                outer._pymusic_outer_touch_down_v10 = old_down
                outer._pymusic_outer_touch_move_v10 = old_move
                outer._pymusic_outer_touch_up_v10 = old_up

                def outer_touch_down(widget, touch):
                    if _playlist_can_own(self, touch):
                        try:
                            touch.ud[_TOUCH_KEY] = True
                        except Exception:
                            pass
                        # Skip ScrollView.on_touch_down for the parent, but keep
                        # normal child dispatch so playlist_scroll and row taps
                        # work exactly as they do without a nested parent.
                        return Widget.on_touch_down(widget, touch)
                    return old_down(touch)

                def outer_touch_move(widget, touch):
                    try:
                        owned = bool(touch.ud.get(_TOUCH_KEY, False))
                    except Exception:
                        owned = False
                    if owned:
                        # Critical v10 change: do not hand MOVE back to the
                        # outer ScrollView after DOWN was routed to the playlist.
                        return Widget.on_touch_move(widget, touch)
                    return old_move(touch)

                def outer_touch_up(widget, touch):
                    try:
                        owned = bool(touch.ud.pop(_TOUCH_KEY, False))
                    except Exception:
                        owned = False
                    if owned:
                        # Complete the same child-owned gesture path. This also
                        # preserves normal PlaylistTrackItem tap/release events.
                        return Widget.on_touch_up(widget, touch)
                    return old_up(touch)

                outer.on_touch_down = MethodType(outer_touch_down, outer)
                outer.on_touch_move = MethodType(outer_touch_move, outer)
                outer.on_touch_up = MethodType(outer_touch_up, outer)
                outer._pymusic_playlist_nested_v10 = True
                print("[PLAYLIST-V10] complete nested gesture routing installed")
            except Exception as exc:
                print("[PLAYLIST-V10] gesture routing install failed:", exc)

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume
        old_render = player_cls._render_playlist_ui

        def init_v10(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                module.Clock.schedule_once(
                    lambda _dt, owner=self: bind_complete_gesture(owner), delay
                )

        def pre_enter_v10(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18):
                module.Clock.schedule_once(
                    lambda _dt, owner=self: bind_complete_gesture(owner), delay
                )
            return result

        def resume_v10(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                module.Clock.schedule_once(
                    lambda _dt, owner=self: bind_complete_gesture(owner), delay
                )
            return result

        def render_v10(self, *args, **kwargs):
            result = old_render(self, *args, **kwargs)
            bind_complete_gesture(self)
            return result

        player_cls.__init__ = init_v10
        player_cls.on_pre_enter = pre_enter_v10
        player_cls.handle_app_resume = resume_v10
        player_cls._render_playlist_ui = render_v10
        player_cls._pymusic_playlist_gesture_v10 = True

        _PATCHED = True
        print("[HOTFIX] playlist complete-gesture v10 enabled")
        return True


_patch_playlist_gesture()
