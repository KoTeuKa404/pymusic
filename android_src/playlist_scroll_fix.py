"""Keep the nested playlist scroll view smooth after collapse/reopen.

The player hotfix intentionally keeps playlist rows alive.  Toggling the
``disabled`` property on the ScrollView still propagates through every row and
thumbnail, though, and on Android that leaves the reopened list noticeably
janky.  This patch changes only geometry/scroll ownership and never disables
the whole widget tree.
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
        if getattr(player_cls, "_pymusic_playlist_scroll_v5", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def stop_effect(scroll) -> None:
            """Stop stale kinetic movement without rebuilding any rows."""
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

        def release_outer_scroll(self, *_args) -> None:
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    outer.do_scroll_y = True
            except Exception:
                pass

        def bind_nested_scroll_guard(self) -> None:
            """Let the inner playlist own a drag instead of fighting its parent."""
            try:
                inner = self.ids.get("playlist_scroll")
                outer = self.ids.get("player_details_scroll")
                if inner is None or outer is None:
                    return
                if getattr(inner, "_pymusic_nested_guard", False):
                    return

                def on_touch_down(widget, touch):
                    try:
                        if (
                            not bool(getattr(self, "_playlist_collapsed", False))
                            and widget.height > 0
                            and widget.collide_point(*touch.pos)
                        ):
                            outer.do_scroll_y = False
                    except Exception:
                        pass

                def on_touch_up(_widget, _touch):
                    Clock.schedule_once(
                        lambda _dt: release_outer_scroll(self), 0
                    )

                inner.bind(
                    on_touch_down=on_touch_down,
                    on_touch_up=on_touch_up,
                )
                inner._pymusic_nested_guard = True
            except Exception as exc:
                print("[PLAYLIST] nested scroll guard failed:", exc)

        def apply_geometry(self, expanded: bool) -> None:
            try:
                scroll = self.ids.get("playlist_scroll")
                if scroll is None:
                    return

                # Preserve the exact position through collapse/reopen.
                if not expanded:
                    try:
                        self._pymusic_playlist_saved_scroll_y = float(
                            scroll.scroll_y
                        )
                    except Exception:
                        self._pymusic_playlist_saved_scroll_y = 1.0

                stop_effect(scroll)

                # Do not set disabled=True.  It recursively invalidates every
                # playlist row and image and is the source of the reopen lag.
                try:
                    scroll.disabled = False
                except Exception:
                    pass
                scroll.do_scroll_x = False
                scroll.do_scroll_y = bool(expanded)
                scroll.height = dp(240) if expanded else 0
                scroll.opacity = 1 if expanded else 0

                if expanded:
                    saved = float(
                        getattr(
                            self,
                            "_pymusic_playlist_saved_scroll_y",
                            getattr(scroll, "scroll_y", 1.0),
                        )
                    )

                    def finish_reopen(_dt):
                        try:
                            scroll.disabled = False
                            scroll.do_scroll_y = True
                            scroll.scroll_y = max(0.0, min(1.0, saved))
                            stop_effect(scroll)
                            bind_nested_scroll_guard(self)
                        except Exception:
                            pass

                    # One callback applies geometry, the second runs after the
                    # MDList minimum_height/layout update on slower phones.
                    Clock.schedule_once(finish_reopen, 0)
                    Clock.schedule_once(finish_reopen, 0.05)
                else:
                    release_outer_scroll(self)
            except Exception as exc:
                print("[PLAYLIST] geometry update failed:", exc)

        def update_header(self, collapsed: bool) -> None:
            try:
                visible = bool(self.playlist and self.playlist.tracks)
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
                apply_geometry(self, bool(visible and not collapsed))
            except Exception as exc:
                print("[PLAYLIST] header update failed:", exc)

        def toggle_playlist_smooth(self):
            collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            self._playlist_collapsed = collapsed
            update_header(self, collapsed)

        player_cls.toggle_playlist_collapsed = toggle_playlist_smooth

        # The v4 renderer can still touch ``disabled`` after a track/window
        # update.  Normalize the ScrollView immediately afterwards.
        old_render_playlist = player_cls._render_playlist_ui

        def render_playlist_smooth(self, *args, **kwargs):
            result = old_render_playlist(self, *args, **kwargs)

            def normalize(_dt):
                try:
                    collapsed = bool(
                        getattr(self, "_playlist_collapsed", False)
                    )
                    visible = bool(
                        self.playlist and self.playlist.tracks
                    )
                    apply_geometry(
                        self, bool(visible and not collapsed)
                    )
                except Exception:
                    pass

            Clock.schedule_once(normalize, 0)
            return result

        player_cls._render_playlist_ui = render_playlist_smooth
        player_cls._pymusic_playlist_scroll_v5 = True
        _PATCHED = True
        print("[HOTFIX] playlist reopen scrolling v5 enabled")
        return True


_patch_playlist_scroll()
