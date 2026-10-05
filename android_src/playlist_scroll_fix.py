"""Deterministic single-scroll playlist for Android.

The player page already owns one vertical ScrollView: ``player_details_scroll``.
Keep playlist rows directly inside that page instead of leaving a second
MDScrollView in the live touch tree.  Render the complete queue synchronously so
startup callbacks cannot cancel a pending chunk and leave only a few rows.
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
                do_layout = getattr(widget, "do_layout", None)
                if callable(do_layout):
                    do_layout()
            except Exception:
                pass
            try:
                widget.canvas.ask_update()
            except Exception:
                pass

        def padding_y(widget) -> float:
            try:
                value = getattr(widget, "padding", 0) or 0
                if isinstance(value, (int, float)):
                    return float(value) * 2.0
                values = list(value)
                if len(values) >= 4:
                    return float(values[1] or 0) + float(values[3] or 0)
                if len(values) == 2:
                    return float(values[1] or 0) * 2.0
                if len(values) == 1:
                    return float(values[0] or 0) * 2.0
            except Exception:
                pass
            return 0.0

        def spacing_y(widget) -> float:
            try:
                value = getattr(widget, "spacing", 0) or 0
                if isinstance(value, (list, tuple)):
                    return float(value[-1] or 0) if value else 0.0
                return float(value)
            except Exception:
                return 0.0

        def exact_list_height(listing) -> float:
            children = list(getattr(listing, "children", []) or [])
            total = padding_y(listing)
            for child in children:
                try:
                    total += max(0.0, float(getattr(child, "height", 0) or 0))
                except Exception:
                    pass
            if len(children) > 1:
                total += spacing_y(listing) * float(len(children) - 1)
            try:
                total = max(total, float(getattr(listing, "minimum_height", 0) or 0))
            except Exception:
                pass
            return max(0.0, total)

        def mount_playlist_direct(self) -> bool:
            """Remove playlist_scroll from the live touch hierarchy once."""
            try:
                viewport = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                if listing is None:
                    return False
                if bool(getattr(listing, "_pymusic_direct_v8", False)):
                    return True
                if viewport is None:
                    return False

                parent = getattr(viewport, "parent", None)
                if parent is None:
                    # It may already have been detached by a previous call.
                    if getattr(listing, "parent", None) is not None:
                        listing._pymusic_direct_v8 = True
                        return True
                    return False

                try:
                    insertion_index = list(parent.children).index(viewport)
                except Exception:
                    insertion_index = 0

                if getattr(listing, "parent", None) is viewport:
                    viewport.remove_widget(listing)
                elif getattr(listing, "parent", None) is not None:
                    try:
                        listing.parent.remove_widget(listing)
                    except Exception:
                        pass

                parent.remove_widget(viewport)
                parent.add_widget(listing, index=insertion_index)

                # Keep the old ids entry only for compatibility with legacy code;
                # the object is detached and can no longer receive a gesture.
                try:
                    viewport.size_hint_y = None
                    viewport.height = 0
                    viewport.opacity = 0
                    viewport.disabled = True
                    viewport.do_scroll_x = False
                    viewport.do_scroll_y = False
                    viewport.bar_width = 0
                except Exception:
                    pass

                listing.size_hint_y = None
                listing._pymusic_direct_v8 = True
                print("[PLAYLIST-V8] nested viewport removed from touch tree")
                return True
            except Exception as exc:
                print("[PLAYLIST-V8] direct mount failed:", exc)
                return False

        def bind_outer_diagnostics(self):
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None or bool(getattr(outer, "_pymusic_diag_v8", False)):
                    return

                def on_start(widget, *_args):
                    try:
                        print(
                            "[PLAYLIST-V8] scroll start "
                            f"y={float(widget.scroll_y):.4f}"
                        )
                    except Exception:
                        pass

                def on_stop(widget, *_args):
                    try:
                        content = widget.children[0] if widget.children else None
                        content_h = float(getattr(content, "height", 0) or 0) if content else 0.0
                        print(
                            "[PLAYLIST-V8] scroll stop "
                            f"y={float(widget.scroll_y):.4f} "
                            f"range={max(0.0, content_h - float(widget.height or 0)):.1f}"
                        )
                    except Exception:
                        pass

                outer.bind(on_scroll_start=on_start, on_scroll_stop=on_stop)
                outer._pymusic_diag_v8 = True
            except Exception as exc:
                print("[PLAYLIST-V8] diagnostic bind failed:", exc)

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
                    # Android rows are ButtonBehavior widgets. Give the parent
                    # enough time and a small enough distance to claim a drag.
                    outer.scroll_timeout = 180
                    outer.scroll_distance = dp(6)
                except Exception:
                    pass
                bind_outer_diagnostics(self)
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

        def playlist_geometry(self, expanded=None, log=False):
            try:
                mount_playlist_direct(self)
                listing = self.ids.get("playlist_list")
                outer = self.ids.get("player_details_scroll")
                if listing is None or outer is None:
                    return

                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                if expanded is None:
                    expanded = bool(visible and not collapsed)

                content_h = exact_list_height(listing) if expanded else 0.0
                listing.size_hint_y = None
                listing.height = content_h
                listing.opacity = 1 if expanded else 0
                listing.disabled = not expanded

                configure_outer(self)
                request_layout(listing)
                request_layout(getattr(listing, "parent", None))
                page = outer.children[0] if outer.children else None
                request_layout(page)
                request_layout(outer)

                if log:
                    page_h = float(getattr(page, "height", 0) or 0) if page is not None else 0.0
                    print(
                        "[PLAYLIST-V8] geometry "
                        f"rows={len(getattr(listing, 'children', []) or [])}/"
                        f"{len(self.playlist.tracks if self.playlist else [])} "
                        f"list_h={float(listing.height or 0):.1f} "
                        f"page_h={page_h:.1f} viewport_h={float(outer.height or 0):.1f} "
                        f"range={max(0.0, page_h - float(outer.height or 0)):.1f}"
                    )
            except Exception as exc:
                print("[PLAYLIST-V8] geometry failed:", exc)

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
            except Exception as exc:
                print("[PLAYLIST-V8] header failed:", exc)

        def render(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None

            mount_playlist_direct(self)
            bind_title(self)
            configure_outer(self)

            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            sig = signature(self)
            old_sig = getattr(self, "_hotfix_playlist_sig", None)
            complete = len(getattr(listing, "children", []) or []) == len(tracks)

            # Do not destroy/recreate live ButtonBehavior rows merely because
            # play_audio called render(force=True) during video startup. Rebuilding
            # the tree under an active finger is enough to cancel scrolling.
            if sig == old_sig and complete:
                update_header(self)
                playlist_geometry(self, log=bool(force))
                return None

            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0) or 0) + 1
            self._hotfix_playlist_sig = sig
            listing.clear_widgets()
            update_header(self)

            if tracks and not bool(getattr(self, "_playlist_collapsed", False)):
                # Complete queue, one pass. No generation-sensitive Clock chunks.
                for idx, item in enumerate(tracks):
                    try:
                        listing.add_widget(self._make_playlist_row(idx, item))
                    except Exception as exc:
                        print(f"[PLAYLIST-V8] row {idx} failed: {exc}")

            playlist_geometry(self, log=True)
            for delay in (0.0, 0.04, 0.12):
                Clock.schedule_once(
                    lambda _dt, owner=self, final=(delay == 0.12): playlist_geometry(
                        owner, log=final
                    ),
                    delay,
                )
            return None

        def toggle(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            if not self._playlist_collapsed:
                listing = self.ids.get("playlist_list")
                total = len(self.playlist.tracks if self.playlist else [])
                if listing is not None and len(listing.children) != total:
                    return render(self, force=True)
            update_header(self)
            playlist_geometry(self, log=True)
            return None

        old_init = player_cls.__init__
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def init_v8(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt, owner=self: mount_playlist_direct(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: configure_outer(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: bind_title(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: playlist_geometry(owner), delay)

        def pre_enter_v8(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18):
                Clock.schedule_once(lambda _dt, owner=self: mount_playlist_direct(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: configure_outer(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: playlist_geometry(owner), delay)
            return result

        def resume_v8(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(lambda _dt, owner=self: mount_playlist_direct(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: configure_outer(owner), delay)
                Clock.schedule_once(lambda _dt, owner=self: playlist_geometry(owner), delay)
            return result

        player_cls.__init__ = init_v8
        player_cls.on_pre_enter = pre_enter_v8
        player_cls.handle_app_resume = resume_v8
        player_cls.toggle_playlist_collapsed = toggle
        player_cls._render_playlist_ui = render
        player_cls._pymusic_playlist_scroll_v8 = True
        _PATCHED = True
        print("[HOTFIX] deterministic single-scroll playlist v8 enabled")
        return True


_patch_playlist_scroll()
