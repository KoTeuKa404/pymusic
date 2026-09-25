"""Robust native-video vertical drag bridge for the outer Kivy player scroll.

V14 fixes two lifecycle/geometry problems left in V13:

* after app resume, resume_ui_fix may still restore the previously saved scroll_y
  for ~450 ms; every native drag now updates that saved state so a delayed
  restore cannot undo the user's new position;
* playlist/player content height can change while rows are rendered, so scroll
  extent is computed from max(height, minimum_height), not height alone.

The listener is also rebound after every native video bounds update.  This
prevents a later SurfaceView/overlay refresh from restoring an older touch
listener after video becomes visible.

Native rewind/play/forward ImageButtons stay on the existing Java ACTION_DOWN
transport path.  Only the SurfaceView and empty controls-overlay area use this
listener.
"""
from __future__ import annotations

from kivy.clock import Clock

_PATCHED = False


def install_video_scroll_bridge_v14() -> bool:
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

        if not bool(getattr(screen_cls, "_pymusic_playlist_single_scroll_v12", False)):
            return False
        if not bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            return False
        if not bool(getattr(video_cls, "_pymusic_native_java_transport_v1", False)):
            return False

        if bool(getattr(screen_cls, "_pymusic_video_scroll_bridge_v14", False)):
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

        def _real_content_height(child) -> float:
            if child is None:
                return 0.0
            values = []
            for name in ("height", "minimum_height"):
                try:
                    values.append(float(getattr(child, name, 0) or 0))
                except Exception:
                    pass
            return max(values or [0.0])

        def _force_scroll_update(scroll) -> None:
            try:
                update = getattr(scroll, "_update_from_scroll", None)
                if callable(update):
                    update()
            except Exception:
                pass
            try:
                trigger = getattr(scroll, "_trigger_update_from_scroll", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass
            try:
                scroll.canvas.ask_update()
            except Exception:
                pass

        def _sync_resume_state(screen, value: float) -> None:
            # resume_ui_fix reads this attribute in its delayed callbacks.
            # Keeping it current prevents 0.04/0.10/0.22/0.45 s restores from
            # snapping a just-started native-video drag back to the old offset.
            try:
                screen._pymusic_resume_outer_y = max(0.0, min(1.0, float(value)))
            except Exception:
                pass

        def _schedule_video_align(screen, immediate=False) -> None:
            try:
                old = getattr(screen, "_pymusic_video_align_event_v14", None)
                if old is not None:
                    try:
                        old.cancel()
                    except Exception:
                        pass
                delay = 0.0 if immediate else 0.01
                screen._pymusic_video_align_event_v14 = Clock.schedule_once(
                    lambda _dt, owner=screen: _finish_align(owner),
                    delay,
                )
            except Exception:
                pass

        def _finish_align(screen) -> None:
            screen._pymusic_video_align_event_v14 = None
            try:
                screen._align_video_to_thumb()
            except Exception:
                pass

        def apply_pending_scroll(screen) -> None:
            try:
                delta = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v14", 0.0) or 0.0
                )
                screen._pymusic_video_scroll_pending_v14 = 0.0
                screen._pymusic_video_scroll_event_v14 = None
                if abs(delta) < 0.01:
                    return

                outer = screen.ids.get("player_details_scroll")
                if outer is None or not bool(getattr(outer, "do_scroll_y", False)):
                    return
                child = outer.children[0] if outer.children else None
                if child is None:
                    return

                content_h = _real_content_height(child)
                viewport_h = float(getattr(outer, "height", 0) or 0)
                scroll_range = max(0.0, content_h - viewport_h)
                if scroll_range <= 1.0:
                    return

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

                current = max(
                    0.0,
                    min(1.0, float(getattr(outer, "scroll_y", 1.0) or 0.0)),
                )
                target = max(
                    0.0,
                    min(1.0, current + (float(delta) / float(scroll_range))),
                )

                if abs(target - current) > 0.000001:
                    outer.scroll_y = target
                    _force_scroll_update(outer)
                    _sync_resume_state(screen, target)
                    _schedule_video_align(screen)

                    screen._pymusic_video_scroll_last_range_v14 = scroll_range
                    screen._pymusic_video_scroll_last_y_v14 = target
            except Exception as exc:
                try:
                    print("[VIDEO-SCROLL-V14] apply failed:", exc)
                except Exception:
                    pass

        def queue_scroll(screen, delta_y: float) -> None:
            try:
                screen._pymusic_video_scroll_pending_v14 = float(
                    getattr(screen, "_pymusic_video_scroll_pending_v14", 0.0) or 0.0
                ) + float(delta_y)
                if getattr(screen, "_pymusic_video_scroll_event_v14", None) is None:
                    screen._pymusic_video_scroll_event_v14 = Clock.schedule_once(
                        lambda _dt, owner=screen: apply_pending_scroll(owner),
                        0,
                    )
            except Exception:
                pass

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

        class ScrollAwareVideoTouchV14(video_player.PythonJavaClass):
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
                self._moved_px = 0.0

            def _reset(self):
                self._dragging = False
                self._cancel_tap = False
                self._moved_px = 0.0

            @video_player.java_method("(Landroid/view/View;Landroid/view/MotionEvent;)Z")
            def onTouch(self, view, event):
                try:
                    action = int(event.getAction()) & 0xFF
                    raw_x = float(event.getRawX())
                    raw_y = float(event.getRawY())
                    screen = getattr(
                        self._owner,
                        "_pymusic_scroll_screen_v14",
                        None,
                    )

                    if action == 0:  # ACTION_DOWN
                        self._down_x = raw_x
                        self._down_y = raw_y
                        self._last_y = raw_y
                        self._dragging = False
                        self._cancel_tap = False
                        self._moved_px = 0.0
                        if screen is not None:
                            try:
                                outer = screen.ids.get("player_details_scroll")
                                if outer is not None:
                                    _sync_resume_state(screen, float(outer.scroll_y))
                            except Exception:
                                pass
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
                                if screen is not None:
                                    try:
                                        outer = screen.ids.get("player_details_scroll")
                                        child = (
                                            outer.children[0]
                                            if outer is not None and outer.children
                                            else None
                                        )
                                        print(
                                            "[VIDEO-SCROLL-V14] drag start "
                                            f"y={float(getattr(outer, 'scroll_y', 1.0) or 0.0):.4f} "
                                            f"content={_real_content_height(child):.1f} "
                                            f"viewport={float(getattr(outer, 'height', 0) or 0):.1f}"
                                        )
                                    except Exception:
                                        pass
                            elif abs(dx) >= touch_slop:
                                self._cancel_tap = True

                        if self._dragging and screen is not None:
                            dy = raw_y - self._last_y
                            self._last_y = raw_y
                            self._moved_px += abs(dy)
                            if abs(dy) > 0.01:
                                queue_scroll(screen, dy)
                        return True

                    if action == 1:  # ACTION_UP
                        if self._dragging:
                            if screen is not None:
                                # Flush a final frame and snap native geometry to
                                # the Kivy placeholder after the gesture ends.
                                try:
                                    apply_pending_scroll(screen)
                                except Exception:
                                    pass
                                _schedule_video_align(screen, immediate=True)
                                try:
                                    outer = screen.ids.get("player_details_scroll")
                                    print(
                                        "[VIDEO-SCROLL-V14] drag end "
                                        f"moved_px={self._moved_px:.1f} "
                                        f"y={float(getattr(outer, 'scroll_y', 1.0) or 0.0):.4f} "
                                        f"range={float(getattr(screen, '_pymusic_video_scroll_last_range_v14', 0.0) or 0.0):.1f}"
                                    )
                                except Exception:
                                    pass
                        elif not self._cancel_tap:
                            cb = getattr(self._owner, "_tap_callback", None)
                            if callable(cb):
                                cb(zone_for_touch(self._owner, view, event))
                        self._reset()
                        return True

                    if action == 3:  # ACTION_CANCEL
                        if self._dragging and screen is not None:
                            try:
                                apply_pending_scroll(screen)
                            except Exception:
                                pass
                            _schedule_video_align(screen, immediate=True)
                        self._reset()
                        return True

                    self._cancel_tap = True
                    return True
                except Exception:
                    return True

        old_bind_surface = video_cls._bind_surface_tap

        def bind_surface_v14(self):
            try:
                if self.surface_view is None:
                    return
                screen = getattr(self, "_pymusic_scroll_screen_v14", None)
                if screen is None:
                    return old_bind_surface(self)

                if not isinstance(
                    getattr(self, "_tap_listener", None),
                    ScrollAwareVideoTouchV14,
                ):
                    self._tap_listener = ScrollAwareVideoTouchV14(self)

                self.surface_view.setClickable(True)
                self.surface_view.setOnTouchListener(self._tap_listener)
            except Exception as exc:
                try:
                    print("[VIDEO-SCROLL-V14] surface bind failed:", exc)
                except Exception:
                    pass

        video_cls._bind_surface_tap = bind_surface_v14
        video_cls._OnTouchListener = ScrollAwareVideoTouchV14

        old_apply_bounds = video_cls._apply_surface_bounds

        def apply_bounds_v14(self, *args, **kwargs):
            result = old_apply_bounds(self, *args, **kwargs)
            try:
                bind_surface_v14(self)
                # native_java_transport_fix keeps the first three ImageButtons
                # on Java while binding the empty overlay/bar to _tap_listener.
                self._bind_controls_tap()
            except Exception:
                pass
            return result

        video_cls._apply_surface_bounds = apply_bounds_v14

        old_ensure = screen_cls._ensure_video_player

        def ensure_video_player_v14(self, *args, **kwargs):
            result = old_ensure(self, *args, **kwargs)
            vp = getattr(self, "_video_player", None)
            if result and vp is not None:
                try:
                    vp._pymusic_scroll_screen_v14 = self
                    if not isinstance(
                        getattr(vp, "_tap_listener", None),
                        ScrollAwareVideoTouchV14,
                    ):
                        vp._tap_listener = ScrollAwareVideoTouchV14(vp)
                    bind_surface_v14(vp)
                    vp._bind_controls_tap()
                except Exception as exc:
                    print("[VIDEO-SCROLL-V14] ensure bind failed:", exc)
            return result

        screen_cls._ensure_video_player = ensure_video_player_v14
        screen_cls._pymusic_video_scroll_bridge_v14 = True
        video_cls._pymusic_video_scroll_bridge_v14 = True

        _PATCHED = True
        print(
            "[VIDEO-SCROLL-V14] robust native video drag bridge enabled "
            f"slop={touch_slop}"
        )
        return True
    except Exception as exc:
        print("[VIDEO-SCROLL-V14] install failed:", exc)
        return False
