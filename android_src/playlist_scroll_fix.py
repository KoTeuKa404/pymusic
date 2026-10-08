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
        Window = module.Window

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
                            # Only suspend the parent for the *current*
                            # gesture. The child can grab the touch, so its
                            # own on_touch_up is not guaranteed to fire.
                            inner._pymusic_active_touch_uid = touch.uid
                            outer.do_scroll_y = False
                            print(
                                "[PLAYLIST-V5] inner gesture begin "
                                f"uid={touch.uid} outer_y={outer.do_scroll_y}"
                            )
                    except Exception as exc:
                        print("[PLAYLIST-V5] gesture begin failed:", exc)

                def restore_touch(touch):
                    try:
                        active = getattr(inner, "_pymusic_active_touch_uid", None)
                        if active is None or active == touch.uid:
                            inner._pymusic_active_touch_uid = None
                            release_outer_scroll(self)
                            print(
                                "[PLAYLIST-V5] gesture released "
                                f"uid={touch.uid} outer_y={outer.do_scroll_y}"
                            )
                    except Exception as exc:
                        print("[PLAYLIST-V5] gesture release failed:", exc)

                def on_touch_up(_widget, touch):
                    restore_touch(touch)

                def window_touch_up(_window, touch):
                    # A grabbed KivyMD Button/ScrollView can bypass the
                    # child's normal touch-up dispatch. Window.on_touch_up
                    # is the lifecycle owner for the physical finger, so it
                    # must ALWAYS restore outer scrolling.
                    restore_touch(touch)
                    return False

                def window_touch_down(_window, touch):
                    # Recover from a dropped UP (screen transition, OS
                    # interruption) before the next gesture starts.
                    if getattr(inner, "_pymusic_active_touch_uid", None) is not None:
                        inner._pymusic_active_touch_uid = None
                        release_outer_scroll(self)
                        print("[PLAYLIST-V5] recovered stale outer scroll state")
                    return False

                inner.bind(on_touch_down=on_touch_down, on_touch_up=on_touch_up)
                Window.bind(
                    on_touch_up=window_touch_up,
                    on_touch_down=window_touch_down,
                )
                # Retain strong references to handlers for Kivy event bindings.
                inner._pymusic_window_touch_handlers = (
                    window_touch_up,
                    window_touch_down,
                )
                inner._pymusic_nested_guard = True
                print("[PLAYLIST-V5] global touch release guard enabled")
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
