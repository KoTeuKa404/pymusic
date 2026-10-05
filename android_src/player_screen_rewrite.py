"""Canonical player page/input architecture.

This module replaces the old nested-scroll/touch patch chain with one simple
layout:

    core Kivy ScrollView
      -> GridLayout page
           -> video_block
           -> existing player details content

The playlist and similar-video MDLists are re-parented out of their nested
MDScrollViews, so there is exactly one vertical gesture owner for the player
page.  The Android SurfaceView and the full-frame controls overlay are passive;
only the three Java transport buttons and the native SeekBar remain native touch
targets.  A drag beginning directly on the video therefore falls through to
Kivy and scrolls the same page as a drag beginning on a playlist row.

The installer deliberately waits for the existing final/native runtime layers
and then becomes the last owner of player-page geometry and input.
"""
from __future__ import annotations

import threading
import time

from kivy.clock import Clock
from kivy.effects.scroll import ScrollEffect
from kivy.metrics import dp
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView

_LOCK = threading.RLock()
_INSTALLED = False
_STARTED = False


def _request_layout(widget) -> None:
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


def _content_height(listing) -> float:
    if listing is None:
        return 0.0
    try:
        value = float(getattr(listing, "minimum_height", 0.0) or 0.0)
        if value > 0.0:
            return value
    except Exception:
        pass

    total = 0.0
    try:
        total = sum(float(getattr(child, "height", 0.0) or 0.0) for child in listing.children)
    except Exception:
        total = 0.0
    try:
        spacing = float(getattr(listing, "spacing", 0.0) or 0.0)
        if len(listing.children) > 1:
            total += spacing * (len(listing.children) - 1)
    except Exception:
        pass
    try:
        padding = getattr(listing, "padding", (0, 0, 0, 0))
        if len(padding) == 2:
            total += float(padding[1]) * 2.0
        elif len(padding) >= 4:
            total += float(padding[1]) + float(padding[3])
    except Exception:
        pass
    return max(0.0, total)


def _playlist_signature(screen):
    try:
        return tuple(
            (
                str(item.get("url") or ""),
                str(item.get("video_id") or ""),
                str(item.get("title") or ""),
                str(item.get("channel") or ""),
                str(item.get("thumb") or ""),
                str(item.get("duration") or ""),
            )
            for item in (screen.playlist.tracks if screen.playlist else [])
        )
    except Exception:
        return None


def _similar_signature(screen):
    try:
        return tuple(
            (
                str(item.get("url") or ""),
                str(item.get("video_id") or ""),
                str(item.get("title") or ""),
                str(item.get("channel") or ""),
                str(item.get("thumb") or item.get("thumbnail") or ""),
                str(item.get("duration") or ""),
            )
            for item in (getattr(screen, "_related_items", None) or [])
        )
    except Exception:
        return None


def _list_geometry(screen, kind: str) -> None:
    if kind == "playlist":
        listing = screen.ids.get("playlist_list")
        visible = bool(screen.playlist and screen.playlist.tracks)
        collapsed = bool(getattr(screen, "_playlist_collapsed", False))
        header_row = screen.ids.get("playlist_header_row")
        header = screen.ids.get("playlist_header")
        toggle = screen.ids.get("playlist_toggle_btn")
        title = screen.playlist.name or "Черга"
    else:
        listing = screen.ids.get("similar_list")
        visible = bool(getattr(screen, "_related_items", None))
        collapsed = bool(getattr(screen, "_similar_collapsed", False))
        header_row = screen.ids.get("similar_header_row")
        header = screen.ids.get("similar_header")
        toggle = screen.ids.get("similar_toggle_btn")
        title = "Схожі відео"

    try:
        screen._set_collapsible_header(
            header_row,
            header,
            toggle,
            visible,
            title,
            collapsed,
        )
    except Exception:
        pass

    if listing is None:
        return

    expanded = bool(visible and not collapsed)
    height = _content_height(listing) if expanded else 0.0
    try:
        listing.size_hint_y = None
        listing.height = height
        listing.opacity = 1.0 if expanded else 0.0
        listing.disabled = not expanded
    except Exception:
        pass
    _request_layout(listing)

    page = getattr(screen, "_pymusic_player_page", None)
    details = getattr(screen, "_pymusic_details_content", None)
    _request_layout(details)
    _request_layout(page)
    outer = getattr(screen, "_pymusic_outer_scroll", None)
    _request_layout(outer)


def _sync_all_geometry(screen, final: bool = False) -> None:
    try:
        _list_geometry(screen, "playlist")
        _list_geometry(screen, "similar")
        page = getattr(screen, "_pymusic_player_page", None)
        outer = getattr(screen, "_pymusic_outer_scroll", None)
        if page is not None:
            _request_layout(page)
        if outer is not None:
            _request_layout(outer)
        if final and outer is not None and page is not None:
            page_h = float(getattr(page, "height", 0.0) or 0.0)
            viewport_h = float(getattr(outer, "height", 0.0) or 0.0)
            print(
                "[PLAYER-CORE] geometry "
                f"page_h={page_h:.1f} viewport_h={viewport_h:.1f} "
                f"range={max(0.0, page_h - viewport_h):.1f} "
                f"scroll_y={float(getattr(outer, 'scroll_y', 1.0)):.3f}"
            )
    except Exception as exc:
        print("[PLAYER-CORE] geometry failed:", exc)


def _replace_nested_list(screen, container_id: str, list_id: str) -> None:
    container = screen.ids.get(container_id)
    listing = screen.ids.get(list_id)
    if container is None or listing is None:
        return
    if bool(getattr(listing, "_pymusic_direct_page_child", False)):
        return

    parent = getattr(container, "parent", None)
    if parent is None:
        return
    try:
        index = parent.children.index(container)
    except Exception:
        index = 0

    try:
        if listing.parent is container:
            container.remove_widget(listing)
    except Exception:
        pass
    try:
        parent.remove_widget(container)
    except Exception:
        return

    try:
        listing.size_hint_y = None
        listing.height = max(0.0, _content_height(listing))
    except Exception:
        pass
    try:
        parent.add_widget(listing, index=index)
    except Exception:
        parent.add_widget(listing)

    listing._pymusic_direct_page_child = True
    try:
        listing.bind(
            minimum_height=lambda *_args, owner=screen: Clock.schedule_once(
                lambda _dt: _sync_all_geometry(owner), 0
            )
        )
    except Exception:
        pass

    # Keep the old id object only as an inert compatibility reference.  It is
    # detached from the live tree and therefore cannot receive touch events.
    try:
        container.disabled = True
        container.opacity = 0.0
        container.height = 0.0
        container.do_scroll_y = False
    except Exception:
        pass

    print(f"[PLAYER-CORE] removed nested {container_id} from live tree")


def _queue_video_align(screen) -> None:
    try:
        event = getattr(screen, "_pymusic_video_align_event", None)
        if event is not None:
            event.cancel()
    except Exception:
        pass

    def apply(_dt=0):
        try:
            screen._pymusic_video_align_event = None
            screen._align_video_to_thumb()
        except Exception:
            pass

    try:
        screen._pymusic_video_align_event = Clock.schedule_once(apply, 0)
    except Exception:
        pass


def _mount_unified_page(screen) -> bool:
    if bool(getattr(screen, "_pymusic_unified_page_mounted", False)):
        return True

    ids = getattr(screen, "ids", {})
    old_outer = ids.get("player_details_scroll")
    video = ids.get("video_block")
    if old_outer is None or video is None or not getattr(old_outer, "children", None):
        return False

    details = old_outer.children[0]
    root_box = getattr(old_outer, "parent", None)
    video_parent = getattr(video, "parent", None)
    if root_box is None or video_parent is None or root_box is not video_parent:
        return False

    # First remove the two nested vertical ScrollViews while their exact visual
    # positions in the details BoxLayout are still available.
    _replace_nested_list(screen, "playlist_scroll", "playlist_list")
    _replace_nested_list(screen, "similar_scroll", "similar_list")

    try:
        outer_index = root_box.children.index(old_outer)
    except Exception:
        outer_index = 0

    try:
        old_outer.remove_widget(details)
    except Exception:
        return False
    try:
        root_box.remove_widget(video)
    except Exception:
        return False
    try:
        root_box.remove_widget(old_outer)
    except Exception:
        return False

    outer = ScrollView(
        do_scroll_x=False,
        do_scroll_y=True,
        bar_width=dp(3),
        scroll_type=["bars", "content"],
        size_hint=(1, 1),
    )
    try:
        outer.effect_cls = ScrollEffect
        outer.always_overscroll = False
        outer.scroll_timeout = 180
        outer.scroll_distance = dp(5)
    except Exception:
        pass

    page = GridLayout(
        cols=1,
        size_hint_y=None,
        spacing=0,
        padding=(0, 0, 0, 0),
    )
    page.bind(minimum_height=page.setter("height"))

    # GridLayout's normal insertion order gives video first, details second.
    page.add_widget(video)
    page.add_widget(details)
    outer.add_widget(page)
    try:
        root_box.add_widget(outer, index=outer_index)
    except Exception:
        root_box.add_widget(outer)

    try:
        ids["player_details_scroll"] = outer
    except Exception:
        pass

    screen._pymusic_outer_scroll = outer
    screen._pymusic_player_page = page
    screen._pymusic_details_content = details
    screen._pymusic_old_details_scroll = old_outer
    screen._pymusic_unified_page_mounted = True

    def scroll_start(widget, touch):
        try:
            print(
                "[PLAYER-CORE] scroll start "
                f"touch=({float(touch.x):.1f},{float(touch.y):.1f}) "
                f"y={float(widget.scroll_y):.3f}"
            )
        except Exception:
            pass
        _queue_video_align(screen)

    def scroll_move(widget, touch):
        _queue_video_align(screen)

    def scroll_stop(widget, touch):
        _queue_video_align(screen)
        try:
            print(f"[PLAYER-CORE] scroll stop y={float(widget.scroll_y):.3f}")
        except Exception:
            pass

    try:
        outer.bind(on_scroll_start=scroll_start)
        outer.bind(on_scroll_move=scroll_move)
        outer.bind(on_scroll_stop=scroll_stop)
        outer.bind(scroll_y=lambda *_a: _queue_video_align(screen))
    except Exception:
        pass
    try:
        video.bind(pos=lambda *_a: _queue_video_align(screen))
        video.bind(size=lambda *_a: _queue_video_align(screen))
    except Exception:
        pass

    _sync_all_geometry(screen)
    for delay in (0.0, 0.05, 0.15, 0.35):
        Clock.schedule_once(
            lambda _dt, owner=screen, last=(delay == 0.35): _sync_all_geometry(owner, final=last),
            delay,
        )
    Clock.schedule_once(lambda _dt: _queue_video_align(screen), 0)
    print("[PLAYER-CORE] unified video+details ScrollView mounted")
    return True


def _render_playlist(screen, force: bool = False):
    _mount_unified_page(screen)
    listing = screen.ids.get("playlist_list")
    if listing is None:
        return None

    tracks = list(screen.playlist.tracks if screen.playlist and screen.playlist.tracks else [])
    signature = _playlist_signature(screen)
    old_signature = getattr(screen, "_pymusic_core_playlist_sig", None)
    same_rows = bool(
        signature == old_signature
        and len(getattr(listing, "children", []) or []) == len(tracks)
    )

    # A forced render caused by video/metadata startup must not destroy widgets
    # while the user is dragging.  Rebuild only when the queue itself changed.
    if not same_rows:
        listing.clear_widgets()
        for index, item in enumerate(tracks):
            try:
                listing.add_widget(screen._make_playlist_row(index, item))
            except Exception as exc:
                print(f"[PLAYER-CORE] playlist row {index} failed: {exc}")
        screen._pymusic_core_playlist_sig = signature
        try:
            listing._trigger_layout()
        except Exception:
            pass

    _list_geometry(screen, "playlist")
    for delay in (0.0, 0.03, 0.10):
        Clock.schedule_once(lambda _dt, owner=screen: _sync_all_geometry(owner), delay)
    return None


def _render_similar(screen, force: bool = False):
    _mount_unified_page(screen)
    listing = screen.ids.get("similar_list")
    if listing is None:
        return None

    items = list(getattr(screen, "_related_items", None) or [])
    signature = _similar_signature(screen)
    old_signature = getattr(screen, "_pymusic_core_similar_sig", None)
    same_rows = bool(
        signature == old_signature
        and len(getattr(listing, "children", []) or []) == len(items)
    )
    if not same_rows:
        listing.clear_widgets()
        for index, item in enumerate(items):
            try:
                listing.add_widget(screen._make_similar_row(index, item))
            except Exception as exc:
                print(f"[PLAYER-CORE] similar row {index} failed: {exc}")
        screen._pymusic_core_similar_sig = signature
        try:
            listing._trigger_layout()
        except Exception:
            pass

    _list_geometry(screen, "similar")
    for delay in (0.0, 0.03, 0.10):
        Clock.schedule_once(lambda _dt, owner=screen: _sync_all_geometry(owner), delay)
    return None


def _install_now() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
            import video_player
        except Exception:
            return False

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        # Become final owner only after all existing layers that wrap init/video
        # controls have settled.  This removes timing-dependent patch order.
        required = (
            "_pymusic_hotfix_v4",
            "_pymusic_final_player_v2",
            "_pymusic_native_java_transport_v1",
            "_pymusic_latest_scroll_fix_v1",
        )
        if not all(bool(getattr(screen_cls, marker, False)) for marker in required):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False

        if bool(getattr(screen_cls, "_pymusic_player_core_rewrite_v1", False)):
            _INSTALLED = True
            return True

        old_init = screen_cls.__init__
        old_pre_enter = screen_cls.on_pre_enter
        old_pause = screen_cls.handle_app_pause
        old_resume = screen_cls.handle_app_resume
        old_ensure_video = screen_cls._ensure_video_player

        old_bind_controls = video_cls._bind_controls_tap
        old_ensure_overlay = video_cls._ensure_controls_overlay
        old_apply_bounds = video_cls._apply_surface_bounds
        old_visible = video_cls.set_native_controls_visible

        @video_player.run_on_ui_thread
        def apply_native_policy(vp):
            # Surface and empty full-frame overlay never own gestures.
            try:
                if vp.surface_view is not None:
                    vp.surface_view.setOnTouchListener(None)
                    vp.surface_view.setClickable(False)
                    vp.surface_view.setFocusable(False)
                    vp.surface_view.setFocusableInTouchMode(False)
            except Exception:
                pass
            try:
                if vp.controls_overlay is not None:
                    vp.controls_overlay.setOnTouchListener(None)
                    vp.controls_overlay.setClickable(False)
                    vp.controls_overlay.setFocusable(False)
            except Exception:
                pass

            # _controls_touch_views[0:3] are Java transport ImageButtons and
            # must keep their NativeTransportBridge listeners.  Anything after
            # them is only a container/background and must be passive.
            try:
                controls = list(getattr(vp, "_controls_touch_views", []) or [])
                for view in controls[3:]:
                    try:
                        view.setOnTouchListener(None)
                        view.setClickable(False)
                        view.setFocusable(False)
                    except Exception:
                        pass
            except Exception:
                pass

        def bind_surface_passive(self):
            apply_native_policy(self)

        def bind_controls_clean(self):
            result = old_bind_controls(self)
            apply_native_policy(self)
            return result

        def ensure_overlay_clean(self, *args, **kwargs):
            result = old_ensure_overlay(self, *args, **kwargs)
            apply_native_policy(self)
            return result

        def apply_bounds_clean(self, *args, **kwargs):
            result = old_apply_bounds(self, *args, **kwargs)
            apply_native_policy(self)
            return result

        def visible_clean(self, visible, *args, **kwargs):
            result = old_visible(self, visible, *args, **kwargs)
            apply_native_policy(self)
            return result

        video_cls._bind_surface_tap = bind_surface_passive
        video_cls._bind_controls_tap = bind_controls_clean
        video_cls._ensure_controls_overlay = ensure_overlay_clean
        video_cls._apply_surface_bounds = apply_bounds_clean
        video_cls.set_native_controls_visible = visible_clean

        def init_core(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self._pymusic_saved_page_scroll_y = 1.0
            for delay in (0.0, 0.04, 0.12):
                Clock.schedule_once(lambda _dt, owner=self: _mount_unified_page(owner), delay)

        def pre_enter_core(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.04, 0.12):
                Clock.schedule_once(lambda _dt, owner=self: _mount_unified_page(owner), delay)
            Clock.schedule_once(lambda _dt, owner=self: _render_playlist(owner, False), 0.02)
            Clock.schedule_once(lambda _dt, owner=self: _render_similar(owner, False), 0.03)
            return result

        def pause_core(self, *args, **kwargs):
            try:
                outer = getattr(self, "_pymusic_outer_scroll", None)
                if outer is not None:
                    self._pymusic_saved_page_scroll_y = max(0.0, min(1.0, float(outer.scroll_y)))
            except Exception:
                self._pymusic_saved_page_scroll_y = 1.0
            return old_pause(self, *args, **kwargs)

        def resume_core(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)

            def restore(_dt=0):
                _mount_unified_page(self)
                _sync_all_geometry(self)
                outer = getattr(self, "_pymusic_outer_scroll", None)
                if outer is not None:
                    try:
                        outer.scroll_y = max(
                            0.0,
                            min(1.0, float(getattr(self, "_pymusic_saved_page_scroll_y", 1.0))),
                        )
                    except Exception:
                        pass
                vp = getattr(self, "_video_player", None)
                if vp is not None:
                    apply_native_policy(vp)
                _queue_video_align(self)

            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(restore, delay)
            return result

        def ensure_video_core(self, *args, **kwargs):
            result = old_ensure_video(self, *args, **kwargs)
            vp = getattr(self, "_video_player", None)
            if vp is not None:
                apply_native_policy(vp)
            _queue_video_align(self)
            return result

        def toggle_playlist_core(self):
            self._playlist_collapsed = not bool(getattr(self, "_playlist_collapsed", False))
            _list_geometry(self, "playlist")
            Clock.schedule_once(lambda _dt: _sync_all_geometry(self, final=True), 0.02)

        def toggle_similar_core(self):
            self._similar_collapsed = not bool(getattr(self, "_similar_collapsed", False))
            _list_geometry(self, "similar")
            Clock.schedule_once(lambda _dt: _sync_all_geometry(self, final=True), 0.02)

        def render_playlist_core(self, force=False):
            return _render_playlist(self, force)

        def render_similar_core(self, force=False):
            return _render_similar(self, force)

        screen_cls.__init__ = init_core
        screen_cls.on_pre_enter = pre_enter_core
        screen_cls.handle_app_pause = pause_core
        screen_cls.handle_app_resume = resume_core
        screen_cls._ensure_video_player = ensure_video_core
        screen_cls.toggle_playlist_collapsed = toggle_playlist_core
        screen_cls.toggle_similar_collapsed = toggle_similar_core
        screen_cls._render_playlist_ui = render_playlist_core
        screen_cls._render_similar_ui = render_similar_core
        screen_cls._pymusic_player_core_rewrite_v1 = True

        _INSTALLED = True
        print(
            "[PLAYER-CORE] rewrite v1 enabled: one ScrollView owns video, "
            "playlist and similar gestures"
        )
        return True


def install_player_screen_rewrite() -> bool:
    global _STARTED
    if _install_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(800):
            if _install_now():
                return
            time.sleep(0.05)
        print("[PLAYER-CORE] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-player-core-rewrite",
        daemon=True,
    ).start()
    return True
