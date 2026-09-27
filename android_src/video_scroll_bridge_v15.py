"""Final native-video drag bridge.

The Kivy video_block is a fixed sibling above player_details_scroll. Scrolling
the details/playlist must therefore NEVER relayout the Android SurfaceView while
an active finger gesture is in progress. V13/V14 did exactly that after each
MOVE, which could interrupt native touch delivery once real video became
visible.

V15 keeps native video geometry completely untouched during a drag. The native
SurfaceView / empty controls overlay only translate vertical MotionEvent deltas
into the one Kivy player_details_scroll. Native rewind/play/forward ImageButtons
remain bound to NativeTransportBridge and the native SeekBar is unchanged.
"""
from __future__ import annotations

from kivy.clock import Clock

_PATCHED = False


def install_video_scroll_bridge_v15() -> bool:
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

        # Install only after every layer whose methods we must be final owner of.
        if not bool(getattr(screen_cls, "_pymusic_playlist_single_scroll_v12", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_final_player_v2", False)):
            return False

        if bool(getattr(screen_cls, "_pymusic_video_scroll_bridge_v15", False)):
            _PATCHED = True
            return True

        try:
            ViewConfiguration = video_player.autoclass("android.view.ViewConfiguration")
            touch_slop = max(
                12,
                int(
                    ViewConfiguration.get(video_player.PythonActivity.mActivity)
                    .getScaledTouchSlop()
                ),
            )
        except Exception:
            touch_slop = 18

        def _content_height(child) -> float:
            if child is None:
                return 0.0
            result = 0.0
            for attr in ("height", "minimum_height"):
                try:
                    result = max(result, float(getattr(child, attr, 0) or 0))
                except Exception:
                    pass
            return result

        def _apply_scroll(screen) -> None:
            try:
                delta = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v15", 0.0) or 0.0
                )
                screen._pymusic_video_scroll_pending_v15 = 0.0
                screen._pymusic_video_scroll_event_v15 = None
                if abs(delta) < 0.01:
                    return

                outer = screen.ids.get("player_details_scroll")
                if outer is None:
                    return
                if not bool(getattr(outer, "do_scroll_y", False)):
                    return
                if bool(getattr(outer, "disabled", False)):
                    return

                child = outer.children[0] if outer.children else None
                content_h = _content_height(child)
                viewport_h = max(0.0, float(getattr(outer, "height", 0) or 0))
                scroll_range = max(0.0, content_h - viewport_h)
                if scroll_range <= 1.0:
                    return

                current = max(
                    0.0,
                    min(1.0, float(getattr(outer, "scroll_y", 1.0) or 0.0)),
                )
                target = max(
                    0.0,
                    min(1.0, current + (delta / scroll_range)),
                )

                if abs(target - current) <= 0.000001:
                    return

                # Kill any old kinetic motion before applying our native-drag
                # position. Do NOT touch SurfaceView bounds here.
                try:
                    effect = getattr(outer, "effect_y", None)
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

                outer.scroll_y = target
                try:
                    update = getattr(outer, "_update_from_scroll", None)
                    if callable(update):
                        update()
                except Exception:
                    pass
                try:
                    outer.canvas.ask_update()
                except Exception:
                    pass

                # A delayed resume callback must not restore an older offset.
                try:
                    screen._pymusic_resume_outer_y = target
                except Exception:
                    pass

                screen._pymusic_video_scroll_last_y_v15 = target
                screen._pymusic_video_scroll_last_range_v15 = scroll_range
            except Exception as exc:
                try:
                    print("[VIDEO-SCROLL-V15] apply failed:", exc)
                except Exception:
                    pass

        def _queue_scroll(screen, delta_y: float) -> None:
            try:
                screen._pymusic_video_scroll_pending_v15 = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v15", 0.0) or 0.0
                ) + float(delta_y)
                if getattr(screen, "_pymusic_video_scroll_event_v15", None) is None:
                    screen._pymusic_video_scroll_event_v15 = Clock.schedule_once(
                        lambda _dt, owner=screen: _apply_scroll(owner),
                        0,
                    )
            except Exception:
                pass

        def _zone(owner, view, event) -> str:
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

        class FinalVideoTouch(video_player.PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnTouchListener"]
            __javacontext__ = "app"

            def __init__(self, owner):
                super().__init__()
                self._owner = owner
                self._down_x = 0.0
                self._down_y = 0.0
                self._last_y = 0.0
                self._dragging = False
                self._horizontal = False
                self._moved = 0.0

            def _reset(self):
                self._dragging = False
                self._horizontal = False
                self._moved = 0.0

            @video_player.java_method("(Landroid/view/View;Landroid/view/MotionEvent;)Z")
            def onTouch(self, view, event):
                try:
                    action = int(event.getAction()) & 0xFF
                    x = float(event.getRawX())
                    y = float(event.getRawY())
                    screen = getattr(self._owner, "_pymusic_scroll_screen_v15", None)

                    if action == 0:  # DOWN
                        self._down_x = x
                        self._down_y = y
                        self._last_y = y
                        self._dragging = False
                        self._horizontal = False
                        self._moved = 0.0
                        return True

                    if action == 2:  # MOVE
                        dx = x - self._down_x
                        total_y = y - self._down_y

                        if not self._dragging and not self._horizontal:
                            if (
                                abs(total_y) >= touch_slop
                                and abs(total_y) >= abs(dx)
                            ):
                                self._dragging = True
                                try:
                                    print("[VIDEO-SCROLL-V15] drag start")
                                except Exception:
                                    pass
                            elif abs(dx) >= touch_slop:
                                self._horizontal = True

                        if self._dragging and screen is not None:
                            dy = y - self._last_y
                            self._last_y = y
                            self._moved += abs(dy)
                            if abs(dy) > 0.01:
                                _queue_scroll(screen, dy)
                        return True

                    if action == 1:  # UP
                        if self._dragging:
                            if screen is not None:
                                try:
                                    _apply_scroll(screen)
                                except Exception:
                                    pass
                                try:
                                    print(
                                        "[VIDEO-SCROLL-V15] drag end "
                                        f"moved={self._moved:.1f} "
                                        f"y={float(getattr(screen, '_pymusic_video_scroll_last_y_v15', 1.0) or 0.0):.4f} "
                                        f"range={float(getattr(screen, '_pymusic_video_scroll_last_range_v15', 0.0) or 0.0):.1f}"
                                    )
                                except Exception:
                                    pass
                        elif not self._horizontal:
                            cb = getattr(self._owner, "_tap_callback", None)
                            if callable(cb):
                                cb(_zone(self._owner, view, event))
                        self._reset()
                        return True

                    if action == 3:  # CANCEL
                        if self._dragging and screen is not None:
                            try:
                                _apply_scroll(screen)
                            except Exception:
                                pass
                        try:
                            print("[VIDEO-SCROLL-V15] drag cancel")
                        except Exception:
                            pass
                        self._reset()
                        return True

                    return True
                except Exception as exc:
                    try:
                        print("[VIDEO-SCROLL-V15] touch error:", exc)
                    except Exception:
                        pass
                    return True

        video_cls._OnTouchListener = FinalVideoTouch

        @video_player.run_on_ui_thread
        def _bind_native_touch(owner) -> None:
            try:
                surface = getattr(owner, "surface_view", None)
                if surface is not None:
                    if not isinstance(
                        getattr(owner, "_tap_listener", None),
                        FinalVideoTouch,
                    ):
                        owner._tap_listener = FinalVideoTouch(owner)
                    surface.setClickable(True)
                    surface.setOnTouchListener(owner._tap_listener)

                # native_java_transport_fix binds the first three ImageButtons
                # directly to Java, while empty overlay/bar get _tap_listener.
                try:
                    owner._bind_controls_tap()
                except Exception:
                    pass
            except Exception as exc:
                try:
                    print("[VIDEO-SCROLL-V15] native bind failed:", exc)
                except Exception:
                    pass

        # Final owner of SurfaceView tap binding.
        def bind_surface_v15(self):
            _bind_native_touch(self)

        video_cls._bind_surface_tap = bind_surface_v15

        old_ensure = screen_cls._ensure_video_player

        def ensure_v15(self, *args, **kwargs):
            result = old_ensure(self, *args, **kwargs)
            vp = getattr(self, "_video_player", None)
            if result and vp is not None:
                try:
                    vp._pymusic_scroll_screen_v15 = self
                    if not isinstance(
                        getattr(vp, "_tap_listener", None),
                        FinalVideoTouch,
                    ):
                        vp._tap_listener = FinalVideoTouch(vp)
                    _bind_native_touch(vp)
                except Exception as exc:
                    try:
                        print("[VIDEO-SCROLL-V15] ensure failed:", exc)
                    except Exception:
                        pass
            return result

        screen_cls._ensure_video_player = ensure_v15
        screen_cls._pymusic_video_scroll_bridge_v15 = True
        video_cls._pymusic_video_scroll_bridge_v15 = True

        _PATCHED = True
        print(
            "[VIDEO-SCROLL-V15] final native drag owner enabled "
            f"slop={touch_slop}; SurfaceView relayout during drag disabled"
        )
        return True
    except Exception as exc:
        print("[VIDEO-SCROLL-V15] install failed:", exc)
        return False
