"""Keep the muted Android video stream tightly synchronized to audio.

Audio and video use separate Android MediaPlayer instances, so they do not
share a hardware clock. This patch treats audio as the master clock, samples
both players at 20 Hz, uses small playback-speed corrections inside the
100 ms window, and performs a closest-frame seek as soon as drift exceeds it.
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
        if getattr(player_cls, "_pymusic_video_sync_v2", False):
            _PATCHED = True
            return True

        Clock = audio_module.Clock
        media = audio_module.ma
        old_video_seek = video_cls.seek_to
        old_video_prepared = video_cls._on_prepared

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
            """Nudge the muted video clock without producing visible jumps."""
            try:
                value = max(0.90, min(1.10, float(speed or 1.0)))
                previous = float(getattr(self, "_pymusic_sync_speed", 1.0) or 1.0)
                if abs(previous - value) < 0.002:
                    return
                if self.player is None or not bool(getattr(self, "_prepared", False)):
                    return
                try:
                    params = self.player.getPlaybackParams()
                    params.setSpeed(value)
                    self.player.setPlaybackParams(params)
                    self._pymusic_sync_speed = value
                except Exception:
                    # PlaybackParams is unavailable on some API 21/22 devices.
                    self._pymusic_sync_speed = 1.0
            except Exception:
                pass

        video_cls.seek_to = precise_video_seek
        video_cls._pymusic_set_sync_speed = set_video_speed

        def aligned_on_prepared(video_self, mp, gen: int):
            """Run the normal prepare path, then immediately re-read audio."""
            old_video_prepared(video_self, mp, gen)
            try:
                if gen != getattr(video_self, "_play_gen", -1):
                    return
                provider = getattr(video_self, "_start_pos_provider", None)
                if not callable(provider):
                    return

                def align(_dt=0):
                    try:
                        if gen != getattr(video_self, "_play_gen", -1):
                            return
                        target = max(0, int(provider() or 0) + 35)
                        video_self.seek_to(target)
                    except Exception:
                        pass

                # The first correction removes buffering delay. The second runs
                # after the asynchronous seek has displayed its first frame.
                Clock.schedule_once(align, 0)
                Clock.schedule_once(align, 0.08)
            except Exception:
                pass

        video_cls._on_prepared = aligned_on_prepared

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
            self._pymusic_sync_bias_ms = 35.0
            self._pymusic_sync_speed = 1.0

        def seek_to_audio(self, vp, audio_pos: int, playing: bool, now: float, reason: str):
            bias = float(getattr(self, "_pymusic_sync_bias_ms", 35.0) or 35.0)
            target = max(0, int(audio_pos + (bias if playing else 0)))
            set_speed(self, 1.0)
            vp.seek_to(target)
            self._pymusic_sync_last_seek = now
            self._pymusic_sync_settle_until = now + 0.11
            self._pymusic_sync_outside_since = 0.0
            if now - float(getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0) >= 2.0:
                print(f"[VIDEO] sub-100ms sync seek reason={reason} target={target}")
                self._pymusic_sync_last_log = now

        def sync_video_to_audio(self, _dt=0):
            try:
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

                # Compensate for the few milliseconds spent reading the two
                # independent Java players one after another.
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
                    playing = bool(getattr(self, "_playback_desired", False))

                if playing:
                    audio_sample_time = (sample_start + sample_mid) * 0.5
                    audio_pos += int(max(0.0, sample_end - audio_sample_time) * 1000.0)

                now = sample_end
                drift = int(audio_pos - video_pos)
                distance = abs(drift)
                player_id = id(getattr(vp, "player", None))

                if player_id != getattr(self, "_pymusic_sync_player_id", None):
                    self._pymusic_sync_player_id = player_id
                    self._pymusic_sync_bias_ms = 35.0
                    if distance > 45:
                        seek_to_audio(self, vp, audio_pos, playing, now, "new-player")
                    else:
                        set_speed(self, 1.0)
                    return

                if now < float(getattr(self, "_pymusic_sync_settle_until", 0.0) or 0.0):
                    return

                # When paused, both positions should remain essentially equal.
                if not playing:
                    set_speed(self, 1.0)
                    if distance > 55 and now - float(getattr(self, "_pymusic_sync_last_seek", 0.0) or 0.0) >= 0.14:
                        seek_to_audio(self, vp, audio_pos, False, now, "paused")
                    return

                # ±35 ms is effectively frame-accurate on typical 24/30 fps
                # content. Do not touch the decoder inside this dead band.
                if distance <= 35:
                    set_speed(self, 1.0)
                    self._pymusic_sync_outside_since = 0.0
                    return

                # Keep all normal drift inside 100 ms without visible seeking.
                # Positive drift means video is behind and must run faster.
                if distance < 100:
                    correction = max(-0.08, min(0.08, drift / 1100.0))
                    set_speed(self, 1.0 + correction)
                    self._pymusic_sync_outside_since = 0.0
                    return

                outside_since = float(
                    getattr(self, "_pymusic_sync_outside_since", 0.0) or 0.0
                )
                if outside_since <= 0.0:
                    self._pymusic_sync_outside_since = now
                    outside_since = now

                # One 50 ms sample filters position-reporting jitter. Large
                # errors, however, are corrected immediately.
                hard = distance >= 180
                persisted = now - outside_since >= 0.045
                last_seek = float(getattr(self, "_pymusic_sync_last_seek", 0.0) or 0.0)
                if (hard or persisted) and now - last_seek >= 0.20:
                    # Learn a small device-specific command-latency bias. This
                    # converges slowly and is clamped to a safe range.
                    bias = float(getattr(self, "_pymusic_sync_bias_ms", 35.0) or 35.0)
                    bias += max(-6.0, min(6.0, drift * 0.04))
                    self._pymusic_sync_bias_ms = max(10.0, min(85.0, bias))
                    seek_to_audio(self, vp, audio_pos, True, now, "drift")
                else:
                    # Start catching up while waiting for the confirming sample.
                    set_speed(self, 1.08 if drift > 0 else 0.92)

                if distance >= 100 and now - float(getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0) >= 2.0:
                    print(
                        "[VIDEO] drift outside target "
                        f"audio={audio_pos} video={video_pos} drift={drift}ms"
                    )
                    self._pymusic_sync_last_log = now
            except Exception as exc:
                now = time.monotonic()
                if now - float(getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0) >= 8.0:
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
            # 20 Hz: a new correction decision every 50 ms.
            self._pymusic_video_sync_ev = Clock.schedule_interval(
                lambda dt: sync_video_to_audio(self, dt), 0.05
            )

        player_cls.__init__ = init_with_video_sync
        player_cls._pymusic_video_sync_tick = sync_video_to_audio
        player_cls._pymusic_video_sync_v2 = True
        _PATCHED = True
        print("[HOTFIX] sub-100ms adaptive audio/video sync v2 enabled")
        return True


_patch_video_sync()
