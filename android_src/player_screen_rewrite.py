"""Player page v2: one stable KV ScrollView and one playlist renderer.

The page is constructed in youtube_gui.kv. No widgets are reparented at runtime.
The Android SurfaceView is visual-only; real native controls remain Java-bound.
All structural heights are Kivy property bindings (minimum_height), never
calculated by delayed monkey-patch callbacks during a user's drag.
"""
from __future__ import annotations

import sys
import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window

_LOCK = threading.RLock()
_INSTALLED = False


def _playlist_signature(owner):
    try:
        return tuple(
            (str(item.get("url") or ""), str(item.get("video_id") or ""))
            for item in (owner.playlist.tracks if owner.playlist else [])
        )
    except Exception:
        return ()


def _similar_signature(owner):
    try:
        return tuple(
            (str(item.get("url") or ""), str(item.get("video_id") or ""))
            for item in (getattr(owner, "_related_items", None) or [])
        )
    except Exception:
        return ()


def _update_section(owner, kind):
    playlist = kind == "playlist"
    tracks = (
        list(owner.playlist.tracks if owner.playlist else [])
        if playlist else list(getattr(owner, "_related_items", None) or [])
    )
    collapsed = bool(getattr(owner, "_playlist_collapsed" if playlist else "_similar_collapsed", False))
    listing = owner.ids.get("playlist_list" if playlist else "similar_list")
    if listing is None:
        return

    row = owner.ids.get("playlist_header_row" if playlist else "similar_header_row")
    label = owner.ids.get("playlist_header" if playlist else "similar_header")
    toggle = owner.ids.get("playlist_toggle_btn" if playlist else "similar_toggle_btn")
    title = (owner.playlist.name or "Черга") if playlist else "Схожі відео"
    owner._set_collapsible_header(row, label, toggle, bool(tracks), title, collapsed)

    visible = bool(tracks and not collapsed)
    listing.size_hint_y = None
    # Collapsing hides only the list, not its rows. A later forced render must
    # not destroy/replace row ButtonBehavior widgets during an active swipe.
    listing.opacity = 1.0 if visible else 0.0
    listing.disabled = not visible
    listing.height = max(0.0, float(listing.minimum_height or 0)) if visible else 0.0


def _render_playlist(owner, force=False):
    listing = owner.ids.get("playlist_list")
    if listing is None:
        return
    tracks = list(owner.playlist.tracks if owner.playlist else [])
    signature = _playlist_signature(owner)
    cached = getattr(owner, "_player_core_playlist_signature", None)
    # Playback switches repeatedly call force=True. Ignore that flag for a
    # stable queue: preserving row identity preserves touch gesture ownership.
    if signature != cached or len(listing.children) != len(tracks):
        listing.clear_widgets()
        for index, item in enumerate(tracks):
            listing.add_widget(owner._make_playlist_row(index, item))
        owner._player_core_playlist_signature = signature
        print(f"[PLAYER-CORE-V2] playlist rows={len(listing.children)}/{len(tracks)}")
    _update_section(owner, "playlist")
    Clock.schedule_once(lambda dt: _report_layout(owner), 0.10)


def _render_similar(owner, force=False):
    # CURRENT-V4 owns the combined autoplay/comments/related panel. Keep its
    # renderer if present; it now uses similar_list directly without nesting.
    current_panel = getattr(owner, "_render_current_lower_v4", None)
    if callable(current_panel):
        return current_panel()
    listing = owner.ids.get("similar_list")
    if listing is None:
        return
    items = list(getattr(owner, "_related_items", None) or [])
    signature = _similar_signature(owner)
    cached = getattr(owner, "_player_core_similar_signature", None)
    if signature != cached or len(listing.children) != len(items):
        listing.clear_widgets()
        for index, item in enumerate(items):
            listing.add_widget(owner._make_similar_row(index, item))
        owner._player_core_similar_signature = signature
    _update_section(owner, "similar")


def _report_layout(owner):
    try:
        scroll = owner.ids.get("player_details_scroll")
        if scroll is None or not scroll.children:
            print("[PLAYER-CORE-V2] missing player_details_scroll")
            return
        page = scroll.children[0]
        p = owner.ids.get("playlist_list")
        s = owner.ids.get("similar_list")
        print(
            "[PLAYER-CORE-V2] geometry "
            f"page={float(page.height):.1f}/{float(page.minimum_height):.1f} "
            f"viewport={float(scroll.height):.1f} "
            f"range={max(0.0, float(page.height)-float(scroll.height)):.1f} "
            f"playlist={len(p.children) if p else 0}:{float(p.height) if p else 0:.1f} "
            f"similar={len(s.children) if s else 0}:{float(s.height) if s else 0:.1f} "
            f"y={float(scroll.scroll_y):.3f}"
        )
    except Exception as exc:
        print("[PLAYER-CORE-V2] geometry error:", exc)


def _install_now():
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        audio = sys.modules.get("audio_screen")
        video = sys.modules.get("video_player")
        if audio is None or video is None:
            return False
        screen_cls = getattr(audio, "AudioPlayerScreen", None)
        video_cls = getattr(video, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        # Only install after the actual native Java and metadata geometry
        # owners. Returning False means not ready; never report "installed"
        # merely because a background waiter was started.
        required = ("_pymusic_hotfix_v4", "_pymusic_final_player_v2",
                    "_pymusic_native_java_transport_v1")
        if not all(bool(getattr(screen_cls, marker, False)) for marker in required):
            return False
        if not getattr(video_cls, "_pymusic_native_java_transport_v1", False):
            return False
        if getattr(screen_cls, "_pymusic_player_core_rewrite_v2", False):
            _INSTALLED = True
            return True

        @video.run_on_ui_thread
        def passive_native(owner):
            try:
                surface = getattr(owner, "surface_view", None)
                if surface is not None:
                    surface.setOnTouchListener(None)
                    surface.setClickable(False)
                    surface.setLongClickable(False)
                    surface.setFocusable(False)
            except Exception as exc:
                print("[PLAYER-CORE-V2] passive surface:", exc)
            try:
                controls = list(getattr(owner, "_controls_touch_views", []) or [])
                parents = [getattr(owner, "controls_overlay", None), *controls[3:]]
                for frame in parents:
                    if frame is not None:
                        frame.setOnTouchListener(None)
                        frame.setClickable(False)
                        frame.setLongClickable(False)
                        frame.setFocusable(False)
            except Exception as exc:
                print("[PLAYER-CORE-V2] passive overlay:", exc)

        previous_controls = video_cls._bind_controls_tap
        previous_bounds = video_cls._apply_surface_bounds
        previous_visible = video_cls.set_native_controls_visible

        def bind_surface(self):
            passive_native(self)

        def bind_controls(self):
            # The Java transport binding is responsible only for child buttons.
            result = previous_controls(self)
            passive_native(self)
            return result

        def apply_bounds(self, *args, **kwargs):
            result = previous_bounds(self, *args, **kwargs)
            passive_native(self)
            return result

        def controls_visible(self, visible, *args, **kwargs):
            result = previous_visible(self, visible, *args, **kwargs)
            passive_native(self)
            return result

        video_cls._bind_surface_tap = bind_surface
        video_cls._bind_controls_tap = bind_controls
        video_cls._apply_surface_bounds = apply_bounds
        video_cls.set_native_controls_visible = controls_visible

        previous_init = screen_cls.__init__
        previous_enter = screen_cls.on_pre_enter
        previous_ensure = screen_cls._ensure_video_player

        def attach(owner):
            if getattr(owner, "_player_core_scroll_bound_v2", False):
                return
            scroll = owner.ids.get("player_details_scroll")
            if scroll is None:
                return

            # The new KV tree is the ONLY live page tree; no detach/insert or
            # replacement is allowed after this point.
            if not scroll.children or owner.ids.get("video_block") is None:
                return
            page = scroll.children[0]
            if owner.ids["video_block"].parent is not page:
                print("[PLAYER-CORE-V2] KV hierarchy mismatch, refusing rewrite")
                return

            scroll.do_scroll_x = False
            scroll.do_scroll_y = True
            scroll.disabled = False
            scroll.scroll_timeout = 180
            scroll.scroll_distance = dp(5)
            try:
                scroll.always_overscroll = False
            except Exception:
                pass

            # The Kivy layout itself owns all heights. Do not subscribe a
            # synchronous re-layout or a native SurfaceView resize to scroll_y.
            owner._player_core_scroll_bound_v2 = True
            print("[PLAYER-CORE-V2] static KV scroll tree ready")

            def align_after_stop(*_args):
                try:
                    old = getattr(owner, "_player_core_align_ev_v2", None)
                    if old is not None:
                        old.cancel()
                    owner._player_core_align_ev_v2 = Clock.schedule_once(
                        lambda _dt: owner._align_video_to_thumb(), 0.08
                    )
                except Exception:
                    pass

            def scroll_begin(*_args):
                try:
                    page = scroll.children[0] if scroll.children else None
                    page_h = float(getattr(page, "height", 0) or 0)
                    print(
                        "[PLAYER-CORE-V2] drag start "
                        f"y={float(scroll.scroll_y):.3f} "
                        f"range={max(0.0, page_h - float(scroll.height)):.1f}"
                    )
                except Exception:
                    pass

            def scroll_end(*_args):
                align_after_stop()
                try:
                    print("[PLAYER-CORE-V2] drag end y=%.3f" % float(scroll.scroll_y))
                except Exception:
                    pass

            scroll.bind(on_scroll_start=scroll_begin, on_scroll_stop=scroll_end)
            # Keep the video rect in sync during normal/kinetic scrolling at
            # a bounded rate rather than re-layouting native views on every MOVE.
            def position_changed(*_args):
                try:
                    old = getattr(owner, "_player_core_align_ev_v2", None)
                    if old is not None:
                        old.cancel()
                    owner._player_core_align_ev_v2 = Clock.schedule_once(
                        lambda _dt: owner._align_video_to_thumb(), 0.07
                    )
                except Exception:
                    pass

            scroll.bind(scroll_y=position_changed)
            video_block = owner.ids["video_block"]
            video_block.bind(pos=position_changed, size=position_changed)

            # Observe the raw Kivy input stream without grabbing or consuming
            # touches. This distinguishes Android-native interception from
            # Kivy ScrollView gesture and geometry problems on the real device.
            def raw_down(_window, touch):
                if owner.manager is None or owner.manager.current != owner.name:
                    return False
                if not scroll.collide_point(*touch.pos):
                    return False
                touch.ud["player_core_v2_trace"] = (
                    float(touch.x), float(touch.y), float(scroll.scroll_y)
                )
                print(
                    "[PLAYER-TOUCH] down "
                    f"x={float(touch.x):.0f} y={float(touch.y):.0f} "
                    f"scroll={float(scroll.scroll_y):.4f}"
                )
                return False

            def raw_move(_window, touch):
                trace = touch.ud.get("player_core_v2_trace")
                if trace is None or touch.ud.get("player_core_v2_move_logged"):
                    return False
                dx = float(touch.x) - trace[0]
                dy = float(touch.y) - trace[1]
                if max(abs(dx), abs(dy)) >= dp(18):
                    touch.ud["player_core_v2_move_logged"] = True
                    print(
                        "[PLAYER-TOUCH] move "
                        f"dx={dx:.0f} dy={dy:.0f} "
                        f"scroll={float(scroll.scroll_y):.4f}"
                    )
                return False

            def raw_up(_window, touch):
                trace = touch.ud.pop("player_core_v2_trace", None)
                if trace is not None:
                    touch.ud.pop("player_core_v2_move_logged", None)
                    print(
                        "[PLAYER-TOUCH] up "
                        f"start={trace[2]:.4f} end={float(scroll.scroll_y):.4f} "
                        f"page_h={float(scroll.children[0].height) if scroll.children else 0:.0f} "
                        f"view_h={float(scroll.height):.0f}"
                    )
                return False

            Window.bind(
                on_touch_down=raw_down,
                on_touch_move=raw_move,
                on_touch_up=raw_up,
            )
            _report_layout(owner)

        def init_v2(self, *args, **kwargs):
            previous_init(self, *args, **kwargs)
            for delay in (0, 0.08, 0.20):
                Clock.schedule_once(lambda dt, owner=self: attach(owner), delay)

        def enter_v2(self, *args, **kwargs):
            result = previous_enter(self, *args, **kwargs)
            attach(self)
            _render_playlist(self)
            _render_similar(self)
            return result

        def ensure_video_v2(self, *args, **kwargs):
            result = previous_ensure(self, *args, **kwargs)
            owner = getattr(self, "_video_player", None)
            if owner is not None:
                passive_native(owner)
            return result

        def toggle_playlist_v2(self):
            self._playlist_collapsed = not bool(getattr(self, "_playlist_collapsed", False))
            _update_section(self, "playlist")

        def toggle_similar_v2(self):
            self._similar_collapsed = not bool(getattr(self, "_similar_collapsed", False))
            _update_section(self, "similar")

        screen_cls.__init__ = init_v2
        screen_cls.on_pre_enter = enter_v2
        screen_cls._ensure_video_player = ensure_video_v2
        screen_cls.toggle_playlist_collapsed = toggle_playlist_v2
        screen_cls.toggle_similar_collapsed = toggle_similar_v2
        screen_cls._render_playlist_ui = _render_playlist
        screen_cls._render_similar_ui = _render_similar
        screen_cls._pymusic_player_core_rewrite_v2 = True
        _INSTALLED = True
        print("[PLAYER-CORE-V2] installed: static KV + one scroll + passive native video")
        return True


def install_player_screen_rewrite():
    # Called repeatedly by recent_utils until all prerequisites exist.
    # True means installed, not scheduled.
    try:
        return _install_now()
    except Exception as exc:
        print("[PLAYER-CORE-V2] installation failure:", exc)
        return False
