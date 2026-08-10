"""Low-latency native Android video controls.

The visible controls over ``SurfaceView`` are Android ``ImageButton`` widgets,
not the Kivy ``video_controls`` row.  Their original shared touch listener waited
for ACTION_UP and then bounced the action through ``Clock.schedule_once`` before
it reached the audio/video MediaPlayers.  On a busy Kivy frame this can make a
tap feel 1+ second late.

This patch keeps the existing player architecture but makes the transport path
short and deterministic:

* native buttons fire on ACTION_DOWN;
* each native rewind/play/forward button maps to its own action explicitly;
* audio/video MediaPlayer pause/start/seek is issued immediately on Android's
  UI thread, before Kivy/notification bookkeeping;
* ACTION_UP is consumed without firing the action a second time;
* the native seek buttons get a little more non-clickable space around the
  central play/pause button while still fitting narrow phone screens.
"""
from __future__ import annotations

import time

from kivy.clock import Clock


_PATCHED = False


def _zone_for_touch(video_owner, view, event) -> str:
    """Prefer exact native button identity; use frame thirds only as fallback."""
    try:
        controls = list(getattr(video_owner, "_controls_touch_views", []) or [])
        # _ensure_controls_overlay appends rewind, play/pause, forward first.
        if len(controls) >= 3:
            if view == controls[0]:
                return "left"
            if view == controls[1]:
                return "center"
            if view == controls[2]:
                return "right"
    except Exception:
        pass

    try:
        frame = getattr(video_owner, "_frame_bounds", None)
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


def _tune_native_spacing(video_owner) -> None:
    """Increase the real Android button gaps, not the hidden Kivy row gaps."""
    try:
        controls = list(getattr(video_owner, "_controls_touch_views", []) or [])
        if len(controls) < 3:
            return

        frame = getattr(video_owner, "_frame_bounds", None)
        frame_w = int(frame[2]) if frame and len(frame) >= 3 else 540
        button_width_sum = 104 + 116 + 104
        available = max(0, frame_w - button_width_sum)
        # There are six horizontal margins (left+right for three buttons).
        # Clamp so the bar cannot become wider than a narrow video frame.
        margin = max(18, min(38, int(available / 6.0) if available else 32))

        for button in controls[:3]:
            try:
                lp = button.getLayoutParams()
                if lp is None:
                    continue
                lp.setMargins(int(margin), 0, int(margin), 0)
                button.setLayoutParams(lp)
            except Exception:
                pass
    except Exception:
        pass


def _schedule_controls_housekeeping(screen, *, playing=None, target_ms=None) -> None:
    """Do Kivy/notification work after the transport command already happened."""
    def _finish(_dt):
        try:
            if playing is not None:
                screen._ui_set_playing(bool(playing))
        except Exception:
            pass

        if target_ms is not None:
            try:
                screen.ids.current_time_label.text = screen._fmt_ms(int(target_ms))
            except Exception:
                pass
            try:
                screen.ids.progress_slider.value = float(target_ms) / 1000.0
            except Exception:
                pass

        try:
            screen._set_video_controls_visible(True)
        except Exception:
            pass

        if playing:
            try:
                Clock.schedule_once(lambda _dt2: screen._force_video_resync(), 0.20)
            except Exception:
                pass

    Clock.schedule_once(_finish, 0)


def _show_controls_immediately(screen) -> None:
    """First tap only reveals controls, matching the previous UX."""
    try:
        screen._video_controls_visible = True
    except Exception:
        pass
    try:
        vp = getattr(screen, "_video_player", None)
        if vp is not None:
            _tune_native_spacing(vp)
            vp.set_native_controls_visible(True)
    except Exception:
        pass
    try:
        Clock.schedule_once(lambda _dt: screen._set_video_controls_visible(True), 0)
    except Exception:
        pass


def _native_transport(screen, zone: str) -> None:
    """Execute the actual transport command synchronously on Android UI thread."""
    try:
        if not bool(getattr(screen, "_video_controls_visible", False)):
            _show_controls_immediately(screen)
            return

        now = time.monotonic()
        last_ts = float(getattr(screen, "_pymusic_native_touch_ts", 0.0) or 0.0)
        last_zone = str(getattr(screen, "_pymusic_native_touch_zone", "") or "")
        # Defensive guard in case a device dispatches the same DOWN through more
        # than one bound native View.
        if zone == last_zone and (now - last_ts) < 0.10:
            return
        screen._pymusic_native_touch_ts = now
        screen._pymusic_native_touch_zone = zone

        media = getattr(screen, "ma", None)
        if media is None:
            import media_android as media

        audio = getattr(media, "android_player", None)
        prepared = bool(audio is not None and media._is_prepared())
        vp = getattr(screen, "_video_player", None)

        if zone == "center":
            if not prepared:
                # Fall back to the normal Kivy path only when there is no live
                # MediaPlayer to control directly.
                Clock.schedule_once(lambda _dt: screen.video_toggle_play(), 0)
                return

            try:
                was_playing = bool(audio.isPlaying())
            except Exception:
                was_playing = bool(
                    getattr(screen, "_playback_desired", False)
                    and not getattr(screen, "_user_paused", False)
                )

            if was_playing:
                try:
                    screen._resume_pos_ms = int(audio.getCurrentPosition() or 0)
                except Exception:
                    pass
                screen._user_paused = True
                screen._playback_desired = False
                try:
                    audio.pause()
                    media.is_playing = False
                except Exception:
                    try:
                        media._mp_pause()
                    except Exception:
                        pass
                try:
                    if vp is not None and getattr(vp, "player", None) is not None and bool(getattr(vp, "_prepared", False)):
                        if vp.player.isPlaying():
                            vp.player.pause()
                except Exception:
                    pass
                try:
                    if vp is not None:
                        vp.set_native_playing(False)
                except Exception:
                    pass
                _schedule_controls_housekeeping(screen, playing=False)
            else:
                screen._user_paused = False
                screen._playback_desired = True
                try:
                    audio.start()
                    media.is_playing = True
                except Exception:
                    try:
                        media._mp_start()
                    except Exception:
                        pass
                try:
                    if vp is not None and getattr(vp, "player", None) is not None and bool(getattr(vp, "_prepared", False)):
                        if not vp.player.isPlaying():
                            vp.player.start()
                except Exception:
                    pass
                try:
                    if vp is not None:
                        vp.set_native_playing(True)
                except Exception:
                    pass
                _schedule_controls_housekeeping(screen, playing=True)
            return

        delta_ms = -10_000 if zone == "left" else 10_000
        try:
            current = int(audio.getCurrentPosition() or 0) if prepared else 0
        except Exception:
            current = 0
        target = max(0, current + delta_ms)

        if prepared:
            try:
                audio.seekTo(int(target))
            except Exception:
                try:
                    media._mp_seek_to(int(target))
                except Exception:
                    pass
        screen._resume_pos_ms = int(target)

        try:
            if vp is not None:
                if getattr(vp, "player", None) is not None and bool(getattr(vp, "_prepared", False)):
                    try:
                        # API 26+: closest decoded frame.
                        vp.player.seekTo(int(target), 3)
                    except Exception:
                        vp.player.seekTo(int(target))
                else:
                    vp._pending_start_pos_ms = int(target)
        except Exception:
            pass

        try:
            duration = int(audio.getDuration() or 0) if prepared else 0
            if vp is not None:
                vp.set_native_progress(int(target), max(1, duration))
        except Exception:
            pass

        _schedule_controls_housekeeping(screen, target_ms=int(target))
    except Exception as exc:
        try:
            print("[VIDEO-CTRL-FAST] transport failed:", exc)
        except Exception:
            pass


def install_native_video_controls_fast_fix() -> bool:
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
        if bool(getattr(video_cls, "_pymusic_native_controls_fast_v1", False)):
            _PATCHED = True
            return True

        class FastOnTouchListener(video_player.PythonJavaClass):
            __javainterfaces__ = ["android/view/View$OnTouchListener"]
            __javacontext__ = "app"

            def __init__(self, owner):
                super().__init__()
                self._owner = owner

            @video_player.java_method("(Landroid/view/View;Landroid/view/MotionEvent;)Z")
            def onTouch(self, view, event):
                try:
                    action = int(event.getAction()) & 0xFF
                    if action == 0:  # MotionEvent.ACTION_DOWN
                        zone = _zone_for_touch(self._owner, view, event)
                        cb = getattr(self._owner, "_tap_callback", None)
                        if callable(cb):
                            cb(zone)
                    # DOWN performs the action. UP/CANCEL are consumed only.
                    return True
                except Exception:
                    return True

        video_cls._OnTouchListener = FastOnTouchListener

        old_bring = video_cls._bring_controls_or_surface_front

        def bring_with_spacing(self, *args, **kwargs):
            _tune_native_spacing(self)
            result = old_bring(self, *args, **kwargs)
            _tune_native_spacing(self)
            return result

        video_cls._bring_controls_or_surface_front = bring_with_spacing

        old_ensure = screen_cls._ensure_video_player

        def ensure_video_player_fast(self, *args, **kwargs):
            result = old_ensure(self, *args, **kwargs)
            vp = getattr(self, "_video_player", None)
            if result and vp is not None:
                # Replace any listener instance created before this patch and bind
                # the native transport callback without the Kivy Clock hop.
                try:
                    if not isinstance(getattr(vp, "_tap_listener", None), FastOnTouchListener):
                        vp._tap_listener = None
                    vp.set_tap_callback(lambda zone="center", owner=self: _native_transport(owner, str(zone or "center")))
                    vp._bind_surface_tap()
                    vp._bind_controls_tap()
                    _tune_native_spacing(vp)
                except Exception as exc:
                    print("[VIDEO-CTRL-FAST] bind failed:", exc)
            return result

        screen_cls._ensure_video_player = ensure_video_player_fast
        video_cls._pymusic_native_controls_fast_v1 = True
        screen_cls._pymusic_native_controls_fast_v1 = True

        _PATCHED = True
        print("[VIDEO-CTRL-FAST] ACTION_DOWN native transport v1 enabled")
        return True
    except Exception as exc:
        print("[VIDEO-CTRL-FAST] patch install failed:", exc)
        return False
