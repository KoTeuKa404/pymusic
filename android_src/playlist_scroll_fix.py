"""Single-scroll playlist/title hotfix v8.

The player already has an outer ``player_details_scroll``.  A second vertical
ScrollView around the playlist caused Android gestures to fight each other:
touches inside the playlist disabled the outer scroller and made both the queue
and the rest of the player feel stuck.  V8 keeps the playlist viewport as a
non-scrolling container whose height follows its rendered rows, so the whole
player uses exactly one vertical gesture surface.
"""
from __future__ import annotations

import sys
import threading

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
        if getattr(player_cls, "_pymusic_playlist_scroll_v8", False):
            _PATCHED = True
            return True

        Clock, dp = module.Clock, module.dp

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

                if not getattr(view, "_pymusic_title_v8", False):
                    label.bind(text=queue, texture_size=queue)
                    view.bind(width=queue)
                    view._pymusic_title_v8 = True
                queue()
            except Exception as exc:
                print("[TITLE] bind failed:", exc)

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

                content_h = max(0.0, float(getattr(listing, "minimum_height", 0) or 0))
                try:
                    listing.size_hint_y = None
                    listing.height = content_h
                except Exception:
                    pass

                # Critical v8 rule: this viewport never scrolls.  It simply
                # expands to its rows and lets player_details_scroll own every
                # vertical swipe, including swipes that begin on a track row.
                try:
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = False
                    viewport.bar_width = 0
                    viewport.always_overscroll = False
                    viewport.scroll_y = 1.0
                    viewport.disabled = not expanded
                except Exception:
                    pass

                viewport.height = content_h if expanded else 0
                viewport.opacity = 1 if expanded else 0

                request_layout(listing)
                request_layout(viewport)
                request_layout(getattr(viewport, "parent", None))
                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    request_layout(outer.children[0] if outer.children else None)
                    request_layout(outer)
                configure_outer(self)
            except Exception as exc:
                print("[PLAYLIST] v8 geometry failed:", exc)

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
                start, end = getattr(self, "_hotfix_playlist_window", (0, 0))
                total = len(self.playlist.tracks) if visible else 0
                if total > 28 and end > start:
                    title = f"{title} · {start + 1}–{end} / {total}"
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
                print("[PLAYLIST] v8 header failed:", exc)

        def render(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None
            bind_title(self)
            configure_outer(self)

            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            sig = signature(self)
            current = int(getattr(self.playlist, "index", 0) or 0)
            start0, end0 = getattr(self, "_hotfix_playlist_window", (0, 0))

            if (
                not force
                and sig == getattr(self, "_hotfix_playlist_sig", None)
                and listing.children
                and start0 <= current < end0
            ):
                update_header(self)
                return None

            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0)) + 1
            generation = self._playlist_render_gen
            listing.clear_widgets()

            if not tracks:
                self._hotfix_playlist_window = (0, 0)
                self._hotfix_playlist_sig = sig
                update_header(self)
                return None

            # Rendering hundreds of Kivy rows is expensive.  Small/medium
            # queues are shown completely; large queues keep a useful window
            # around the current track while still using the single page scroll.
            window_size = 28
            if len(tracks) <= window_size:
                start, end = 0, len(tracks)
            else:
                start = max(0, current - 9)
                end = min(len(tracks), start + window_size)
                start = max(0, end - window_size)

            self._hotfix_playlist_window = (start, end)
            self._hotfix_playlist_sig = sig
            update_header(self)
            if bool(getattr(self, "_playlist_collapsed", False)):
                return None

            indices = list(range(start, end))

            def add_chunk(offset):
                if generation != int(getattr(self, "_playlist_render_gen", -1)):
                    return
                stop_at = min(len(indices), offset + 4)
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
                    for delay in (0.0, 0.03, 0.12):
                        Clock.schedule_once(
                            lambda _dt: playlist_geometry(self, True), delay
                        )

            Clock.schedule_once(lambda _dt: add_chunk(0), 0)
            return None

        def toggle(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            if not self._playlist_collapsed:
                listing = self.ids.get("playlist_list")
                if listing is not None and not listing.children and self.playlist.tracks:
                    render(self, force=True)
                    return
            update_header(self)

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def init_v8(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: bind_title(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)

        def pre_enter_v8(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)
            return result

        def resume_v8(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(lambda _dt: configure_outer(self), delay)
                Clock.schedule_once(lambda _dt: playlist_geometry(self), delay)
            return result

        player_cls.__init__ = init_v8
        player_cls.on_pre_enter = pre_enter_v8
        player_cls.handle_app_resume = resume_v8
        player_cls.toggle_playlist_collapsed = toggle
        player_cls._render_playlist_ui = render
        player_cls._pymusic_playlist_scroll_v8 = True
        _PATCHED = True
        print("[HOTFIX] single-scroll playlist v8 enabled")
        return True


_patch_playlist_scroll()
