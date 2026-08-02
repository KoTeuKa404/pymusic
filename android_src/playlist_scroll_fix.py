"""Android playlist/title hotfix v7."""
from __future__ import annotations

import sys
import threading
import types

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
        if getattr(player_cls, "_pymusic_playlist_scroll_v7", False):
            _PATCHED = True
            return True

        Clock, dp = module.Clock, module.dp

        def stop(scroll):
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
                scroll.scroll_y = max(0.0, min(1.0, float(scroll.scroll_y)))
            except Exception:
                pass

        def outer_ready(self, top=False):
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None:
                    return
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass
                if top:
                    outer.scroll_y = 1.0
                stop(outer)
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
                        target_size = (width, None)
                        if tuple(label.text_size) != target_size:
                            label.text_size = target_size
                        text = str(label.text or "").strip()
                        texture_h = float((label.texture_size or (0, 0))[1] or 0)
                        if text:
                            content_h = max(dp(27), texture_h)
                            view_h = min(dp(54), content_h)
                        else:
                            content_h = 0
                            view_h = 0
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
                        stop(view)
                        Clock.schedule_once(lambda _x: outer_ready(self), 0)
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

                if not getattr(view, "_pymusic_title_v7", False):
                    label.bind(text=queue, texture_size=queue)
                    view.bind(width=queue)
                    view._pymusic_title_v7 = True
                queue()
                Clock.schedule_once(apply, 0.06)
            except Exception as exc:
                print("[TITLE] bind failed:", exc)

        def release_outer(self, *_args):
            Clock.schedule_once(lambda _dt: outer_ready(self), 0.01)

        def bind_guard(self):
            try:
                inner = self.ids.get("playlist_scroll")
                outer = self.ids.get("player_details_scroll")
                if inner is None or outer is None:
                    return
                if getattr(inner, "_pymusic_guard_v7", False):
                    return
                old_down, old_up = outer.on_touch_down, outer.on_touch_up

                def on_down(widget, touch):
                    try:
                        expanded = (
                            not bool(getattr(self, "_playlist_collapsed", False))
                            and inner.height > 0 and inner.opacity > 0
                        )
                        if expanded and inner.collide_point(*touch.pos):
                            widget.do_scroll_y = False
                            stop(widget)
                    except Exception:
                        pass
                    return old_down(touch)

                def on_up(widget, touch):
                    try:
                        return old_up(touch)
                    finally:
                        release_outer(self)

                outer.on_touch_down = types.MethodType(on_down, outer)
                outer.on_touch_up = types.MethodType(on_up, outer)
                try:
                    inner.bind(
                        on_scroll_start=lambda *_a: setattr(outer, "do_scroll_y", False),
                        on_scroll_stop=lambda *_a: release_outer(self),
                    )
                except Exception:
                    pass
                inner._pymusic_guard_v7 = True
            except Exception as exc:
                print("[PLAYLIST] guard failed:", exc)

        def geometry(self, expanded=None):
            try:
                inner = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                if inner is None or listing is None:
                    return
                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                if expanded is None:
                    expanded = visible and not collapsed
                try:
                    inner.disabled = False
                    inner.always_overscroll = False
                except Exception:
                    pass
                inner.do_scroll_x = False
                if expanded:
                    content_h = float(getattr(listing, "minimum_height", 0) or 0)
                    view_h = min(dp(240), max(dp(82), content_h))
                    inner.height = view_h
                    inner.opacity = 1
                    inner.do_scroll_y = content_h > view_h + dp(1)
                    inner.scroll_y = max(0.0, min(1.0, float(
                        getattr(self, "_pymusic_playlist_y", inner.scroll_y)
                    )))
                else:
                    try:
                        self._pymusic_playlist_y = float(inner.scroll_y)
                    except Exception:
                        self._pymusic_playlist_y = 1.0
                    inner.height = 0
                    inner.opacity = 0
                    inner.do_scroll_y = False
                stop(inner)
                bind_guard(self)
                outer_ready(self)
            except Exception as exc:
                print("[PLAYLIST] geometry failed:", exc)

        def signature(self):
            try:
                return tuple(
                    (str(x.get("url") or ""), str(x.get("video_id") or ""),
                     str(x.get("thumb") or ""), str(x.get("duration") or ""),
                     str(x.get("title") or ""), str(x.get("channel") or ""))
                    for x in (self.playlist.tracks if self.playlist else [])
                )
            except Exception:
                return None

        def header(self):
            try:
                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                title = self.playlist.name or "Черга"
                start, end = getattr(self, "_hotfix_playlist_window", (0, 0))
                total = len(self.playlist.tracks) if visible else 0
                if total > 36 and end > start:
                    title = f"{title} · {start + 1}–{end} / {total}"
                self._set_collapsible_header(
                    self.ids.get("playlist_header_row"),
                    self.ids.get("playlist_header"),
                    self.ids.get("playlist_toggle_btn"),
                    visible, title, collapsed,
                )
                geometry(self, visible and not collapsed)
            except Exception as exc:
                print("[PLAYLIST] header failed:", exc)

        def thumb_url(item):
            thumb = str(item.get("thumb") or "")
            try:
                vid = playlist_cls._normalize_video_id(str(item.get("url") or ""))
                vid = vid or playlist_cls._normalize_video_id(str(item.get("video_id") or ""))
                vid = vid or playlist_cls._video_id_from_thumb_url(thumb)
                if vid:
                    return f"https://i.ytimg.com/vi/{vid}/default.jpg"
            except Exception:
                pass
            return thumb

        def queue_thumbs(self, delay=0.05):
            try:
                ev = getattr(self, "_pymusic_thumb_ev", None)
                if ev is not None:
                    ev.cancel()
            except Exception:
                pass

            def load(_dt):
                try:
                    inner = self.ids.get("playlist_scroll")
                    rows = getattr(self, "_pymusic_rows", {}) or {}
                    order = list(getattr(self, "_pymusic_order", []) or [])
                    loader = getattr(self, "_pymusic_thumb_loader", None)
                    if inner is None or not rows or not order or loader is None:
                        return
                    first = int(round((1.0 - max(0.0, min(1.0, float(inner.scroll_y))))
                                      * max(0, len(order) - 5)))
                    for pos in range(max(0, first - 3), min(len(order), first + 10)):
                        row = rows.get(order[pos])
                        if row is None or getattr(row, "_pymusic_thumb_started", False):
                            continue
                        url = str(getattr(row, "_pymusic_thumb_url", "") or "")
                        if url:
                            row._pymusic_thumb_started = True
                            loader(row.ids.pt_thumb, url)
                except Exception as exc:
                    print("[PLAYLIST] thumb failed:", exc)

            self._pymusic_thumb_ev = Clock.schedule_once(load, delay)

        def bind_thumb_scroll(self):
            try:
                inner = self.ids.get("playlist_scroll")
                if inner is not None and not getattr(inner, "_pymusic_thumb_v7", False):
                    inner.bind(scroll_y=lambda *_a: queue_thumbs(self, 0.08))
                    inner._pymusic_thumb_v7 = True
            except Exception:
                pass

        def render(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None
            bind_title(self)
            bind_thumb_scroll(self)
            tracks = list(self.playlist.tracks if self.playlist and self.playlist.tracks else [])
            sig = signature(self)
            current = int(getattr(self.playlist, "index", 0) or 0)
            start0, end0 = getattr(self, "_hotfix_playlist_window", (0, 0))
            if (not force and sig == getattr(self, "_hotfix_playlist_sig", None)
                    and listing.children and start0 <= current < end0):
                header(self)
                queue_thumbs(self)
                return None

            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0)) + 1
            generation = self._playlist_render_gen
            listing.clear_widgets()
            self._pymusic_rows, self._pymusic_order = {}, []
            if not tracks:
                self._hotfix_playlist_window = (0, 0)
                self._hotfix_playlist_sig = sig
                header(self)
                return None

            size = 36
            if len(tracks) <= size:
                start, end = 0, len(tracks)
            else:
                start = max(0, current - 10)
                end = min(len(tracks), start + size)
                start = max(0, end - size)
            self._hotfix_playlist_window = (start, end)
            self._hotfix_playlist_sig = sig
            header(self)
            if bool(getattr(self, "_playlist_collapsed", False)):
                return None

            indices = list(range(start, end))
            self._pymusic_order = indices
            self._pymusic_thumb_loader = getattr(self, "_set_playlist_thumb", None)

            def make_row(idx):
                previous = getattr(self, "_set_playlist_thumb", None)
                try:
                    self._set_playlist_thumb = lambda *_a, **_k: None
                    row = self._make_playlist_row(idx, tracks[idx])
                finally:
                    if previous is not None:
                        self._set_playlist_thumb = previous
                row._pymusic_thumb_url = thumb_url(tracks[idx])
                row._pymusic_thumb_started = False
                return row

            def chunk(offset):
                if generation != int(getattr(self, "_playlist_render_gen", -1)):
                    return
                stop_at = min(len(indices), offset + 2)
                for pos in range(offset, stop_at):
                    idx = indices[pos]
                    row = make_row(idx)
                    self._pymusic_rows[idx] = row
                    listing.add_widget(row)
                geometry(self, True)
                queue_thumbs(self, 0.02)
                if stop_at < len(indices):
                    Clock.schedule_once(lambda _dt: chunk(stop_at), 0.016)
                else:
                    Clock.schedule_once(lambda _dt: geometry(self, True), 0.05)

            Clock.schedule_once(lambda _dt: chunk(0), 0)
            return None

        def toggle(self):
            self._playlist_collapsed = not bool(getattr(self, "_playlist_collapsed", False))
            header(self)
            if not self._playlist_collapsed:
                listing = self.ids.get("playlist_list")
                if listing is not None and not listing.children and self.playlist.tracks:
                    render(self, force=True)
                else:
                    queue_thumbs(self)

        old_init = player_cls.__init__

        def init(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self._pymusic_rows, self._pymusic_order = {}, []
            Clock.schedule_once(lambda _dt: bind_title(self), 0)
            Clock.schedule_once(lambda _dt: bind_title(self), 0.1)
            Clock.schedule_once(lambda _dt: bind_guard(self), 0)
            Clock.schedule_once(lambda _dt: outer_ready(self, True), 0.1)

        player_cls.__init__ = init
        player_cls.toggle_playlist_collapsed = toggle
        player_cls._render_playlist_ui = render
        player_cls._pymusic_playlist_scroll_v7 = True
        _PATCHED = True
        print("[HOTFIX] stable playlist + compact title v7 enabled")
        return True


_patch_playlist_scroll()
