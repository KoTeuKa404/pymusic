"""Use the player details ScrollView as the only vertical playlist scroller.

V9-V11 made ``playlist_scroll`` a bounded nested ScrollView.  That works on
some Kivy builds, but on Android the parent and child ScrollViews can still
trade ownership of the same finger gesture.  The visible symptom is that the
queue moves by only a few rows and then appears to stop, especially after the
native video surface becomes visible.

The queue is already rendered completely (in small chunks), so a second
vertical ScrollView is unnecessary.  V12 expands ``playlist_scroll`` to the
exact height of every rendered row and disables scrolling on that inner view.
The existing ``player_details_scroll`` therefore owns the whole page from
metadata through the final playlist row.  Native video/SurfaceView touch
handling is not modified here.
"""
from __future__ import annotations

import sys
import threading

_PATCHED = False
_LOCK = threading.RLock()


def _patch_playlist_single_scroll() -> bool:
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
        if not bool(getattr(player_cls, "_pymusic_playlist_extent_v11", False)):
            return False
        if bool(getattr(player_cls, "_pymusic_playlist_single_scroll_v12", False)):
            _PATCHED = True
            return True

        Clock = module.Clock

        def _spacing_y(widget) -> float:
            try:
                spacing = getattr(widget, "spacing", 0) or 0
                if isinstance(spacing, (list, tuple)):
                    return float(spacing[-1] or 0) if spacing else 0.0
                return float(spacing)
            except Exception:
                return 0.0

        def _padding_y(widget) -> float:
            try:
                padding = getattr(widget, "padding", 0) or 0
                if isinstance(padding, (int, float)):
                    return float(padding) * 2.0
                values = list(padding)
                if len(values) >= 4:
                    return float(values[1] or 0) + float(values[3] or 0)
                if len(values) == 2:
                    return float(values[1] or 0) * 2.0
                if len(values) == 1:
                    return float(values[0] or 0) * 2.0
            except Exception:
                pass
            return 0.0

        def _rendered_height(listing) -> float:
            try:
                children = list(getattr(listing, "children", []) or [])
            except Exception:
                children = []

            total = 0.0
            for child in children:
                try:
                    total += max(0.0, float(getattr(child, "height", 0) or 0))
                except Exception:
                    pass
            if len(children) > 1:
                total += _spacing_y(listing) * float(len(children) - 1)
            total += _padding_y(listing)

            try:
                total = max(total, float(getattr(listing, "minimum_height", 0) or 0))
            except Exception:
                pass
            return max(0.0, total)

        def _request_layout(widget) -> None:
            if widget is None:
                return
            try:
                trigger = getattr(widget, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass
            try:
                widget.canvas.ask_update()
            except Exception:
                pass

        def apply_single_scroll(self, _dt=0) -> None:
            try:
                viewport = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                outer = self.ids.get("player_details_scroll")
                if viewport is None or listing is None or outer is None:
                    return

                has_tracks = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                expanded = bool(has_tracks and not collapsed)

                if not expanded:
                    viewport.height = 0
                    viewport.opacity = 0
                    viewport.disabled = True
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = False
                    try:
                        viewport.bar_width = 0
                    except Exception:
                        pass
                else:
                    content_h = _rendered_height(listing)
                    listing.size_hint_y = None
                    listing.height = content_h

                    # One vertical scroll owner only: the inner viewport is just
                    # a full-height container, while player_details_scroll moves
                    # the complete page.
                    viewport.size_hint_y = None
                    viewport.height = content_h
                    viewport.opacity = 1
                    viewport.disabled = False
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = False
                    try:
                        viewport.always_overscroll = False
                        viewport.bar_width = 0
                        viewport.scroll_y = 1.0
                    except Exception:
                        pass

                outer.disabled = False
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass

                _request_layout(listing)
                _request_layout(viewport)
                _request_layout(getattr(viewport, "parent", None))
                try:
                    _request_layout(outer.children[0] if outer.children else None)
                except Exception:
                    pass
                _request_layout(outer)
            except Exception as exc:
                try:
                    print("[PLAYLIST-V12] single-scroll geometry failed:", exc)
                except Exception:
                    pass

        def queue_apply(self, delay=0.0) -> None:
            try:
                Clock.schedule_once(
                    lambda dt, owner=self: apply_single_scroll(owner, dt),
                    max(0.0, float(delay)),
                )
            except Exception:
                pass

        def bind_single_scroll(self) -> None:
            try:
                listing = self.ids.get("playlist_list")
                viewport = self.ids.get("playlist_scroll")
                if listing is None or viewport is None:
                    return

                if not bool(getattr(listing, "_pymusic_single_scroll_v12_bound", False)):
                    def changed(*_args):
                        # V9 calls its bounded geometry immediately after each
                        # chunk. Run on the next frame so our full-height geometry
                        # is the final owner.
                        queue_apply(self, 0)

                    listing.bind(children=changed)
                    try:
                        listing.bind(minimum_height=changed)
                    except Exception:
                        pass
                    listing._pymusic_single_scroll_v12_bound = True
                    print("[PLAYLIST-V12] single outer-scroll playlist installed")

                queue_apply(self, 0)
            except Exception as exc:
                try:
                    print("[PLAYLIST-V12] binding failed:", exc)
                except Exception:
                    pass

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume
        old_render = player_cls._render_playlist_ui
        old_toggle = player_cls.toggle_playlist_collapsed

        def init_v12(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt, owner=self: bind_single_scroll(owner), delay)

        def pre_enter_v12(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18, 0.40):
                queue_apply(self, delay)
            return result

        def resume_v12(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22, 0.45):
                queue_apply(self, delay)
            return result

        def render_v12(self, *args, **kwargs):
            result = old_render(self, *args, **kwargs)
            bind_single_scroll(self)
            # V9 has its own delayed geometry at 0.04/0.14 and V11 also queues
            # reconciliation. Re-assert single-scroll after all of them.
            for delay in (0.0, 0.03, 0.08, 0.18, 0.35, 0.60):
                queue_apply(self, delay)
            return result

        def toggle_v12(self, *args, **kwargs):
            result = old_toggle(self, *args, **kwargs)
            for delay in (0.0, 0.04, 0.18, 0.35):
                queue_apply(self, delay)
            return result

        player_cls.__init__ = init_v12
        player_cls.on_pre_enter = pre_enter_v12
        player_cls.handle_app_resume = resume_v12
        player_cls._render_playlist_ui = render_v12
        player_cls.toggle_playlist_collapsed = toggle_v12
        player_cls._pymusic_playlist_single_scroll_v12 = True

        _PATCHED = True
        print("[HOTFIX] playlist single-scroll v12 enabled")
        return True


_patch_playlist_single_scroll()
