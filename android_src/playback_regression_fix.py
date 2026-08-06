"""Regression fixes for fast audio startup and screen-off playback.

The muxed-video synchronizer must never compete with the audio critical path.
The hidden audio MediaPlayer also remains the continuous background clock, so
switching the display off only unmutes it instead of seeking/restarting it.
"""
from __future__ import annotations

import threading
import time

_INSTALLED = False
_LOCK = threading.RLock()


def install_playback_regression_fix() -> bool:
    global _INSTALLED

    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
            import ytdlp_helpers as ydlh
        except Exception as exc:
            print("[PLAYBACK-V5] import failed:", exc)
            return False

        player_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if player_cls is None:
            return False
        if getattr(player_cls, "_pymusic_playback_regression_v5", False):
            _INSTALLED = True
            return True

        Clock = audio_screen.Clock
        media = audio_screen.ma
        old_auto_video = player_cls._auto_video_for_current
        old_set_video_mode = player_cls._set_video_mode
        old_background_tick = player_cls._background_tick

        def set_audio_volume(audio, value: float) -> None:
            try:
                media._mp_set_volume(float(value))
                return
            except Exception:
                pass
            try:
                if audio is not None:
                    audio.setVolume(float(value), float(value))
            except Exception:
                pass

        # --------------------------------------------------------------
        # 1. Audio is the critical path.  Start muxed-video extraction only
        # after MediaPlayer has actually started producing audio.  yt-dlp can
        # otherwise hold the Python GIL/network for several seconds and make a
        # cached/fast audio start feel slow.
        # --------------------------------------------------------------
        def audio_first_auto_video(self, gen: int, sync_start: bool = False):
            request_id = int(
                getattr(self, "_pymusic_video_start_request", 0) or 0
            ) + 1
            self._pymusic_video_start_request = request_id

            def worker():
                deadline = time.monotonic() + (0.9 if not sync_start else 0.35)
                while time.monotonic() < deadline:
                    if request_id != int(
                        getattr(self, "_pymusic_video_start_request", -1)
                    ):
                        return
                    if int(getattr(self, "_load_gen", -1)) != int(gen):
                        return
                    if getattr(self, "_app_in_background", False):
                        return
                    if not getattr(self, "_playback_desired", False):
                        return
                    if getattr(self, "_user_paused", False):
                        return
                    try:
                        audio = getattr(media, "android_player", None)
                        if audio is not None and bool(audio.isPlaying()):
                            break
                    except Exception:
                        pass
                    time.sleep(0.025)

                if request_id != int(
                    getattr(self, "_pymusic_video_start_request", -1)
                ):
                    return
                if int(getattr(self, "_load_gen", -1)) != int(gen):
                    return
                old_auto_video(self, gen, sync_start=sync_start)

            # Some resume paths can call this method from Kivy's UI thread.
            # Never block that thread while waiting for the audio player.
            if threading.current_thread() is threading.main_thread():
                threading.Thread(target=worker, daemon=True).start()
                return None
            return worker()

        # --------------------------------------------------------------
        # 2. Restore audio-only prefetch.  V4 also prefetched a full muxed
        # video with yt-dlp; that expensive extraction competed with the next
        # track's audio extraction.  Keep only the lightweight audio URL cache
        # and start it shortly after playback is already audible.
        # --------------------------------------------------------------
        def audio_only_prefetch(self):
            try:
                playlist = getattr(self, "playlist", None)
                tracks = getattr(playlist, "tracks", None) if playlist else None
                if not tracks or len(tracks) < 2:
                    return None
                current_index = int(getattr(playlist, "index", 0) or 0)
                next_index = (current_index + 1) % len(tracks)
                next_item = tracks[next_index] or {}
                next_url = str(next_item.get("url") or "")
                current_url = str(getattr(self, "_last_video_url", "") or "")
                if not next_url or next_url == current_url:
                    return None
                if next_url in getattr(self, "_prefetch_inflight", set()):
                    return None
                cached = getattr(self, "_url_cache", {}).get(next_url)
                if cached and cached.get("audio_url"):
                    return None
                expected_gen = int(getattr(self, "_load_gen", 0) or 0)
            except Exception:
                return None

            def launch(_dt=0):
                if int(getattr(self, "_load_gen", -1)) != expected_gen:
                    return
                if str(getattr(self, "_last_video_url", "") or "") != current_url:
                    return
                inflight = getattr(self, "_prefetch_inflight", None)
                if inflight is None or next_url in inflight:
                    return
                cached_now = getattr(self, "_url_cache", {}).get(next_url)
                if cached_now and cached_now.get("audio_url"):
                    return
                inflight.add(next_url)

                def job():
                    try:
                        info = ydlh.extract_audio_info(next_url)
                        audio_url = info.get("audio_url") or ""
                        headers = info.get("http_headers") or {}
                        expire_ts = info.get("expire_ts")
                        if audio_url:
                            self._put_cache(
                                next_url, audio_url, headers, expire_ts
                            )
                            self._cache_audio_async(
                                next_url, audio_url, headers
                            )
                            print("[PLAYBACK-V5] next audio prefetched")
                    except Exception as exc:
                        print("[PLAYBACK-V5] audio prefetch failed:", exc)
                    finally:
                        try:
                            inflight.discard(next_url)
                        except Exception:
                            pass

                threading.Thread(target=job, daemon=True).start()

            Clock.schedule_once(launch, 1.25)
            return None

        # --------------------------------------------------------------
        # 3. Seamless foreground-video -> background-audio handoff.
        # The shadow audio player is already running.  Unmute it first and
        # invalidate the muxed handoff flags before delegating to V4's UI hide.
        # This deliberately avoids seekTo(), which was pausing/restarting audio
        # and could make the elapsed time jump or start counting again.
        # --------------------------------------------------------------
        def seamless_set_video_mode(self, video_on: bool):
            if video_on:
                return old_set_video_mode(self, True)

            was_master = bool(
                getattr(self, "_pymusic_muxed_video_master", False)
            )
            was_pending = bool(
                getattr(self, "_pymusic_muxed_handoff_pending", False)
            )

            if was_master or was_pending:
                self._pymusic_muxed_sync_gen = int(
                    getattr(self, "_pymusic_muxed_sync_gen", 0) or 0
                ) + 1
                self._pymusic_muxed_handoff_pending = False

                audio = getattr(media, "android_player", None)
                vp = getattr(self, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp else None

                try:
                    audio_pos = int(audio.getCurrentPosition() or 0) if audio else 0
                except Exception:
                    audio_pos = 0
                try:
                    video_pos = (
                        int(native_video.getCurrentPosition() or 0)
                        if native_video is not None
                        else 0
                    )
                except Exception:
                    video_pos = 0

                # Make the already-running shadow audio audible before the
                # SurfaceView/video player is hidden or stopped.
                set_audio_volume(audio, 1.0)
                try:
                    if (
                        audio is not None
                        and getattr(self, "_playback_desired", False)
                        and not getattr(self, "_user_paused", False)
                        and not bool(audio.isPlaying())
                    ):
                        audio.start()
                except Exception:
                    try:
                        if (
                            getattr(self, "_playback_desired", False)
                            and not getattr(self, "_user_paused", False)
                        ):
                            media._mp_start()
                    except Exception:
                        pass

                # Preserve the continuous audio clock.  Do not overwrite it
                # with the video position during a screen-off transition.
                if audio_pos > 0:
                    self._resume_pos_ms = audio_pos
                elif video_pos > 0:
                    self._resume_pos_ms = video_pos

                try:
                    if native_video is not None:
                        native_video.setVolume(0.0, 0.0)
                except Exception:
                    pass

                # Clear these before calling the V4 wrapper.  V4 will then only
                # hide the video UI and will skip its old seek/restart handoff.
                self._pymusic_muxed_video_master = False
                self._pymusic_muxed_handoff_pending = False
                print(
                    "[PLAYBACK-V5] seamless audio handoff "
                    f"audio={audio_pos} video={video_pos} "
                    f"drift={video_pos - audio_pos}"
                )

            return old_set_video_mode(self, False)

        # Keep the muted shadow clock close while video is visible.  Any seek
        # happens while the audio player is inaudible, never during screen-off.
        def background_tick_with_shadow_sync(self, dt):
            try:
                if (
                    getattr(self, "_pymusic_muxed_video_master", False)
                    and not getattr(self, "_app_in_background", False)
                ):
                    audio = getattr(media, "android_player", None)
                    vp = getattr(self, "_video_player", None)
                    native_video = getattr(vp, "player", None) if vp else None
                    if audio is not None and native_video is not None:
                        audio_pos = int(audio.getCurrentPosition() or 0)
                        video_pos = int(native_video.getCurrentPosition() or 0)
                        drift = video_pos - audio_pos
                        now = time.monotonic()
                        last_sync = float(
                            getattr(self, "_pymusic_shadow_sync_ts", 0.0)
                            or 0.0
                        )
                        if abs(drift) > 650 and (now - last_sync) > 2.0:
                            self._pymusic_shadow_sync_ts = now
                            try:
                                media._mp_seek_to(video_pos)
                                print(
                                    "[PLAYBACK-V5] shadow clock corrected "
                                    f"drift={drift}"
                                )
                            except Exception:
                                pass
            except Exception:
                pass
            return old_background_tick(self, dt)

        player_cls._auto_video_for_current = audio_first_auto_video
        player_cls._prefetch_next_track_audio = audio_only_prefetch
        player_cls._set_video_mode = seamless_set_video_mode
        player_cls._background_tick = background_tick_with_shadow_sync
        player_cls._pymusic_playback_regression_v5 = True

        _INSTALLED = True
        print("[PLAYBACK-V5] fast audio and screen-off fixes installed")
        return True
