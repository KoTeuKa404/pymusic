"""Keep the muted Android video stream tightly synchronized to audio.

The key startup fix is to wait for MediaPlayer.OnSeekComplete before starting
video playback. Starting immediately after seekTo() lets Android begin from the
old decoder position for roughly a second; a later manual seek then appears to
"magically" fix sync. Audio remains the master clock.
"""
from __future__ import annotations

import sys
import threading
import time

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_video_sync() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        audio_module = sys.modules.get("audio_screen")
        video_module = sys.modules.get("video_player")
        if audio_module is None or video_module is None:
            return False

        player_cls = getattr(audio_module, "AudioPlayerScreen", None)
        video_cls = getattr(video_module, "AndroidVideoPlayer", None)
        if player_cls is None or video_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_hotfix_v4", False):
            return False
        if getattr(player_cls, "_pymusic_video_sync_v3", False):
            _PATCHED = True
            return True

        Clock = audio_module.Clock
        media = audio_module.ma
        old_video_seek = video_cls.seek_to

        class InitialSeekCompleteListener(video_module.PythonJavaClass):
            __javainterfaces__ = [
                "android/media/MediaPlayer$OnSeekCompleteListener"
            ]
            __javacontext__ = "app"

            def __init__(self, owner, generation):
                super().__init__()
                self._owner = owner
                self._generation = int(generation)

            @video_module.java_method("(Landroid/media/MediaPlayer;)V")
            def onSeekComplete(self, mp):
                try:
                    self._owner._pymusic_finish_initial_seek(
                        mp, self._generation, "seek-complete"
                    )
                except Exception:
                    pass

        @video_module.run_on_ui_thread
        def precise_video_seek(self, ms: int):
            """Seek to the closest decoded frame instead of an old keyframe."""
            try:
                target = max(0, int(ms or 0))
                if self.player is None:
                    return
                if not bool(getattr(self, "_prepared", False)):
                    self._pending_start_pos_ms = target
                    return
                try:
                    # Android API 26+: MediaPlayer.SEEK_CLOSEST == 3.
                    self.player.seekTo(target, 3)
                except Exception:
                    self.player.seekTo(target)
            except Exception:
                try:
                    old_video_seek(self, ms)
                except Exception:
                    pass

        @video_module.run_on_ui_thread
        def set_video_speed(self, speed: float):
            try:
                value = max(0.90, min(1.10, float(speed or 1.0)))
                previous = float(
                    getattr(self, "_pymusic_sync_speed", 1.0) or 1.0
                )
                if abs(previous - value) < 0.002:
                    return
                if self.player is None or not bool(
                    getattr(self, "_prepared", False)
                ):
                    return
                try:
                    params = self.player.getPlaybackParams()
                    params.setSpeed(value)
                    self.player.setPlaybackParams(params)
                    self._pymusic_sync_speed = value
                except Exception:
                    self._pymusic_sync_speed = 1.0
            except Exception:
                pass

        def schedule_visual_realign(video_self, generation: int) -> None:
            """Repeat the same operation that makes manual seeking sync perfectly."""

            def align_after(delay: float) -> None:
                def run() -> None:
                    try:
                        if generation != int(
                            getattr(video_self, "_play_gen", -1)
                        ):
                            return
                        if video_self.player is None or not bool(
                            getattr(video_self, "_prepared", False)
                        ):
                            return
                        provider = getattr(
                            video_self, "_start_pos_provider", None
                        )
                        if not callable(provider):
                            return
                        target = max(0, int(provider() or 0) + 45)
                        video_self.seek_to(target)
                        print(
                            "[VIDEO] post-start visual realign "
                            f"delay={delay:.2f}s target={target}"
                        )
                    except Exception:
                        pass

                timer = threading.Timer(delay, run)
                timer.daemon = True
                timer.start()

            # The first flush removes initial decoder latency. The second one
            # catches devices that buffer a larger first GOP before rendering.
            align_after(0.22)
            align_after(0.85)

        @video_module.run_on_ui_thread
        def finish_initial_seek(self, mp, generation: int, reason: str = ""):
            try:
                generation = int(generation)
                if generation != int(getattr(self, "_play_gen", -1)):
                    return
                if self.player is None or mp is None or mp != self.player:
                    return
                if int(
                    getattr(self, "_pymusic_initial_started_gen", -1)
                ) == generation:
                    return
                self._pymusic_initial_started_gen = generation

                try:
                    mp.setOnSeekCompleteListener(None)
                except Exception:
                    pass
                self._pymusic_initial_seek_listener = None

                if not bool(getattr(self, "_start_paused", False)):
                    try:
                        mp.start()
                    except Exception:
                        pass

                try:
                    self._surface_ready_to_show = True
                    self._apply_surface_bounds()
                except Exception:
                    pass
                try:
                    if self.surface_view is not None:
                        self.surface_view.setVisibility(video_module.View.VISIBLE)
                except Exception:
                    pass
                try:
                    callback = getattr(self, "_on_prepared_cb", None)
                    if callable(callback):
                        callback()
                except Exception:
                    pass

                print(
                    "[VIDEO] initial seek finished before start "
                    f"reason={reason} gen={generation}"
                )
                if not bool(getattr(self, "_start_paused", False)):
                    schedule_visual_realign(self, generation)
            except Exception as exc:
                print("[VIDEO] initial seek finish failed:", exc)

        def prepared_seek_then_start(self, mp, generation: int):
            """Do not start until Android reports that the initial seek landed."""
            if generation != int(getattr(self, "_play_gen", -1)):
                return
            if self.player is None or mp is None:
                return

            self._prepared = True
            self._pymusic_initial_started_gen = -1
            position = None
            try:
                provider = getattr(self, "_start_pos_provider", None)
                if callable(provider):
                    position = provider()
            except Exception:
                position = None
            if position is None:
                position = getattr(self, "_pending_start_pos_ms", None)

            try:
                target = max(0, int(position or 0))
            except Exception:
                target = 0
            if not bool(getattr(self, "_start_paused", False)):
                target += 45

            if target <= 0:
                self._pymusic_finish_initial_seek(
                    mp, generation, "zero-position"
                )
                return

            try:
                listener = InitialSeekCompleteListener(self, generation)
                self._pymusic_initial_seek_listener = listener
                mp.setOnSeekCompleteListener(listener)
                try:
                    mp.seekTo(target, 3)
                except Exception:
                    mp.seekTo(target)
                print(
                    "[VIDEO] waiting for initial seek before start "
                    f"target={target} gen={generation}"
                )

                # Firmware fallback: some devices fail to emit OnSeekComplete.
                def timeout_start() -> None:
                    try:
                        self._pymusic_finish_initial_seek(
                            mp, generation, "seek-timeout"
                        )
                    except Exception:
                        pass

                timer = threading.Timer(0.70, timeout_start)
                timer.daemon = True
                timer.start()
            except Exception:
                self._pymusic_finish_initial_seek(
                    mp, generation, "seek-exception"
                )

        video_cls.seek_to = precise_video_seek
        video_cls._pymusic_set_sync_speed = set_video_speed
        video_cls._pymusic_finish_initial_seek = finish_initial_seek
        video_cls._on_prepared = prepared_seek_then_start

        def set_speed(self, value: float) -> None:
            try:
                vp = getattr(self, "_video_player", None)
                if vp is not None:
                    vp._pymusic_set_sync_speed(value)
            except Exception:
                pass

        def reset_sync_state(self) -> None:
            self._pymusic_sync_player_id = None
            self._pymusic_sync_last_seek = 0.0
            self._pymusic_sync_settle_until = 0.0
            self._pymusic_sync_last_log = 0.0
            self._pymusic_sync_outside_since = 0.0
            self._pymusic_sync_bias_ms = 45.0
            self._pymusic_sync_speed = 1.0
            self._pymusic_sync_last_tick = time.monotonic()

        def seek_to_audio(
            self, vp, audio_pos: int, playing: bool, now: float, reason: str
        ) -> None:
            bias = float(
                getattr(self, "_pymusic_sync_bias_ms", 45.0) or 45.0
            )
            target = max(0, int(audio_pos + (bias if playing else 0)))
            set_speed(self, 1.0)
            vp.seek_to(target)
            self._pymusic_sync_last_seek = now
            self._pymusic_sync_settle_until = now + 0.10
            self._pymusic_sync_outside_since = 0.0
            if now - float(
                getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0
            ) >= 1.5:
                print(
                    "[VIDEO] hard sync "
                    f"reason={reason} audio={audio_pos} target={target}"
                )
                self._pymusic_sync_last_log = now

        def sync_video_to_audio(self, _dt=0):
            try:
                now = time.monotonic()
                previous_tick = float(
                    getattr(self, "_pymusic_sync_last_tick", now) or now
                )
                self._pymusic_sync_last_tick = now
                tick_gap = max(0.0, now - previous_tick)

                vp = getattr(self, "_video_player", None)
                inactive = (
                    vp is None
                    or not bool(getattr(self, "_video_enabled", False))
                    or not bool(getattr(self, "_video_active", False))
                    or bool(getattr(self, "_app_in_background", False))
                    or bool(getattr(self, "_is_scrubbing", False))
                )
                if inactive:
                    set_speed(self, 1.0)
                    self._pymusic_sync_outside_since = 0.0
                    return

                audio_player = getattr(media, "android_player", None)
                if (
                    audio_player is None
                    or not media._is_prepared()
                    or getattr(vp, "player", None) is None
                    or not bool(getattr(vp, "_prepared", False))
                ):
                    return

                sample_start = time.monotonic()
                audio_pos = int(audio_player.getCurrentPosition() or 0)
                sample_mid = time.monotonic()
                video_pos = vp.get_current_position()
                sample_end = time.monotonic()
                if video_pos is None:
                    return
                video_pos = int(video_pos or 0)

                try:
                    playing = bool(audio_player.isPlaying())
                except Exception:
                    playing = bool(
                        getattr(self, "_playback_desired", False)
                    )

                if playing:
                    audio_sample_time = (sample_start + sample_mid) * 0.5
                    audio_pos += int(
                        max(0.0, sample_end - audio_sample_time) * 1000.0
                    )

                now = sample_end
                drift = int(audio_pos - video_pos)
                distance = abs(drift)
                player_id = id(getattr(vp, "player", None))

                if player_id != getattr(
                    self, "_pymusic_sync_player_id", None
                ):
                    self._pymusic_sync_player_id = player_id
                    self._pymusic_sync_bias_ms = 45.0
                    if distance > 30:
                        seek_to_audio(
                            self, vp, audio_pos, playing, now, "new-player"
                        )
                    return

                if now < float(
                    getattr(self, "_pymusic_sync_settle_until", 0.0) or 0.0
                ):
                    return

                # A long Python/UI stall can leave the visible decoder frame
                # behind even when MediaPlayer reports a plausible position.
                # Flush it exactly like the user's successful manual seek.
                if playing and tick_gap >= 0.22:
                    last_seek = float(
                        getattr(self, "_pymusic_sync_last_seek", 0.0) or 0.0
                    )
                    if now - last_seek >= 0.18:
                        seek_to_audio(
                            self, vp, audio_pos, True, now, "runtime-stall"
                        )
                        return

                if not playing:
                    set_speed(self, 1.0)
                    if distance > 45:
                        seek_to_audio(
                            self, vp, audio_pos, False, now, "paused"
                        )
                    return

                if distance <= 30:
                    set_speed(self, 1.0)
                    self._pymusic_sync_outside_since = 0.0
                    return

                if distance < 90:
                    correction = max(
                        -0.09, min(0.09, drift / 800.0)
                    )
                    set_speed(self, 1.0 + correction)
                    self._pymusic_sync_outside_since = 0.0
                    return

                last_seek = float(
                    getattr(self, "_pymusic_sync_last_seek", 0.0) or 0.0
                )
                if now - last_seek >= 0.16:
                    bias = float(
                        getattr(self, "_pymusic_sync_bias_ms", 45.0) or 45.0
                    )
                    bias += max(-5.0, min(5.0, drift * 0.025))
                    self._pymusic_sync_bias_ms = max(
                        15.0, min(95.0, bias)
                    )
                    seek_to_audio(
                        self, vp, audio_pos, True, now, "position-drift"
                    )
            except Exception as exc:
                now = time.monotonic()
                if now - float(
                    getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0
                ) >= 8.0:
                    print("[VIDEO] sync tick failed:", exc)
                    self._pymusic_sync_last_log = now

        old_init = player_cls.__init__

        def init_with_video_sync(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            reset_sync_state(self)
            old_event = getattr(self, "_pymusic_video_sync_ev", None)
            if old_event is not None:
                try:
                    old_event.cancel()
                except Exception:
                    pass
            self._pymusic_video_sync_ev = Clock.schedule_interval(
                lambda dt: sync_video_to_audio(self, dt), 0.04
            )

        player_cls.__init__ = init_with_video_sync
        player_cls._pymusic_video_sync_tick = sync_video_to_audio
        player_cls._pymusic_video_sync_v2 = True
        player_cls._pymusic_video_sync_v3 = True
        _PATCHED = True
        print("[HOTFIX] seek-complete startup AV sync v3 enabled")
        return True


_patch_video_sync()
