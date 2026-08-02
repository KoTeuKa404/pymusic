"""Start audio and video from one first-frame barrier.

A video MediaPlayer can report the requested position before that frame is
actually visible on SurfaceView. Seeking only the video therefore leaves the
picture roughly one decoder buffer behind, while a manual slider seek appears
perfect because it flushes both players. This patch reproduces that successful
manual operation during startup:

1. pause audio only when video is ready;
2. seek video to the frozen audio position;
3. preroll until Android reports the first rendered video frame;
4. freeze video, seek audio to that exact frame;
5. start both prepared players together.
"""
from __future__ import annotations

import sys
import threading
import time

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_startup_sync_barrier() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        audio_module = sys.modules.get("audio_screen")
        video_module = sys.modules.get("video_player")
        if audio_module is None or video_module is None:
            return False

        screen_cls = getattr(audio_module, "AudioPlayerScreen", None)
        video_cls = getattr(video_module, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False
        if not getattr(screen_cls, "_pymusic_video_sync_v3", False):
            return False
        if getattr(screen_cls, "_pymusic_startup_barrier_v4", False):
            _PATCHED = True
            return True

        media = audio_module.ma

        class VideoSeekListener(video_module.PythonJavaClass):
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
                    self._owner._pymusic_start_video_preroll(
                        mp, self._generation, "video-seek-complete"
                    )
                except Exception:
                    pass

        class FirstFrameInfoListener(video_module.PythonJavaClass):
            __javainterfaces__ = ["android/media/MediaPlayer$OnInfoListener"]
            __javacontext__ = "app"

            def __init__(self, owner, generation):
                super().__init__()
                self._owner = owner
                self._generation = int(generation)

            @video_module.java_method("(Landroid/media/MediaPlayer;II)Z")
            def onInfo(self, mp, what, extra):
                try:
                    if int(what) == 3:  # MEDIA_INFO_VIDEO_RENDERING_START
                        self._owner._pymusic_lock_first_frame(
                            mp, self._generation, "first-frame"
                        )
                except Exception:
                    pass
                return False

        class AudioSeekListener(video_module.PythonJavaClass):
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
                    self._owner._pymusic_release_start_barrier(
                        mp, self._generation, "audio-seek-complete"
                    )
                except Exception:
                    pass

        def generation_valid(self, generation: int, mp=None) -> bool:
            try:
                if int(generation) != int(getattr(self, "_play_gen", -1)):
                    return False
                if self.player is None:
                    return False
                if mp is not None and mp != self.player:
                    return False
                return True
            except Exception:
                return False

        def bound_audio_owner(self):
            try:
                provider = getattr(self, "_start_pos_provider", None)
                owner = getattr(provider, "__self__", None)
                if owner is not None and isinstance(owner, screen_cls):
                    return owner
            except Exception:
                pass
            return None

        def should_play(owner) -> bool:
            try:
                return bool(
                    owner is not None
                    and getattr(owner, "_playback_desired", False)
                    and not getattr(owner, "_user_paused", False)
                    and not getattr(owner, "_app_in_background", False)
                )
            except Exception:
                return False

        def current_audio_position(audio_player, fallback=0) -> int:
            try:
                if audio_player is not None:
                    return max(0, int(audio_player.getCurrentPosition() or 0))
            except Exception:
                pass
            try:
                return max(0, int(fallback or 0))
            except Exception:
                return 0

        def cancel_timer(self, name: str) -> None:
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.cancel()
                except Exception:
                    pass
            try:
                setattr(self, name, None)
            except Exception:
                pass

        def start_timer(self, name: str, delay: float, callback) -> None:
            cancel_timer(self, name)
            timer = threading.Timer(delay, callback)
            timer.daemon = True
            setattr(self, name, timer)
            timer.start()

        def hold_drift_watchdog(owner) -> None:
            if owner is None:
                return
            try:
                if not bool(
                    getattr(owner, "_pymusic_startup_barrier_active", False)
                ):
                    owner._pymusic_barrier_previous_scrubbing = bool(
                        getattr(owner, "_is_scrubbing", False)
                    )
                owner._pymusic_startup_barrier_active = True
                # The regular sync watchdog treats scrubbing as an intentional
                # synchronization hold, so it cannot seek either player while
                # the first-frame barrier owns them.
                owner._is_scrubbing = True
            except Exception:
                pass

        def release_drift_watchdog(owner) -> None:
            if owner is None:
                return
            try:
                owner._is_scrubbing = bool(
                    getattr(owner, "_pymusic_barrier_previous_scrubbing", False)
                )
                owner._pymusic_startup_barrier_active = False
            except Exception:
                pass

        @video_module.run_on_ui_thread
        def start_video_preroll(
            self, mp, generation: int, reason: str = ""
        ) -> None:
            if not generation_valid(self, generation, mp):
                return
            if bool(getattr(self, "_pymusic_preroll_started", False)):
                return
            self._pymusic_preroll_started = True
            cancel_timer(self, "_pymusic_video_seek_timeout")
            try:
                mp.setOnSeekCompleteListener(None)
            except Exception:
                pass
            self._pymusic_video_seek_listener = None

            owner = getattr(self, "_pymusic_barrier_owner", None)
            if not should_play(owner):
                self._pymusic_lock_first_frame(
                    mp, generation, "paused-before-preroll"
                )
                return

            try:
                mp.start()
            except Exception:
                self._pymusic_lock_first_frame(
                    mp, generation, "video-start-exception"
                )
                return

            print(
                "[VIDEO] startup preroll "
                f"reason={reason} target={int(getattr(self, '_pymusic_barrier_target', 0) or 0)}"
            )

            # Some firmware does not emit MEDIA_INFO_VIDEO_RENDERING_START.
            start_timer(
                self,
                "_pymusic_first_frame_timeout",
                1.20,
                lambda: self._pymusic_lock_first_frame(
                    mp, generation, "first-frame-timeout"
                ),
            )

        @video_module.run_on_ui_thread
        def lock_first_frame(
            self, mp, generation: int, reason: str = ""
        ) -> None:
            if not generation_valid(self, generation, mp):
                return
            if bool(getattr(self, "_pymusic_frame_locked", False)):
                return
            self._pymusic_frame_locked = True
            cancel_timer(self, "_pymusic_first_frame_timeout")
            try:
                mp.setOnInfoListener(None)
            except Exception:
                pass
            self._pymusic_first_frame_listener = None

            owner = getattr(self, "_pymusic_barrier_owner", None)
            audio_player = getattr(media, "android_player", None)

            try:
                mp.pause()
            except Exception:
                pass
            try:
                frame_pos = max(0, int(mp.getCurrentPosition() or 0))
            except Exception:
                frame_pos = max(
                    0, int(getattr(self, "_pymusic_barrier_target", 0) or 0)
                )
            self._pymusic_barrier_frame_pos = frame_pos

            if audio_player is None or not should_play(owner):
                self._pymusic_release_start_barrier(
                    audio_player, generation, "no-audio-or-paused"
                )
                return

            try:
                audio_player.pause()
            except Exception:
                pass

            try:
                listener = AudioSeekListener(self, generation)
                self._pymusic_audio_seek_listener = listener
                audio_player.setOnSeekCompleteListener(listener)
                try:
                    audio_player.seekTo(frame_pos, 3)
                except Exception:
                    audio_player.seekTo(frame_pos)
                print(
                    "[VIDEO] first frame locked; aligning audio "
                    f"reason={reason} frame={frame_pos}"
                )
                start_timer(
                    self,
                    "_pymusic_audio_seek_timeout",
                    0.45,
                    lambda: self._pymusic_release_start_barrier(
                        audio_player, generation, "audio-seek-timeout"
                    ),
                )
            except Exception:
                self._pymusic_release_start_barrier(
                    audio_player, generation, "audio-seek-exception"
                )

        @video_module.run_on_ui_thread
        def release_start_barrier(
            self, audio_player, generation: int, reason: str = ""
        ) -> None:
            if not generation_valid(self, generation):
                return
            if bool(getattr(self, "_pymusic_barrier_released", False)):
                return
            self._pymusic_barrier_released = True
            cancel_timer(self, "_pymusic_video_seek_timeout")
            cancel_timer(self, "_pymusic_first_frame_timeout")
            cancel_timer(self, "_pymusic_audio_seek_timeout")

            owner = getattr(self, "_pymusic_barrier_owner", None)
            mp = self.player
            if audio_player is None:
                audio_player = getattr(media, "android_player", None)
            try:
                if audio_player is not None:
                    audio_player.setOnSeekCompleteListener(None)
            except Exception:
                pass
            self._pymusic_audio_seek_listener = None

            play_now = should_play(owner)
            if play_now:
                # Video already has a decoded frame waiting, so starting it
                # first avoids another visual decoder startup delay. Audio is
                # started immediately afterwards on the same Android UI turn.
                try:
                    mp.start()
                except Exception:
                    pass
                try:
                    if audio_player is not None:
                        audio_player.start()
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

            try:
                if owner is not None:
                    now = time.monotonic()
                    owner._pymusic_sync_player_id = id(mp)
                    owner._pymusic_sync_last_seek = now
                    owner._pymusic_sync_settle_until = now + 0.30
                    owner._pymusic_sync_outside_since = 0.0
                    owner._pymusic_sync_bias_ms = 25.0
            except Exception:
                pass
            release_drift_watchdog(owner)

            try:
                video_pos = int(mp.getCurrentPosition() or 0)
            except Exception:
                video_pos = -1
            audio_pos = current_audio_position(audio_player, -1)
            print(
                "[VIDEO] startup barrier released "
                f"reason={reason} audio={audio_pos} video={video_pos} "
                f"diff={audio_pos - video_pos if audio_pos >= 0 and video_pos >= 0 else 'n/a'}"
            )

        def prepared_with_first_frame_barrier(
            self, mp, generation: int
        ) -> None:
            if not generation_valid(self, generation, mp):
                return

            self._prepared = True
            self._surface_ready_to_show = False
            self._pymusic_preroll_started = False
            self._pymusic_frame_locked = False
            self._pymusic_barrier_released = False
            self._pymusic_barrier_owner = bound_audio_owner(self)

            owner = self._pymusic_barrier_owner
            hold_drift_watchdog(owner)
            audio_player = getattr(media, "android_player", None)
            fallback = 0
            try:
                provider = getattr(self, "_start_pos_provider", None)
                if callable(provider):
                    fallback = int(provider() or 0)
            except Exception:
                fallback = 0
            target = current_audio_position(audio_player, fallback)
            self._pymusic_barrier_target = target

            # Freeze the master clock only now, after the video has fully
            # prepared, so network extraction/prepare does not interrupt audio.
            if should_play(owner) and audio_player is not None:
                try:
                    audio_player.pause()
                except Exception:
                    pass
                target = current_audio_position(audio_player, target)
                self._pymusic_barrier_target = target

            try:
                info_listener = FirstFrameInfoListener(self, generation)
                self._pymusic_first_frame_listener = info_listener
                mp.setOnInfoListener(info_listener)
            except Exception:
                self._pymusic_first_frame_listener = None

            if target <= 0:
                self._pymusic_start_video_preroll(
                    mp, generation, "zero-position"
                )
                return

            try:
                seek_listener = VideoSeekListener(self, generation)
                self._pymusic_video_seek_listener = seek_listener
                mp.setOnSeekCompleteListener(seek_listener)
                try:
                    mp.seekTo(target, 3)
                except Exception:
                    mp.seekTo(target)
                print(
                    "[VIDEO] startup barrier waiting for video seek "
                    f"target={target} gen={generation}"
                )
                start_timer(
                    self,
                    "_pymusic_video_seek_timeout",
                    0.75,
                    lambda: self._pymusic_start_video_preroll(
                        mp, generation, "video-seek-timeout"
                    ),
                )
            except Exception:
                self._pymusic_start_video_preroll(
                    mp, generation, "video-seek-exception"
                )

        video_cls._pymusic_start_video_preroll = start_video_preroll
        video_cls._pymusic_lock_first_frame = lock_first_frame
        video_cls._pymusic_release_start_barrier = release_start_barrier
        video_cls._on_prepared = prepared_with_first_frame_barrier

        screen_cls._pymusic_startup_barrier_v4 = True
        _PATCHED = True
        print("[HOTFIX] first-rendered-frame AV startup barrier v4 enabled")
        return True


_patch_startup_sync_barrier()
