"""Canonical player input/layout cleanup.

This module replaces the old stacked playlist-scroll/touch hotfix chain with
one deterministic owner:

* ``playlist_list`` is re-parented out of the nested ``MDScrollView`` so the
  player has one vertical scroll owner only: ``player_details_scroll``;
* playlist geometry/rendering is handled here, without v9/v10/v11/v12 timers
  fighting over ``do_scroll_y`` and touch ownership;
* native video keeps Java transport buttons and the native SeekBar, but the
  bare ``SurfaceView`` and empty full-frame overlay are not touch targets.

The old patch files remain in the repository for rollback/history, but
``recent_utils`` no longer imports them.
"""
from __future__ import annotations

import sys
import threading

_SCROLL_PATCHED = False
_NATIVE_PATCHED = False
_SCROLL_LOCK = threading.RLock()
_NATIVE_LOCK = threading.RLock()


def _spacing_y(widget) -> float:
    try:
        value = getattr(widget, "spacing", 0) or 0
        if isinstance(value, (list, tuple)):
            return float(value[-1] or 0) if value else 0.0
        return float(value)
    except Exception:
        return 0.0


def _padding_y(widget) -> float:
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


def _children_height(widget) -> float:
    try:
        children = list(getattr(widget, "children", []) or [])
    except Exception:
        children = []

    total = 0.0
    for child in children:
        try:
            total += max(0.0, float(getattr(child, "height", 0) or 0))
        except Exception:
            pass
    if len(children) > 1:
        total += _spacing_y(widget) * float(len(children) - 1)
    total += _padding_y(widget)
    try:
        total = max(total, float(getattr(widget, "minimum_height", 0) or 0))
    except Exception:
        pass
    return max(0.0, total)


def install_single_scroll_cleanup() -> bool:
    """Make the outer player ScrollView the sole playlist scroll owner."""
    global _SCROLL_PATCHED

    with _SCROLL_LOCK:
        if _SCROLL_PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        if player_cls is None:
            return False
        if not bool(getattr(player_cls, "_pymusic_hotfix_v4", False)):
            return False
        if bool(getattr(player_cls, "_pymusic_clean_single_scroll_v1", False)):
            _SCROLL_PATCHED = True
            return True

        Clock = module.Clock

        def request_layout(widget) -> None:
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

        def mount_playlist_direct(self) -> bool:
            """Remove the nested playlist ScrollView from the live touch tree."""
            try:
                viewport = self.ids.get("playlist_scroll")
                listing = self.ids.get("playlist_list")
                if viewport is None or listing is None:
                    return False

                if bool(getattr(listing, "_pymusic_direct_playlist_v1", False)):
                    return True

                parent = getattr(viewport, "parent", None)
                if parent is None:
                    # Already detached by an earlier call. The list may already
                    # be mounted directly.
                    if getattr(listing, "parent", None) is not None:
                        listing._pymusic_direct_playlist_v1 = True
                        return True
                    return False

                try:
                    index = list(parent.children).index(viewport)
                except Exception:
                    index = 0

                if getattr(listing, "parent", None) is viewport:
                    viewport.remove_widget(listing)
                parent.remove_widget(viewport)
                parent.add_widget(listing, index=index)

                # Keep the old viewport object alive in ids for compatibility
                # with old resume code, but it is no longer part of the touch
                # hierarchy and therefore cannot steal DOWN/MOVE/UP.
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
                listing._pymusic_direct_playlist_v1 = True

                if not bool(getattr(listing, "_pymusic_clean_height_bound_v1", False)):
                    def changed(*_args):
                        Clock.schedule_once(
                            lambda _dt, owner=self: sync_playlist_geometry(owner),
                            0,
                        )

                    listing.bind(children=changed)
                    try:
                        listing.bind(minimum_height=changed)
                    except Exception:
                        pass
                    listing._pymusic_clean_height_bound_v1 = True

                print("[PLAYER-INPUT] playlist_list mounted directly in outer scroll")
                return True
            except Exception as exc:
                print("[PLAYER-INPUT] direct playlist mount failed:", exc)
                return False

        def sync_playlist_geometry(self) -> None:
            try:
                mount_playlist_direct(self)
                listing = self.ids.get("playlist_list")
                outer = self.ids.get("player_details_scroll")
                if listing is None or outer is None:
                    return

                has_tracks = bool(self.playlist and self.playlist.tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                expanded = bool(has_tracks and not collapsed)

                wanted = _children_height(listing) if expanded else 0.0
                listing.size_hint_y = None
                listing.height = wanted
                listing.opacity = 1 if expanded else 0
                listing.disabled = not expanded

                outer.disabled = False
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                try:
                    outer.always_overscroll = False
                    outer.bar_width = module.dp(3)
                except Exception:
                    pass

                request_layout(listing)
                request_layout(getattr(listing, "parent", None))
                try:
                    request_layout(outer.children[0] if outer.children else None)
                except Exception:
                    pass
                request_layout(outer)
            except Exception as exc:
                print("[PLAYER-INPUT] playlist geometry failed:", exc)

        def update_playlist_header(self) -> None:
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
                print("[PLAYER-INPUT] playlist header failed:", exc)

        def signature(self):
            try:
                return tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("video_id") or ""),
                        str(item.get("title") or ""),
                        str(item.get("channel") or ""),
                        str(item.get("duration") or ""),
                        str(item.get("thumb") or ""),
                    )
                    for item in (self.playlist.tracks if self.playlist else [])
                )
            except Exception:
                return None

        def render_playlist_clean(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None

            mount_playlist_direct(self)
            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            sig = signature(self)
            previous_sig = getattr(self, "_pymusic_clean_playlist_sig_v1", None)

            if (
                not force
                and sig == previous_sig
                and len(getattr(listing, "children", []) or []) == len(tracks)
            ):
                update_playlist_header(self)
                sync_playlist_geometry(self)
                return None

            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0) or 0) + 1
            generation = self._playlist_render_gen
            self._pymusic_clean_playlist_sig_v1 = sig
            listing.clear_widgets()
            update_playlist_header(self)
            sync_playlist_geometry(self)

            if not tracks or bool(getattr(self, "_playlist_collapsed", False)):
                return None

            # A genuinely new queue starts at the top. Track changes within the
            # same queue do not reset the user's outer scroll position.
            if sig != previous_sig:
                try:
                    self.ids.player_details_scroll.scroll_y = 1.0
                except Exception:
                    pass

            indices = list(range(len(tracks)))
            chunk_size = 8

            def add_chunk(offset: int):
                if generation != int(getattr(self, "_playlist_render_gen", -1)):
                    return
                stop = min(len(indices), offset + chunk_size)
                for pos in range(offset, stop):
                    idx = indices[pos]
                    try:
                        listing.add_widget(self._make_playlist_row(idx, tracks[idx]))
                    except Exception as exc:
                        print("[PLAYER-INPUT] playlist row failed:", exc)
                sync_playlist_geometry(self)
                if stop < len(indices):
                    Clock.schedule_once(
                        lambda _dt, next_offset=stop: add_chunk(next_offset),
                        0.01,
                    )
                else:
                    for delay in (0.0, 0.04, 0.12):
                        Clock.schedule_once(
                            lambda _dt, owner=self: sync_playlist_geometry(owner),
                            delay,
                        )

            Clock.schedule_once(lambda _dt: add_chunk(0), 0)
            return None

        def toggle_playlist_clean(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            listing = self.ids.get("playlist_list")
            if (
                not self._playlist_collapsed
                and listing is not None
                and len(getattr(listing, "children", []) or [])
                != len(self.playlist.tracks if self.playlist else [])
            ):
                return render_playlist_clean(self, force=True)
            update_playlist_header(self)
            sync_playlist_geometry(self)
            return None

        old_on_kv_post = player_cls.on_kv_post
        old_pre_enter = player_cls.on_pre_enter

        def on_kv_post_clean(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            for delay in (0.0, 0.05, 0.16):
                Clock.schedule_once(
                    lambda _dt, owner=self: (
                        mount_playlist_direct(owner),
                        sync_playlist_geometry(owner),
                    ),
                    delay,
                )
            return result

        def pre_enter_clean(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.05, 0.16):
                Clock.schedule_once(
                    lambda _dt, owner=self: (
                        mount_playlist_direct(owner),
                        sync_playlist_geometry(owner),
                    ),
                    delay,
                )
            return result

        player_cls.on_kv_post = on_kv_post_clean
        player_cls.on_pre_enter = pre_enter_clean
        player_cls._render_playlist_ui = render_playlist_clean
        player_cls.toggle_playlist_collapsed = toggle_playlist_clean

        # Compatibility only: resume_ui_fix historically waits for this marker.
        # No old v7/v9 nested-scroll code is installed anymore.
        player_cls._pymusic_playlist_scroll_v7 = True
        player_cls._pymusic_clean_single_scroll_v1 = True
        _SCROLL_PATCHED = True
        print("[PLAYER-INPUT] clean single-scroll playlist enabled")
        return True


def install_native_touch_cleanup() -> bool:
    """Keep only real native controls touchable; let empty video pixels fall through."""
    global _NATIVE_PATCHED

    with _NATIVE_LOCK:
        if _NATIVE_PATCHED:
            return True

        try:
            import audio_screen
            import video_player
            from jnius import autoclass
        except Exception:
            return False

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        if not bool(getattr(screen_cls, "_pymusic_clean_single_scroll_v1", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_final_player_v2", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_youtube_timeline_v3", False)):
            return False
        if bool(getattr(video_cls, "_pymusic_clean_native_touch_v1", False)):
            _NATIVE_PATCHED = True
            return True

        Bridge = autoclass("org.koteuka404.pymusic.NativeTransportBridge")

        @video_player.run_on_ui_thread
        def apply_native_policy(owner) -> None:
            try:
                controls = list(getattr(owner, "_controls_touch_views", []) or [])

                # Only the three actual transport ImageButtons own native touch.
                actions = (-1, 0, 1)
                for index, button in enumerate(controls[:3]):
                    if button is None:
                        continue
                    try:
                        Bridge.bindTransportButton(button, actions[index])
                        button.setClickable(True)
                        button.setFocusable(False)
                    except Exception as exc:
                        print(f"[PLAYER-INPUT] Java button {index} bind failed: {exc}")

                # The bare video surface is visual only. A tap/swipe on it falls
                # through to Kivy's VideoTapArea/player UI.
                surface = getattr(owner, "surface_view", None)
                if surface is not None:
                    try:
                        surface.setOnTouchListener(None)
                    except Exception:
                        pass
                    try:
                        surface.setClickable(False)
                        surface.setLongClickable(False)
                        surface.setFocusable(False)
                        surface.setFocusableInTouchMode(False)
                    except Exception:
                        pass

                # Full-frame transparent FrameLayout must not be a touch sink.
                overlay = getattr(owner, "controls_overlay", None)
                if overlay is not None:
                    try:
                        overlay.setOnTouchListener(None)
                    except Exception:
                        pass
                    try:
                        overlay.setClickable(False)
                        overlay.setLongClickable(False)
                        overlay.setFocusable(False)
                        overlay.setFocusableInTouchMode(False)
                    except Exception:
                        pass

                # controls[3:] contains the surrounding transport bar. Its child
                # buttons remain clickable; the container itself must be passive.
                for view in controls[3:]:
                    if view is None:
                        continue
                    try:
                        view.setOnTouchListener(None)
                    except Exception:
                        pass
                    try:
                        view.setClickable(False)
                        view.setLongClickable(False)
                        view.setFocusable(False)
                        view.setFocusableInTouchMode(False)
                    except Exception:
                        pass

                seek = getattr(owner, "_native_seek_bar", None)
                if seek is not None:
                    try:
                        seek.setEnabled(True)
                        seek.setClickable(True)
                        seek.setFocusable(False)
                    except Exception:
                        pass
            except Exception as exc:
                print("[PLAYER-INPUT] native touch policy failed:", exc)

        # Capture the complete existing overlay/timeline stack and become the
        # single final policy owner after it. This specifically neutralizes the
        # old Java wrapper that made the full transparent overlay clickable.
        old_ensure_overlay = video_cls._ensure_controls_overlay
        old_visible = video_cls.set_native_controls_visible
        old_apply_bounds = video_cls._apply_surface_bounds
        old_ensure_player = screen_cls._ensure_video_player

        def ensure_overlay_clean(self, *args, **kwargs):
            result = old_ensure_overlay(self, *args, **kwargs)
            apply_native_policy(self)
            return result

        def visible_clean(self, visible, *args, **kwargs):
            result = old_visible(self, visible, *args, **kwargs)
            apply_native_policy(self)
            return result

        def apply_bounds_clean(self, *args, **kwargs):
            result = old_apply_bounds(self, *args, **kwargs)
            apply_native_policy(self)
            return result

        def bind_surface_clean(self):
            apply_native_policy(self)

        def bind_controls_clean(self):
            apply_native_policy(self)

        def ensure_player_clean(self, *args, **kwargs):
            result = old_ensure_player(self, *args, **kwargs)
            try:
                vp = getattr(self, "_video_player", None)
                if vp is not None:
                    apply_native_policy(vp)
            except Exception:
                pass
            return result

        video_cls._ensure_controls_overlay = ensure_overlay_clean
        video_cls.set_native_controls_visible = visible_clean
        video_cls._apply_surface_bounds = apply_bounds_clean
        video_cls._bind_surface_tap = bind_surface_clean
        video_cls._bind_controls_tap = bind_controls_clean
        screen_cls._ensure_video_player = ensure_player_clean

        video_cls._pymusic_clean_native_touch_v1 = True
        screen_cls._pymusic_clean_native_touch_v1 = True
        _NATIVE_PATCHED = True
        print(
            "[PLAYER-INPUT] native touch cleanup enabled: "
            "SurfaceView/overlay passive, buttons+seekbar active"
        )
        return True
