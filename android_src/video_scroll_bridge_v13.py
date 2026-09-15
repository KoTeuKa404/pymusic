"""Bridge vertical drags on the native video layer into the Kivy player scroll.

AndroidVideoPlayer uses a native SurfaceView (and a transparent native controls
FrameLayout) above the Kivy tree. Those Views must consume their touch stream,
so Kivy's player_details_scroll never receives gestures that begin on video.

V13 keeps the three native transport ImageButtons on the existing Java
ACTION_DOWN path, but makes the SurfaceView/empty overlay distinguish a tap from
a vertical drag. A tap still uses the existing video-zone callback. A drag
updates the single outer Kivy ScrollView introduced by playlist v12.
"""
from __future__ import annotations

from kivy.clock import Clock

_PATCHED = False


def install_video_scroll_bridge_v13() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen
        import video_player

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        # Do not race the layers that own the actual buttons and playlist
        # geometry. recent_utils retries this installer until both are ready.
        if not bool(getattr(screen_cls, "_pymusic_playlist_single_scroll_v12", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False

        if bool(getattr(screen_cls, "_pymusic_video_scroll_bridge_v13", False)):
            _PATCHED = True
            return True

        try:
            ViewConfiguration = video_player.autoclass("android.view.ViewConfiguration")
            slop = int(
                ViewConfiguration.get(video_player.PythonActivity.mActivity)
                .getScaledTouchSlop()
            )
        except Exception:
            slop = 18
        touch_slop = max(12, slop)

        def zone_for_touch(owner, view, event) -> str:
            try:
                frame = getattr(owner, "_frame_bounds", None)
                if frame:
                    left, _top, frame_w, _frame_h = frame
                    width = float(frame_w or view.getWidth() or 1)
                    try:
                        x = float(event.getRawX()) - float(left)
                    except Exception:
                        x = float(event.getX() or 0)
                else:
                    width = float(view.getWidth() or 1)
                    x = float(event.getX() or 0)
                if x < width / 3.0:
                    return "left"
                if x > (width * 2.0) / 3.0:
                    return "right"
            except Exception:
                pass
            return "center"

        def apply_pending_scroll(screen) -> None:
            try:
                delta = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v13", 0.0) or 0.0
                )
                screen._pymusic_video_scroll_pending_v13 = 0.0
                screen._pymusic_video_scroll_event_v13 = None
                if abs(delta) < 0.01:
                    return

                outer = screen.ids.get("player_details_scroll")
                if outer is None or not bool(getattr(outer, "do_scroll_y", False)):
                    return
                child = outer.children[0] if outer.children else None
                if child is None:
                    return

                content_h = float(getattr(child, "height", 0) or 0)
                viewport_h = float(getattr(outer, "height", 0) or 0)
                scroll_range = content_h - viewport_h
                if scroll_range <= 1.0:
                    return

                current = float(getattr(outer, "scroll_y", 1.0) or 0.0)
                outer.scroll_y = max(
                    0.0,
                    min(1.0, current + (delta / scroll_range)),
                )

                # SurfaceView is a native overlay, so keep it aligned with the
                # Kivy video placeholder while the page moves underneath it.
                try:
                    Clock.schedule_once(
                        lambda _dt: screen._align_video_to_thumb(),
                        0,
                    )
                except Exception:
                    pass
            except Exception as exc:
                try:
                    print("[VIDEO-SCROLL-V13] apply failed:", exc)
                except Exception:
                    pass

        def queue_scroll(screen, delta_y: float) -> None:
            try:
                screen._pymusic_video_scroll_pending_v13 = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v13", 0.0) or 0.0
                ) + float(delta_y)
                if getattr(screen, "_pymusic_video_scroll_event_v13", None) is None:
                    screen._pymusic_video_scroll_event_v13 = Clock.schedule_once(
                        lambda _dt, owner=screen: apply_pending_scroll(owner),
                        0,
                    )
            except Exception:
                pass

        class ScrollAwareVideoTouch(video_player.PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnTouchListener"]
            __javacontext__ = "app"

            def __init__(self, owner):
                super().__init__()
                self._owner = owner
                self._down_x = 0.0
                self._down_y = 0.0
                self._last_y = 0.0
                self._dragging = False
                self._cancel_tap = False

            def _reset(self):
                self._dragging = False
                self._cancel_tap = False

            @video_player.java_method("(Landroid/view/View;Landroid/view/MotionEvent;)Z")
            def onTouch(self, view, event):
                try:
                    action = int(event.getAction()) & 0xFF
                    raw_x = float(event.getRawX())
                    raw_y = float(event.getRawY())

                    if action == 0:  # ACTION_DOWN
                        self._down_x = raw_x
                        self._down_y = raw_y
                        self._last_y = raw_y
                        self._dragging = False
                        self._cancel_tap = False
                        return True

                    if action == 2:  # ACTION_MOVE
                        dx = raw_x - self._down_x
                        dy_total = raw_y - self._down_y
                        if not self._dragging:
                            if (
                                abs(dy_total) >= touch_slop
                                and abs(dy_total) >= abs(dx)
                            ):
                                self._dragging = True
                            elif abs(dx) >= touch_slop:
                                self._cancel_tap = True

                        if self._dragging:
                            dy = raw_y - self._last_y
                            self._last_y = raw_y
                            screen = getattr(
                                self._owner,
                                "_pymusic_scroll_screen_v13",
                                None,
                            )
                            if screen is not None and abs(dy) > 0.01:
                                queue_scroll(screen, dy)
                        return True

                    if action == 1:  # ACTION_UP
                        if not self._dragging and not self._cancel_tap:
                            cb = getattr(self._owner, "_tap_callback", None)
                            if callable(cb):
                                cb(zone_for_touch(self._owner, view, event))
                        self._reset()
                        return True

                    if action == 3:  # ACTION_CANCEL
                        self._reset()
                        return True

                    # Multi-pointer/other actions are consumed so the native
                    # overlay never leaks a partial gesture into Kivy.
                    self._cancel_tap = True
                    return True
                except Exception:
                    return True

        # Replace only the listener class used by SurfaceView / empty overlay.
        # native_java_transport_fix owns the first three ImageButtons and keeps
        # rebinding them to NativeTransportBridge.
        video_cls._OnTouchListener = ScrollAwareVideoTouch

        old_ensure = screen_cls._ensure_video_player

        def ensure_video_player_v13(self, *args, **kwargs):
            result = old_ensure(self, *args, **kwargs)
            vp = getattr(self, "_video_player", None)
            if result and vp is not None:
                try:
                    vp._pymusic_scroll_screen_v13 = self
                    if not isinstance(
                        getattr(vp, "_tap_listener", None),
                        ScrollAwareVideoTouch,
                    ):
                        vp._tap_listener = ScrollAwareVideoTouch(vp)
                    vp._bind_surface_tap()
                    # This method is already owned by native_java_transport_fix:
                    # buttons stay Java; only overlay/bar receive our listener.
                    vp._bind_controls_tap()
                except Exception as exc:
                    print("[VIDEO-SCROLL-V13] bind failed:", exc)
            return result

        screen_cls._ensure_video_player = ensure_video_player_v13
        screen_cls._pymusic_video_scroll_bridge_v13 = True
        video_cls._pymusic_video_scroll_bridge_v13 = True

        _PATCHED = True
        print(
            "[VIDEO-SCROLL-V13] native video tap/vertical-drag bridge enabled "
            f"slop={touch_slop}"
        )
        return True
    except Exception as exc:
        print("[VIDEO-SCROLL-V13] install failed:", exc)
        return False
