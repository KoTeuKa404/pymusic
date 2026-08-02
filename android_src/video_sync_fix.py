"""Keep the Android video stream synchronized to the audio MediaPlayer."""

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
        if getattr(player_cls, "_pymusic_video_sync_v1", False):
            _PATCHED = True
            return True

        Clock = audio_module.Clock
        media = audio_module.ma

        old_video_seek = video_cls.seek_to

        @video_module.run_on_ui_thread
        def precise_video_seek(self, ms: int):
            """Use Android's closest-frame seek when the API supports it."""
            try:
                target = max(0, int(ms or 0))
                if self.player is None:
                    return
                if not bool(getattr(self, "_prepared", False)):
                    self._pending_start_pos_ms = target
                    return
                try:
                    # API 26+: MediaPlayer.SEEK_CLOSEST. The old one-argument
                    # overload usually lands on an earlier keyframe.
                    self.player.seekTo(target, 3)
                except Exception:
                    self.player.seekTo(target)
            except Exception:
                try:
                    old_video_seek(self, ms)
                except Exception:
                    pass

        video_cls.seek_to = precise_video_seek

        def reset_sync_state(self) -> None:
            self._pymusic_sync_player_id = None
            self._pymusic_sync_hits = 0
            self._pymusic_sync_sign = 0
            self._pymusic_sync_last_seek = 0.0
            self._pymusic_sync_last_log = 0.0

        def sync_video_to_audio(self, _dt=0):
            try:
                vp = getattr(self, "_video_player", None)
                if (
                    vp is None
                    or not bool(getattr(self, "_video_enabled", False))
                    or not bool(getattr(self, "_video_active", False))
                    or bool(getattr(self, "_app_in_background", False))
                    or bool(getattr(self, "_is_scrubbing", False))
                ):
                    self._pymusic_sync_hits = 0
                    return

                audio_player = getattr(media, "android_player", None)
                if (
                    audio_player is None
                    or not media._is_prepared()
                    or getattr(vp, "player", None) is None
                    or not bool(getattr(vp, "_prepared", False))
                ):
                    return

                audio_pos = int(audio_player.getCurrentPosition() or 0)
                video_pos = vp.get_current_position()
                if video_pos is None:
                    return
                video_pos = int(video_pos or 0)

                java_player = getattr(vp, "player", None)
                player_id = id(java_player)
                now = time.monotonic()
                playing = False
                try:
                    playing = bool(audio_player.isPlaying())
                except Exception:
                    playing = bool(getattr(self, "_playback_desired", False))

                drift = audio_pos - video_pos
                distance = abs(drift)

                # A newly prepared video must be aligned immediately. This also
                # fixes the initial delay caused by buffering the video stream.
                if player_id != getattr(self, "_pymusic_sync_player_id", None):
                    self._pymusic_sync_player_id = player_id
                    self._pymusic_sync_hits = 0
                    self._pymusic_sync_sign = 0
                    if distance >= 120:
                        lead = 110 if playing else 0
                        vp.seek_to(audio_pos + lead)
                        self._pymusic_sync_last_seek = now
                    return

                sign = 1 if drift > 0 else (-1 if drift < 0 else 0)
                if distance < 220:
                    self._pymusic_sync_hits = 0
                    self._pymusic_sync_sign = sign
                    return

                if sign == getattr(self, "_pymusic_sync_sign", 0):
                    self._pymusic_sync_hits = int(
                        getattr(self, "_pymusic_sync_hits", 0)
                    ) + 1
                else:
                    self._pymusic_sync_sign = sign
                    self._pymusic_sync_hits = 1

                last_seek = float(
                    getattr(self, "_pymusic_sync_last_seek", 0.0) or 0.0
                )
                hard = distance >= 850
                sustained = (
                    distance >= 300
                    and int(getattr(self, "_pymusic_sync_hits", 0)) >= 2
                )
                paused_mismatch = not playing and distance >= 180
                cooldown = 0.75 if hard else 1.35

                if (
                    (hard or sustained or paused_mismatch)
                    and now - last_seek >= cooldown
                ):
                    # Aim slightly ahead while playing because the seek is
                    # delivered asynchronously on Android's UI thread.
                    lead = 110 if playing else 0
                    target = max(0, audio_pos + lead)
                    vp.seek_to(target)
                    self._pymusic_sync_last_seek = now
                    self._pymusic_sync_hits = 0
                    if (
                        distance >= 500
                        or now
                        - float(
                            getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0
                        )
                        >= 8.0
                    ):
                        print(
                            "[VIDEO] precise sync "
                            f"audio={audio_pos} video={video_pos} "
                            f"drift={drift} target={target}"
                        )
                        self._pymusic_sync_last_log = now
            except Exception as exc:
                now = time.monotonic()
                if (
                    now
                    - float(getattr(self, "_pymusic_sync_last_log", 0.0) or 0.0)
                    >= 10.0
                ):
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
                lambda dt: sync_video_to_audio(self, dt), 0.25
            )

        player_cls.__init__ = init_with_video_sync
        player_cls._pymusic_video_sync_tick = sync_video_to_audio
        player_cls._pymusic_video_sync_v1 = True
        _PATCHED = True
        print("[HOTFIX] precise audio/video sync v1 enabled")
        return True


_patch_video_sync()
