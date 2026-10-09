"""Bounded, gesture-isolated playlist scrolling.

The original working Android APK used an independent playlist_scroll viewport.
Keep that layout. Dispatch touches beginning inside the playlist directly to
its children, bypassing only the outer ScrollView's drag recognizer.

Never disable the outer ScrollView, reset an effect or restore scroll_y during
ordinary playback/metadata updates. Those operations previously killed the
next drag, or snapped the queue back immediately after the first swipe.
"""
from __future__ import annotations

import sys
import threading
from types import MethodType

from kivy.uix.widget import Widget

_LOCK = threading.RLock()
_INSTALLED = False


def _patch_playlist_scroll() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        module = sys.modules.get("audio_screen")
        cls = getattr(module, "AudioPlayerScreen", None) if module else None
        if cls is None or not getattr(cls, "_pymusic_hotfix_v4", False):
            return False
        if getattr(cls, "_pymusic_playlist_scroll_v5", False):
            _INSTALLED = True
            return True

        Clock = module.Clock
        dp = module.dp
        original_init = cls.__init__
        original_enter = cls.on_pre_enter
        original_render = cls._render_playlist_ui
        original_toggle = cls.toggle_playlist_collapsed

        def configure(owner):
            outer = owner.ids.get("player_details_scroll")
            inner = owner.ids.get("playlist_scroll")
            listing = owner.ids.get("playlist_list")
            if outer is None or inner is None or listing is None:
                return
            visible = bool(owner.playlist and owner.playlist.tracks)
            collapsed = bool(getattr(owner, "_playlist_collapsed", False))
            expanded = visible and not collapsed

            # Do not touch scroll_y or effect_y here. The renderer may be
            # called continuously as the currently playing track changes.
            inner.size_hint_y = None
            inner.height = dp(240) if expanded else 0
            inner.opacity = 1.0 if expanded else 0.0
            inner.disabled = False
            inner.do_scroll_x = False
            inner.do_scroll_y = True
            inner.scroll_timeout = 180
            inner.scroll_distance = dp(5)
            outer.do_scroll_x = False
            outer.do_scroll_y = True
            listing.size_hint_y = None
            listing.disabled = not expanded

            if getattr(outer, "_pymusic_playlist_direct_dispatch", False):
                return

            key = "_pymusic_playlist_inner_touch_" + str(id(outer))
            old_down = outer.on_touch_down
            old_move = outer.on_touch_move
            old_up = outer.on_touch_up

            def is_inner_target(touch):
                return (
                    inner.parent is not None
                    and inner.height > 0
                    and not bool(getattr(owner, "_playlist_collapsed", False))
                    and inner.collide_point(*touch.pos)
                )

            def touch_down(widget, touch):
                # The default outer ScrollView grabs a touch BEFORE nested
                # ScrollViews can decide who owns it. Bypass that recognizer
                # only when DOWN starts inside the playlist viewport.
                if is_inner_target(touch):
                    touch.ud[key] = True
                    handled = Widget.on_touch_down(widget, touch)
                    print(
                        "[PLAYLIST-TOUCH] down delegated "
                        f"inner_y={float(inner.scroll_y):.3f} "
                        f"range={max(0.0, float(listing.height)-float(inner.height)):.1f} "
                        f"handled={bool(handled)}"
                    )
                    return handled
                return old_down(touch)

            def touch_move(widget, touch):
                if touch.ud.get(key):
                    return Widget.on_touch_move(widget, touch)
                return old_move(touch)

            def touch_up(widget, touch):
                if touch.ud.pop(key, False):
                    handled = Widget.on_touch_up(widget, touch)
                    print(
                        "[PLAYLIST-TOUCH] up delegated "
                        f"inner_y={float(inner.scroll_y):.3f} "
                        f"outer_enabled={bool(outer.do_scroll_y)}"
                    )
                    return handled
                return old_up(touch)

            touch_down.__name__ = "on_touch_down"
            touch_move.__name__ = "on_touch_move"
            touch_up.__name__ = "on_touch_up"
            outer.on_touch_down = MethodType(touch_down, outer)
            outer.on_touch_move = MethodType(touch_move, outer)
            outer.on_touch_up = MethodType(touch_up, outer)
            outer._pymusic_playlist_direct_dispatch = True
            print("[PLAYLIST-TOUCH] isolated inner gestures without disabling outer")

        def init_fixed(self, *args, **kwargs):
            result = original_init(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: configure(self), 0)
            return result

        def enter_fixed(self, *args, **kwargs):
            result = original_enter(self, *args, **kwargs)
            configure(self)
            return result

        def render_fixed(self, *args, **kwargs):
            result = original_render(self, *args, **kwargs)
            configure(self)
            return result

        def toggle_fixed(self, *args, **kwargs):
            result = original_toggle(self, *args, **kwargs)
            configure(self)
            return result

        # Kivy WeakMethod looks up the callback by name on the class.
        init_fixed.__name__ = "__init__"
        enter_fixed.__name__ = "on_pre_enter"
        render_fixed.__name__ = "_render_playlist_ui"
        toggle_fixed.__name__ = "toggle_playlist_collapsed"
        cls.__init__ = init_fixed
        cls.on_pre_enter = enter_fixed
        cls._render_playlist_ui = render_fixed
        cls.toggle_playlist_collapsed = toggle_fixed

        cls._pymusic_playlist_scroll_v5 = True
        _INSTALLED = True
        print("[PLAYLIST-TOUCH] bounded independent playlist owner installed")
        return True


_patch_playlist_scroll()
