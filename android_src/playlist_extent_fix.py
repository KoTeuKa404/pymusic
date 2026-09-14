"""Keep the playlist ScrollView extent synced with all rendered rows.

The playlist renderer adds rows in small chunks.  On KivyMD 1.2.0 the MDList
``minimum_height`` can lag behind those incremental ``add_widget`` calls after
v9 switches the list to an explicit height.  The visible playlist then scrolls
for a few rows and stops because ScrollView believes its child is shorter than
it really is.

This patch never touches Android SurfaceView/native controls.  It derives the
list height directly from the actual rendered row widgets and updates the inner
ScrollView whenever rows are added or their geometry changes.
"""
from __future__ import annotations

import sys
import threading

_PATCHED = False
_LOCK = threading.RLock()


def _patch_playlist_extent() -> bool:
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
        if not bool(getattr(player_cls, "_pymusic_playlist_gesture_v10", False)):
            return False
        if bool(getattr(player_cls, "_pymusic_playlist_extent_v11", False)):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def _spacing_y(widget) -> float:
            try:
                spacing = getattr(widget, "spacing", 0) or 0
                if isinstance(spacing, (list, tuple)):
                    if not spacing:
                        return 0.0
                    return float(spacing[-1] or 0)
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

        def _actual_rows_height(listing) -> float:
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
            return max(0.0, total)

        def sync_extent(self, _dt=0) -> None:
            try:
                listing = self.ids.get("playlist_list")
                viewport = self.ids.get("playlist_scroll")
                if listing is None or viewport is None:
                    return

                actual = _actual_rows_height(listing)
                try:
                    minimum = max(0.0, float(getattr(listing, "minimum_height", 0) or 0))
                except Exception:
                    minimum = 0.0

                # Actual child geometry is authoritative. minimum_height remains
                # a useful fallback for the frame in which a new row has not yet
                # received its final Kivy size.
                wanted = max(actual, minimum)
                listing.size_hint_y = None
                if abs(float(getattr(listing, "height", 0) or 0) - wanted) > 0.5:
                    listing.height = wanted

                view_h = max(0.0, float(getattr(viewport, "height", 0) or 0))
                if not bool(getattr(viewport, "disabled", False)) and view_h > dp(1):
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = bool(wanted > view_h + dp(2))
                    try:
                        viewport.bar_width = dp(3) if viewport.do_scroll_y else 0
                    except Exception:
                        pass

                try:
                    trigger = getattr(listing, "_trigger_layout", None)
                    if callable(trigger):
                        trigger()
                except Exception:
                    pass
                try:
                    parent = getattr(viewport, "parent", None)
                    trigger = getattr(parent, "_trigger_layout", None)
                    if callable(trigger):
                        trigger()
                except Exception:
                    pass
                try:
                    # Re-assigning the normalized value forces ScrollView to
                    # reconcile its effect bounds after child height changes.
                    viewport.scroll_y = max(0.0, min(1.0, float(viewport.scroll_y)))
                except Exception:
                    pass
                try:
                    listing.canvas.ask_update()
                    viewport.canvas.ask_update()
                except Exception:
                    pass
            except Exception as exc:
                try:
                    print("[PLAYLIST-V11] extent sync failed:", exc)
                except Exception:
                    pass

        def queue_sync(self, delay=0.0) -> None:
            try:
                Clock.schedule_once(
                    lambda dt, owner=self: sync_extent(owner, dt),
                    max(0.0, float(delay)),
                )
            except Exception:
                pass

        def bind_extent(self) -> None:
            try:
                listing = self.ids.get("playlist_list")
                viewport = self.ids.get("playlist_scroll")
                if listing is None or viewport is None:
                    return
                if bool(getattr(listing, "_pymusic_extent_v11_bound", False)):
                    queue_sync(self, 0)
                    return

                def changed(*_args):
                    queue_sync(self, 0)

                # children changes on every chunked add_widget; minimum_height
                # catches Kivy's deferred GridLayout pass; viewport height catches
                # expand/collapse and orientation changes.
                listing.bind(children=changed)
                try:
                    listing.bind(minimum_height=changed)
                except Exception:
                    pass
                viewport.bind(height=changed)
                listing._pymusic_extent_v11_bound = True
                queue_sync(self, 0)
                print("[PLAYLIST-V11] exact row extent binding installed")
            except Exception as exc:
                try:
                    print("[PLAYLIST-V11] extent binding failed:", exc)
                except Exception:
                    pass

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume
        old_render = player_cls._render_playlist_ui

        def init_v11(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt, owner=self: bind_extent(owner), delay)

        def pre_enter_v11(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18):
                Clock.schedule_once(lambda _dt, owner=self: bind_extent(owner), delay)
            return result

        def resume_v11(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(lambda _dt, owner=self: bind_extent(owner), delay)
            return result

        def render_v11(self, *args, **kwargs):
            result = old_render(self, *args, **kwargs)
            bind_extent(self)
            # The v9 renderer adds 8 rows per chunk at 10 ms intervals. These
            # delayed reconciliations are defensive; the children binding above
            # normally updates the extent immediately for every chunk.
            for delay in (0.0, 0.03, 0.08, 0.16, 0.35):
                queue_sync(self, delay)
            return result

        player_cls.__init__ = init_v11
        player_cls.on_pre_enter = pre_enter_v11
        player_cls.handle_app_resume = resume_v11
        player_cls._render_playlist_ui = render_v11
        player_cls._pymusic_playlist_extent_v11 = True

        _PATCHED = True
        print("[HOTFIX] playlist exact extent v11 enabled")
        return True


_patch_playlist_extent()
