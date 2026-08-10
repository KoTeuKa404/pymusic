"""Bind the visible native video controls directly to Java transport actions.

This is deliberately separate from the Python/Kivy touch handlers.  The actual
pause/resume/seek command is executed by NativeTransportController.TouchListener
inside Android's UI thread.  Python only publishes the current MediaPlayer
references and mirrors the resulting state back into the Kivy UI afterward.
"""
from __future__ import annotations

import time

from jnius import autoclass
from kivy.clock import Clock


_PATCHED = False
_SYNC_INTERVAL = 0.10


def install_java_native_transport_bridge() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen
        import media_android as ma

        Controller = autoclass("org.koteuka404.pymusic.NativeTransportController")
        TouchListener = autoclass(
            "org.koteuka404.pymusic.NativeTransportController$TouchListener"
        )

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if screen_cls is None:
            return False
        if bool(getattr(screen_cls, "_pymusic_java_transport_v1", False)):
            _PATCHED = True
            return True

        old_init = screen_cls.__init__

        def init_with_java_transport(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self._java_transport_listeners = []
            self._java_transport_last_audio = None
            self._java_transport_last_video = None
            self._java_transport_last_playing = None
            self._java_transport_last_pos = 0
            self._java_transport_last_ui_ts = 0.0
            self._java_transport_bound_player_id = None

            def sync_native_transport(_dt):
                try:
                    audio = getattr(ma, "android_player", None)
                    if audio is not self._java_transport_last_audio:
                        try:
                            Controller.setAudioPlayer(audio)
                        except Exception:
                            pass
                        self._java_transport_last_audio = audio

                    vp = getattr(self, "_video_player", None)
                    video = getattr(vp, "player", None) if vp is not None else None
                    if video is not self._java_transport_last_video:
                        try:
                            Controller.setVideoPlayer(video)
                        except Exception:
                            pass
                        self._java_transport_last_video = video

                    # Rebind whenever AndroidVideoPlayer creates/recreates its native
                    # ImageButtons.  Child listeners consume the event, so the older
                    # Python listener on the parent cannot trigger a duplicate action.
                    controls = list(
                        getattr(vp, "_controls_touch_views", []) or []
                    ) if vp is not None else []
                    buttons = controls[:3] if len(controls) >= 3 else []
                    player_id = (
                        id(vp),
                        tuple(id(btn) for btn in buttons),
                    ) if buttons else None

                    if buttons and player_id != self._java_transport_bound_player_id:
                        listeners = [
                            TouchListener(Controller.ACTION_REWIND),
                            TouchListener(Controller.ACTION_TOGGLE),
                            TouchListener(Controller.ACTION_FORWARD),
                        ]
                        for button, listener in zip(buttons, listeners):
                            button.setClickable(True)
                            button.setOnTouchListener(listener)
                        # Keep Python references alive; otherwise PyJNIus can release
                        # wrappers while Java still holds the listener objects.
                        self._java_transport_listeners = listeners
                        self._java_transport_bound_player_id = player_id
                        print("[JAVA-TRANSPORT] native button listeners bound")

                    # Mirror Java-side state into Python. This is intentionally not
                    # part of the touch path, so even a slow Kivy frame cannot delay
                    # the actual MediaPlayer command.
                    prepared = bool(audio is not None and ma._is_prepared())
                    if prepared:
                        try:
                            playing = bool(Controller.isAudioPlaying())
                        except Exception:
                            playing = bool(audio.isPlaying())
                        try:
                            pos = int(Controller.getAudioPosition() or 0)
                        except Exception:
                            pos = int(audio.getCurrentPosition() or 0)

                        now = time.monotonic()
                        previous_playing = self._java_transport_last_playing
                        if previous_playing is None or playing != previous_playing:
                            self._java_transport_last_playing = playing
                            self._user_paused = not playing
                            self._playback_desired = playing
                            self._resume_pos_ms = int(pos)
                            try:
                                self._ui_set_playing(playing)
                            except Exception:
                                pass

                        # Keep the Kivy progress display responsive after native seek,
                        # but avoid rebuilding notifications every 100 ms.
                        if abs(int(pos) - int(self._java_transport_last_pos or 0)) >= 1500:
                            self._java_transport_last_pos = int(pos)
                            try:
                                self.ids.current_time_label.text = self._fmt_ms(pos)
                                self.ids.progress_slider.value = float(pos) / 1000.0
                            except Exception:
                                pass

                        if now - float(self._java_transport_last_ui_ts or 0.0) > 0.5:
                            self._java_transport_last_ui_ts = now
                            try:
                                if vp is not None and hasattr(vp, "set_native_playing"):
                                    vp.set_native_playing(playing)
                            except Exception:
                                pass
                except Exception as exc:
                    try:
                        print("[JAVA-TRANSPORT] sync failed:", exc)
                    except Exception:
                        pass

            self._java_transport_sync_ev = Clock.schedule_interval(
                sync_native_transport, _SYNC_INTERVAL
            )
            Clock.schedule_once(sync_native_transport, 0)

        screen_cls.__init__ = init_with_java_transport
        screen_cls._pymusic_java_transport_v1 = True
        _PATCHED = True
        print("[JAVA-TRANSPORT] zero-Python touch path v1 enabled")
        return True
    except Exception as exc:
        print("[JAVA-TRANSPORT] install failed:", exc)
        return False
