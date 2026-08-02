"""Smooth playlist scrolling and compact title layout for the Android player.

The playlist lives inside the main details ScrollView. Nested kinetic
ScrollViews fight over the same drag gesture on Android, so this patch turns
the inner playlist viewport into a non-scrolling content container and lets
the outer ScrollView handle the movement. Playlist rows stay clickable and
are still rendered in small chunks.

It also removes the fixed empty space below short titles: the title viewport
uses one-line height for short text and grows up to two lines only when needed.
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
        if player_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_hotfix_v4", False):
            # Apply only after sitecustomize has installed the main player fix.
            return False
        if getattr(player_cls, "_pymusic_playlist_scroll_v6", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def stop_effect(scroll) -> None:
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
            except Exception:
                pass

        def bind_compact_title(self) -> None:
            """Shrink the title area to one line unless more height is needed."""
            try:
                title_scroll = self.ids.get("title_scroll")
                title_label = self.ids.get("audio_title")
                if title_scroll is None or title_label is None:
                    return

                def update_title_height(*_args):
                    try:
                        texture_h = float(
                            (getattr(title_label, "texture_size", (0, 0)) or (0, 0))[1]
                            or 0
                        )
                        one_line = dp(26)
                        max_height = dp(52)
                        wanted = max(one_line, min(max_height, texture_h or one_line))
                        title_scroll.height = wanted
                        title_scroll.do_scroll_x = False
                        title_scroll.do_scroll_y = texture_h > wanted + dp(1)
                        try:
                            title_scroll.bar_width = 0
                        except Exception:
                            pass
                        if not title_scroll.do_scroll_y:
                            try:
                                title_scroll.scroll_y = 1
                            except Exception:
                                pass
                            stop_effect(title_scroll)
                    except Exception:
                        pass

                if not getattr(title_scroll, "_pymusic_compact_title_bound", False):
                    title_label.bind(texture_size=update_title_height)
                    title_label.bind(width=update_title_height)
                    title_scroll.bind(width=update_title_height)
                    title_scroll._pymusic_compact_title_bound = True

                update_title_height()
                Clock.schedule_once(lambda _dt: update_title_height(), 0)
                Clock.schedule_once(lambda _dt: update_title_height(), 0.08)
            except Exception as exc:
                print("[TITLE] compact height binding failed:", exc)

        def playlist_signature(self):
            try:
                return tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("video_id") or ""),
                        str(item.get("thumb") or ""),
                        str(item.get("duration") or ""),
                        str(item.get("title") or ""),
                        str(item.get("channel") or ""),
                    )
                    for item in (
                        self.playlist.tracks if self.playlist else []
                    )
                )
            except Exception:
                return None

        def sync_playlist_height(self, expanded: bool | None = None) -> None:
            """Use one ScrollView only: the outer details view owns scrolling."""
            try:
                scroll = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                if scroll is None or listing is None:
                    return

                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(
                    getattr(self, "_playlist_collapsed", False)
                )
                if expanded is None:
                    expanded = bool(visible and not collapsed)

                stop_effect(scroll)
                try:
                    scroll.disabled = False
                except Exception:
                    pass

                scroll.do_scroll_x = False
                scroll.do_scroll_y = False
                try:
                    scroll.bar_width = 0
                except Exception:
                    pass
                try:
                    scroll.scroll_y = 1
                except Exception:
                    pass

                if expanded:
                    content_height = float(
                        getattr(listing, "minimum_height", 0) or 0
                    )
                    scroll.height = max(dp(1), content_height)
                    scroll.opacity = 1
                else:
                    scroll.height = 0
                    scroll.opacity = 0
            except Exception as exc:
                print("[PLAYLIST] single-scroll geometry failed:", exc)

        def bind_playlist_height(self) -> None:
            try:
                listing = self.ids.get("playlist_list")
                scroll = self.ids.get("playlist_scroll")
                if listing is None or scroll is None:
                    return
                if getattr(listing, "_pymusic_height_bound_v6", False):
                    return

                def on_minimum_height(*_args):
                    Clock.schedule_once(
                        lambda _dt: sync_playlist_height(self), 0
                    )

                listing.bind(minimum_height=on_minimum_height)
                listing._pymusic_height_bound_v6 = True
            except Exception as exc:
                print("[PLAYLIST] height binding failed:", exc)

        def update_header(self) -> None:
            try:
                visible = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(
                    getattr(self, "_playlist_collapsed", False)
                )
                title = self.playlist.name or "Черга"
                start, end = getattr(
                    self, "_hotfix_playlist_window", (0, 0)
                )
                total = len(self.playlist.tracks) if visible else 0
                if total > 72 and end > start:
                    title = f"{title} · {start + 1}–{end} / {total}"

                self._set_collapsible_header(
                    self.ids.get("playlist_header_row"),
                    self.ids.get("playlist_header"),
                    self.ids.get("playlist_toggle_btn"),
                    visible,
                    title,
                    collapsed,
                )
                bind_playlist_height(self)
                sync_playlist_height(
                    self, bool(visible and not collapsed)
                )
            except Exception as exc:
                print("[PLAYLIST] header update failed:", exc)

        def render_playlist_single_scroll(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None

            bind_compact_title(self)
            bind_playlist_height(self)

            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            signature = playlist_signature(self)
            current_index = int(
                getattr(self.playlist, "index", 0) or 0
            )
            win_start, win_end = getattr(
                self, "_hotfix_playlist_window", (0, 0)
            )
            current_in_window = win_start <= current_index < win_end
            has_rows = bool(getattr(listing, "children", None))

            if (
                not force
                and signature == getattr(
                    self, "_hotfix_playlist_sig", None
                )
                and has_rows
                and (current_in_window or len(tracks) <= 72)
            ):
                update_header(self)
                return None

            self._playlist_render_gen = int(
                getattr(self, "_playlist_render_gen", 0)
            ) + 1
            render_gen = self._playlist_render_gen
            listing.clear_widgets()

            if not tracks:
                self._hotfix_playlist_window = (0, 0)
                self._hotfix_playlist_sig = signature
                update_header(self)
                return None

            if len(tracks) <= 72:
                start = 0
                end = len(tracks)
            else:
                start = max(0, current_index - 14)
                end = min(len(tracks), start + 54)
                start = max(0, end - 54)

            self._hotfix_playlist_window = (start, end)
            self._hotfix_playlist_sig = signature
            update_header(self)

            if bool(getattr(self, "_playlist_collapsed", False)):
                return None

            indices = list(range(start, end))
            chunk_size = 4

            def add_chunk(offset):
                if render_gen != int(
                    getattr(self, "_playlist_render_gen", -1)
                ):
                    return

                stop = min(len(indices), offset + chunk_size)
                for position in range(offset, stop):
                    actual_index = indices[position]
                    row = self._make_playlist_row(
                        actual_index, tracks[actual_index]
                    )
                    listing.add_widget(row)

                sync_playlist_height(self, True)
                if stop < len(indices):
                    Clock.schedule_once(
                        lambda _dt, next_offset=stop: add_chunk(
                            next_offset
                        ),
                        0.016,
                    )
                else:
                    Clock.schedule_once(
                        lambda _dt: sync_playlist_height(self, True),
                        0,
                    )
                    Clock.schedule_once(
                        lambda _dt: sync_playlist_height(self, True),
                        0.06,
                    )

            Clock.schedule_once(lambda _dt: add_chunk(0), 0)
            return None

        def toggle_playlist_smooth(self):
            collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            self._playlist_collapsed = collapsed

            listing = self.ids.get("playlist_list")
            if (
                not collapsed
                and listing is not None
                and not bool(getattr(listing, "children", None))
                and bool(self.playlist and self.playlist.tracks)
            ):
                # The playlist may have changed while collapsed. Render it on
                # open instead of showing an empty container.
                render_playlist_single_scroll(self, force=True)
                return

            update_header(self)
            if not collapsed:
                Clock.schedule_once(
                    lambda _dt: sync_playlist_height(self, True), 0
                )
                Clock.schedule_once(
                    lambda _dt: sync_playlist_height(self, True), 0.06
                )

        old_init = player_cls.__init__

        def init_with_smooth_layout(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            Clock.schedule_once(
                lambda _dt: bind_compact_title(self), 0
            )
            Clock.schedule_once(
                lambda _dt: bind_compact_title(self), 0.12
            )
            Clock.schedule_once(
                lambda _dt: bind_playlist_height(self), 0
            )

        player_cls.__init__ = init_with_smooth_layout
        player_cls.toggle_playlist_collapsed = toggle_playlist_smooth
        player_cls._render_playlist_ui = render_playlist_single_scroll
        player_cls._pymusic_playlist_scroll_v6 = True
        _PATCHED = True
        print("[HOTFIX] single-scroll playlist + compact title v6 enabled")
        return True


_patch_playlist_scroll()
