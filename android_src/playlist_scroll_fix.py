"""Nested playlist scrolling hotfix v9.

The previous single-scroll v8 deliberately disabled ``playlist_scroll`` and
expanded the queue to the full row height.  That avoided two ScrollViews
fighting over one gesture, but it also meant the playlist itself could no longer
be swiped and large queues were truncated to a 28-row window.

V9 gives the playlist a bounded viewport again and lets it own vertical gestures
that start inside that viewport.  The outer player ScrollView keeps working
normally everywhere else.  All playlist tracks are rendered incrementally so
users can actually reach the full queue.
"""
from __future__ import annotations

import sys
import threading
from types import MethodType

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_playlist_scroll() -> bool:
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        playlist_cls = getattr(module, "Playlist", None)
        if player_cls is None or playlist_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_hotfix_v4", False):
            return False
        if getattr(player_cls, "_pymusic_playlist_scroll_v9", False):
            _PATCHED = True
            return True

        Clock, dp = module.Clock, module.dp
        Window = getattr(module, "Window", None)

        try:
            from kivy.uix.widget import Widget
        except Exception:
            Widget = None

        def request_layout(widget):
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

        def configure_outer(self):
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None:
                    return
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                outer.disabled = False
                try:
                    outer.always_overscroll = False
                    outer.bar_width = dp(3)
                except Exception:
                    pass
            except Exception:
                pass

        def bind_nested_touch(self):
            """Do not let the parent ScrollView steal a playlist swipe.

            Kivy nested ScrollViews both try to grab the same touch.  When the
            playlist is actually scrollable, bypass the parent's ScrollView
            handler for touches that start over the playlist and dispatch them
            straight through the normal Widget child chain.  The inner
            playlist_scroll then becomes the only ScrollView that grabs it.
            """
            if Widget is None:
                return
            try:
                outer = self.ids.get("player_details_scroll")
                inner = self.ids.get("playlist_scroll")
                if outer is None or inner is None:
                    return
                if getattr(outer, "_pymusic_playlist_nested_v9", False):
                    return

                original_down = outer.on_touch_down
                outer._pymusic_outer_touch_down_v9 = original_down

                def outer_touch_down(widget, touch):
                    try:
                        playlist = self.ids.get("playlist_scroll")
                        if (
                            playlist is not None
                            and not bool(getattr(playlist, "disabled", False))
                            and bool(getattr(playlist, "do_scroll_y", False))
                            and float(getattr(playlist, "height", 0) or 0) > dp(1)
                            and playlist.collide_point(*touch.pos)
                        ):
                            # Skip ScrollView.on_touch_down on the parent.  The
                            # regular Widget dispatcher walks into its content,
                            # where playlist_scroll receives and grabs the touch.
                            return Widget.on_touch_down(widget, touch)
                    except Exception:
                        pass
                    return original_down(touch)

                outer.on_touch_down = MethodType(outer_touch_down, outer)
                outer._pymusic_playlist_nested_v9 = True
                print("[PLAYLIST] nested touch owner v9 installed")
            except Exception as exc:
                print("[PLAYLIST] nested touch install failed:", exc)

        def bind_title(self):
            try:
                view = self.ids.get("title_scroll")
                label = self.ids.get("audio_title")
                if view is None or label is None:
                    return

                def apply(_dt=0):
                    try:
                        width = max(dp(1), float(view.width or 0))
                        label.text_size = (width, None)
                        try:
                            label.texture_update()
                        except Exception:
                            pass
                        text = str(label.text or "").strip()
                        texture_h = float((label.texture_size or (0, 0))[1] or 0)
                        content_h = max(dp(27), texture_h) if text else 0
                        view_h = min(dp(54), content_h) if content_h else 0
                        view.height = view_h
                        label.height = max(content_h, view_h)
                        view.do_scroll_x = False
                        view.do_scroll_y = content_h > view_h + dp(1)
                        view.scroll_y = 1.0
                        try:
                            view.always_overscroll = False
                            view.bar_width = 0
                        except Exception:
                            pass
                    except Exception as exc:
                        print("[TITLE] layout failed:", exc)

                def queue(*_args):
                    try:
                        ev = getattr(self, "_pymusic_title_ev", None)
                        if ev is not None:
                            ev.cancel()
                    except Exception:
                        pass
                    self._pymusic_title_ev = Clock.schedule_once(apply, 0)

                if not getattr(view, "_pymusic_title_v9", False):
                    label.bind(text=queue, texture_size=queue)
                    view.bind(width=queue)
                    view._pymusic_title_v9 = True
                queue()
            except Exception as exc:
                print("[TITLE] bind failed:", exc)

        def playlist_view_cap():
            try:
                win_h = float(getattr(Window, "height", 0) or 0)
            except Exception:
                win_h = 0
            if win_h > 0:
                return max(dp(220), min(dp(360), win_h * 0.42))
            return dp(320)

        def playlist_geometry(self, expanded=None):
            try:
                viewport = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                if viewport is None or listing is None:
                    return

                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                if expanded is None:
                    expanded = bool(visible and not collapsed)

                content_h = max(
                    0.0,
                    float(getattr(listing, "minimum_height", 0) or 0),
                    float(getattr(listing, "height", 0) or 0),
                )
                try:
                    listing.size_hint_y = None
                    listing.height = max(
                        content_h,
                        float(getattr(listing, "minimum_height", 0) or 0),
                    )
                except Exception:
                    pass

                if not expanded:
                    viewport.height = 0
                    viewport.opacity = 0
                    viewport.disabled = True
                    viewport.do_scroll_y = False
                else:
                    cap = playlist_view_cap()
                    view_h = min(content_h, cap) if content_h > 0 else 0
                    viewport.height = view_h
                    viewport.opacity = 1
                    viewport.disabled = False
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = bool(content_h > view_h + dp(2))
                    try:
                        viewport.always_overscroll = False
                        viewport.bar_width = dp(3) if viewport.do_scroll_y else 0
                        viewport.scroll_type = ["bars", "content"]
                        # Shorter timeout/distance makes Android finger scrolling
                        # feel immediate without breaking row taps.
                        viewport.scroll_timeout = 140
                        viewport.scroll_distance = dp(8)
                    except Exception:
                        pass

                request_layout(listing)
                request_layout(viewport)
                request_layout(getattr(viewport, "parent", None))
                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    request_layout(outer.children[0] if outer.children else None)
                    request_layout(outer)
                configure_outer(self)
                bind_nested_touch(self)
            except Exception as exc:
                print("[PLAYLIST] v9 geometry failed:", exc)

        def signature(self):
            try:
                return tuple(
                    (
                        str(x.get("url") or ""),
                        str(x.get("video_id") or ""),
                        str(x.get("thumb") or ""),
                        str(x.get("duration") or ""),
                        str(x.get("title") or ""),
                        str(x.get("channel") or ""),
                    )
                    for x in (self.playlist.tracks if self.playlist else [])
                )
            except Exception:
                return None

        def update_header(self):
            try:
                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                title = self.playlist.name or "Черга"
                total = len(self.playlist.tracks) if visible else 0
                if total:
                    title = f"{title} · {total}"
                self._set_collapsible_header(
                    self.ids.get("playlist_header_row"),
                    self.ids.get("playlist_header"),
                    self.ids.get("playlist_toggle_btn"),
                    visible,
                    title,
                    collapsed,
                )
                playlist_geometry(self, visible and not collapsed)
            except Exception as exc:
                print("[PLAYLIST] v9 header failed:", exc)

        def position_on_current(self):
            try:
                viewport = self.ids.get("playlist_scroll")
                total = len(self.playlist.tracks) if self.playlist else 0
                current = int(getattr(self.playlist, "index", 0) or 0)
                if viewport is None or total <= 1 or not viewport.do_scroll_y:
                    return
                # scroll_y: 1 = top, 0 = bottom.
                viewport.scroll_y = max(0.0, min(1.0, 1.0 - (current / float(total - 1))))
            except Exception:
                pass

        def render(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None
            bind_title(self)
            configure_outer(self)
            bind_nested_touch(self)

            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            sig = signature(self)

            if (
                not force
                and sig == getattr(self, "_hotfix_playlist_sig_v9", None)
                and len(listing.children) == len(tracks)
            ):
                update_header(self)
                return None

            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0)) + 1
            generation = self._playlist_render_gen
            listing.clear_widgets()
            self._hotfix_playlist_sig_v9 = sig

            if not tracks:
                update_header(self)
                return None

            update_header(self)
            if bool(getattr(self, "_playlist_collapsed", False)):
                return None

            # Render the complete queue, but in small chunks so opening a large
            # YouTube playlist does not freeze the Kivy UI for one long frame.
            indices = list(range(len(tracks)))
            chunk_size = 8

            def add_chunk(offset):
                if generation != int(getattr(self, "_playlist_render_gen", -1)):
                    return
                stop_at = min(len(indices), offset + chunk_size)
                for pos in range(offset, stop_at):
                    idx = indices[pos]
                    try:
                        row = self._make_playlist_row(idx, tracks[idx])
                        listing.add_widget(row)
                    except Exception as exc:
                        print("[PLAYLIST] row failed:", exc)
                playlist_geometry(self, True)
                if stop_at < len(indices):
                    Clock.schedule_once(lambda _dt: add_chunk(stop_at), 0.01)
                else:
                    for delay in (0.0, 0.04, 0.14):
                        Clock.schedule_once(lambda _dt: playlist_geometry(self, True), delay)
                    Clock.schedule_once(lambda _dt: position_on_current(self), 0.16)

            Clock.schedule_once(lambda _dt: add_chunk(0), 0)
            return None

        def toggle(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            if not self._playlist_collapsed:
                listing = self.ids.get("playlist_list")
                if listing is not None and (
                    not listing.children
                    or len(listing.children) != len(self.playlist.tracks)
                ):
                    render(self, force=True)
                    return
            update_header(self)

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def init_v9(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: bind_title(self), delay)
                Clock.schedule_once(lambda _dt: bind_nested_touch(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)

        def pre_enter_v9(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: bind_nested_touch(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)
            return result

        def resume_v9(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: bind_nested_touch(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)
            return result

        player_cls.__init__ = init_v9
        player_cls.on_pre_enter = pre_enter_v9
        player_cls.handle_app_resume = resume_v9
        player_cls.toggle_playlist_collapsed = toggle
        player_cls._render_playlist_ui = render

        # Keep old markers for downstream patches that only check readiness.
        player_cls._pymusic_playlist_scroll_v7 = True
        player_cls._pymusic_playlist_scroll_v8 = True
        player_cls._pymusic_playlist_scroll_v9 = True
        _PATCHED = True
        print("[HOTFIX] nested playlist scroll v9 enabled")
        return True


_patch_playlist_scroll()
