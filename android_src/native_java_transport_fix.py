"""Bind the visible SurfaceView transport buttons directly to Java.

The older Python/PyJNIus fast path still had one unavoidable latency source:
Android's OnTouch callback had to acquire the Python GIL before it could call
MediaPlayer.pause/start/seekTo.  This patch removes Python from that critical
path.  NativeTransportBridge handles ACTION_DOWN and the MediaPlayer command in
Java; Python only mirrors state afterwards for the rest of the app.
"""
from __future__ import annotations

from kivy.clock import Clock
from jnius import autoclass


_PATCHED = False
_SYNC_INTERVAL = 0.10


def install_native_java_transport_fix() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen
        import video_player

        media = audio_screen.ma
        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False

        if bool(getattr(screen_cls, "_pymusic_native_java_transport_v1", False)):
            _PATCHED = True
            return True

        Bridge = autoclass("org.koteuka404.pymusic.NativeTransportBridge")

        # ------------------------------------------------------------------
        # Native overlay binding.
        # ------------------------------------------------------------------
        def bind_controls_java(self):
            """Keep Python touch handling off the three transport ImageButtons."""
            try:
                controls = list(getattr(self, "_controls_touch_views", []) or [])
                actions = (-1, 0, 1)
                for index, button in enumerate(controls[:3]):
                    try:
                        Bridge.bindTransportButton(button, actions[index])
                    except Exception as exc:
                        print(f"[JAVA-CTRL] button bind {index} failed: {exc}")

                # The transparent overlay/bar may still use the normal Python
                # zone listener.  Crucially, it must never replace the Java
                # OnTouchListener installed on the first three ImageButtons.
                if self._tap_listener is None:
                    self._tap_listener = self._OnTouchListener(self)

                views = [self.controls_overlay]
                if len(controls) > 3:
                    views.extend(controls[3:])
                for view in views:
                    if view is None:
                        continue
                    try:
                        view.setClickable(True)
                        view.setOnTouchListener(self._tap_listener)
                    except Exception:
                        pass
            except Exception as exc:
                print("[JAVA-CTRL] bind controls failed:", exc)

        video_cls._bind_controls_tap = bind_controls_java

        old_ensure_overlay = video_cls._ensure_controls_overlay

        def ensure_overlay_java(self, *args, **kwargs):
            result = old_ensure_overlay(self, *args, **kwargs)
            try:
                bind_controls_java(self)
            except Exception:
                pass
            return result

        video_cls._ensure_controls_overlay = ensure_overlay_java

        # ------------------------------------------------------------------
        # Player reference/state synchronization. This is deliberately not in
        # the touch critical path; it can run later without affecting response.
        # ------------------------------------------------------------------
        old_init = screen_cls.__init__
        old_background_tick = screen_cls._background_tick
        old_pause = screen_cls._pause_playback
        old_resume = screen_cls._resume_playback
        old_play_audio = screen_cls.play_audio
        old_stop_audio = screen_cls.stop_audio

        def register_players(self):
            try:
                audio = getattr(media, "android_player", None)
                if audio is not getattr(self, "_pymusic_java_audio_ref", None):
                    Bridge.setAudioPlayer(audio)
                    self._pymusic_java_audio_ref = audio
            except Exception:
                pass

            try:
                vp = getattr(self, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp is not None else None
                if native_video is not getattr(self, "_pymusic_java_video_ref", None):
                    Bridge.setVideoPlayer(native_video)
                    self._pymusic_java_video_ref = native_video
            except Exception:
                pass

        def apply_java_event(self):
            register_players(self)
            try:
                version = int(Bridge.getStateVersion())
            except Exception:
                return

            previous = int(getattr(self, "_pymusic_java_state_version", version) or 0)
            if version == previous:
                # Keep Java's userPaused flag consistent with non-overlay actions
                # (notification, headset, Kivy buttons, track changes).
                try:
                    if bool(getattr(self, "_user_paused", False)):
                        Bridge.markPaused()
                    elif bool(getattr(self, "_playback_desired", False)):
                        Bridge.markPlaying()
                except Exception:
                    pass
                return

            self._pymusic_java_state_version = version
            try:
                event = int(Bridge.getLastEvent())
            except Exception:
                event = 0

            if event == 2:  # pause
                self._user_paused = True
                self._playback_desired = False
                try:
                    media.is_playing = False
                except Exception:
                    pass
                try:
                    audio = getattr(media, "android_player", None)
                    if audio is not None:
                        self._resume_pos_ms = int(audio.getCurrentPosition() or 0)
                except Exception:
                    pass
                try:
                    # Notification/Kivy bookkeeping happens only after native
                    # playback has already stopped.
                    self._ui_set_playing(False)
                except Exception:
                    pass

            elif event == 1:  # play
                self._user_paused = False
                self._playback_desired = True
                try:
                    media.is_playing = True
                except Exception:
                    pass
                try:
                    self._ui_set_playing(True)
                except Exception:
                    pass

            elif event in (3, 4):  # rewind / forward
                try:
                    target = max(0, int(Bridge.getLastTargetMs()))
                except Exception:
                    target = -1
                if target >= 0:
                    self._resume_pos_ms = target
                    try:
                        self.ids.current_time_label.text = self._fmt_ms(target)
                    except Exception:
                        pass
                    try:
                        self.ids.progress_slider.value = float(target) / 1000.0
                    except Exception:
                        pass
                    try:
                        vp = getattr(self, "_video_player", None)
                        audio = getattr(media, "android_player", None)
                        duration = int(audio.getDuration() or 0) if audio is not None else 0
                        if vp is not None:
                            vp.set_native_progress(target, max(1, duration))
                    except Exception:
                        pass

            try:
                self._set_video_controls_visible(True)
            except Exception:
                pass

        def sync_tick(_dt, owner):
            try:
                apply_java_event(owner)
            except ReferenceError:
                return False
            except Exception:
                return True
            return True

        def init_java_transport(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self._pymusic_java_audio_ref = None
            self._pymusic_java_video_ref = None
            try:
                self._pymusic_java_state_version = int(Bridge.getStateVersion())
            except Exception:
                self._pymusic_java_state_version = 0
            old_ev = getattr(self, "_pymusic_java_transport_ev", None)
            if old_ev is not None:
                try:
                    old_ev.cancel()
                except Exception:
                    pass
            self._pymusic_java_transport_ev = Clock.schedule_interval(
                lambda dt, owner=self: sync_tick(dt, owner),
                _SYNC_INTERVAL,
            )
            Clock.schedule_once(lambda _dt: register_players(self), 0)

        def pause_with_java_state(self, *args, **kwargs):
            try:
                Bridge.markPaused()
            except Exception:
                pass
            return old_pause(self, *args, **kwargs)

        def resume_with_java_state(self, *args, **kwargs):
            try:
                Bridge.markPlaying()
            except Exception:
                pass
            return old_resume(self, *args, **kwargs)

        def play_audio_with_java_state(self, *args, **kwargs):
            try:
                Bridge.markPlaying()
            except Exception:
                pass
            result = old_play_audio(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: register_players(self), 0)
            return result

        def stop_audio_with_java_state(self, *args, **kwargs):
            try:
                Bridge.markPaused()
            except Exception:
                pass
            result = old_stop_audio(self, *args, **kwargs)
            try:
                Bridge.setAudioPlayer(None)
                Bridge.setVideoPlayer(None)
                self._pymusic_java_audio_ref = None
                self._pymusic_java_video_ref = None
            except Exception:
                pass
            return result

        def background_tick_java_guard(self, dt):
            # If Java already processed a pause but Python's state mirror has not
            # run yet, never let the 1-second watchdog auto-resume the player.
            try:
                if bool(Bridge.isUserPaused()):
                    self._user_paused = True
                    self._playback_desired = False
            except Exception:
                pass
            return old_background_tick(self, dt)

        screen_cls.__init__ = init_java_transport
        screen_cls._pause_playback = pause_with_java_state
        screen_cls._resume_playback = resume_with_java_state
        screen_cls.play_audio = play_audio_with_java_state
        screen_cls.stop_audio = stop_audio_with_java_state
        screen_cls._background_tick = background_tick_java_guard
        # Kivy Clock's WeakMethod resolves bound callbacks by __name__.  The
        # scheduled method lives under _background_tick, so expose the function
        # name as an alias too; otherwise the first 1s tick can fail lookup.
        screen_cls.background_tick_java_guard = background_tick_java_guard
        screen_cls._pymusic_native_java_transport_v1 = True
        video_cls._pymusic_native_java_transport_v1 = True

        _PATCHED = True
        print("[JAVA-CTRL] direct Java ACTION_DOWN transport v1 enabled")
        return True
    except Exception as exc:
        print("[JAVA-CTRL] patch install failed:", exc)
        return False
