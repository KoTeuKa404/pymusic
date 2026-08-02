"""Core player fix installed synchronously from search_utils.

When a progressive YouTube format contains both audio and video, that single
Android MediaPlayer becomes the audible foreground source. The old audio-only
MediaPlayer keeps running muted for notifications/progress/background handoff.
This removes the impossible-to-perfectly-sync pair of independent audible/video
clocks while the video is visible.
"""
from __future__ import annotations

import threading

_INSTALLED = False
_LOCK = threading.RLock()
_MUXED_URLS: set[str] = set()


def install_core_player_fix() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
            import video_player
            import ytdlp_helpers as ydlh
            from jnius import PythonJavaClass, java_method
        except Exception as exc:
            print("[CORE-V2] import failed:", exc)
            return False

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False
        if getattr(screen_cls, "_pymusic_core_av_master_v2", False):
            _INSTALLED = True
            return True

        Clock = audio_screen.Clock
        media = audio_screen.ma
        old_safe_video = ydlh.safe_extract_video_info
        old_set_video_mode = screen_cls._set_video_mode

        class _MuxedSeekCompleteListener(PythonJavaClass):
            __javainterfaces__ = ["android/media/MediaPlayer$OnSeekCompleteListener"]
            __javacontext__ = "app"

            def __init__(self, callback):
                super().__init__()
                self._callback = callback

            @java_method("(Landroid/media/MediaPlayer;)V")
            def onSeekComplete(self, _mp):
                try:
                    self._callback()
                except Exception as exc:
                    print("[CORE-V2] seek callback failed:", exc)

        def extract_muxed_video(video_url: str):
            """Prefer a progressive format whose one URL contains audio+video."""
            opts = {
                "quiet": True,
                "skip_download": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "logger": ydlh.YDLLogger(),
                "format": (
                    "best[ext=mp4][vcodec!=none][acodec!=none][height<=720]/"
                    "best[vcodec!=none][acodec!=none][height<=720]/"
                    "best[vcodec!=none][acodec!=none]"
                ),
                "http_headers": {
                    "User-Agent": getattr(
                        ydlh,
                        "_ANDROID_YT_UA",
                        "com.google.android.youtube/19.20.0 (Linux; U; Android 12) gzip",
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.youtube.com",
                    "Connection": "keep-alive",
                },
                "extractor_retries": 2,
                "ignoreerrors": "only_download",
            }
            try:
                extract = getattr(ydlh, "_extract_info_with_clients", None)
                if not callable(extract):
                    raise RuntimeError("yt-dlp client helper missing")
                info, err = extract(video_url, opts, ("android",))
                if not info:
                    raise RuntimeError(repr(err))

                chosen = info
                url = str(chosen.get("url") or "")
                acodec = str(chosen.get("acodec") or "none")
                vcodec = str(chosen.get("vcodec") or "none")

                if not url or acodec == "none" or vcodec == "none":
                    candidates = []
                    for fmt in info.get("formats") or []:
                        if not isinstance(fmt, dict):
                            continue
                        furl = str(fmt.get("url") or "")
                        if not furl:
                            continue
                        if str(fmt.get("acodec") or "none") == "none":
                            continue
                        if str(fmt.get("vcodec") or "none") == "none":
                            continue
                        height = int(fmt.get("height") or 0)
                        ext = str(fmt.get("ext") or "")
                        protocol = str(fmt.get("protocol") or "")
                        score = (
                            1 if ext == "mp4" else 0,
                            1 if protocol in {"https", "http"} else 0,
                            1 if 0 < height <= 720 else 0,
                            min(height or 0, 720),
                            float(fmt.get("tbr") or 0),
                        )
                        candidates.append((score, fmt))
                    if not candidates:
                        raise RuntimeError("no progressive audio+video format")
                    candidates.sort(key=lambda item: item[0], reverse=True)
                    chosen = candidates[0][1]
                    url = str(chosen.get("url") or "")

                headers_fn = getattr(ydlh, "_best_effort_headers", None)
                if callable(headers_fn):
                    headers = headers_fn(
                        chosen.get("http_headers") or info.get("http_headers"),
                        opts["http_headers"],
                    )
                else:
                    headers = dict(
                        chosen.get("http_headers")
                        or info.get("http_headers")
                        or opts["http_headers"]
                    )

                _MUXED_URLS.add(url)
                print(
                    "[CORE-V2] muxed format "
                    f"id={chosen.get('format_id')} ext={chosen.get('ext')} "
                    f"height={chosen.get('height')} acodec={chosen.get('acodec')} "
                    f"vcodec={chosen.get('vcodec')}"
                )
                return {
                    "video_url": url,
                    "http_headers": headers,
                    "thumb": info.get("thumbnail", "") or "",
                    "muxed_av": True,
                }
            except Exception as exc:
                print("[CORE-V2] muxed extraction fallback:", exc)
                # Keep ytdlp_helpers.extract_video_info untouched so this call
                # cannot recurse back into extract_muxed_video.
                return old_safe_video(video_url)

        # audio_screen calls safe_extract_video_info. Do not replace the lower
        # level extract_video_info function because old_safe_video resolves it
        # dynamically during a fallback.
        ydlh.safe_extract_video_info = extract_muxed_video

        @video_player.run_on_ui_thread
        def start_muxed_master(screen, target: int) -> None:
            try:
                vp = getattr(screen, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp is not None else None
                audio = getattr(media, "android_player", None)
                if native_video is None or audio is None or not getattr(vp, "_prepared", False):
                    return
                if not getattr(screen, "_playback_desired", False) or getattr(screen, "_user_paused", False):
                    return

                sync_gen = int(getattr(screen, "_pymusic_muxed_sync_gen", 0) or 0) + 1
                screen._pymusic_muxed_sync_gen = sync_gen
                screen._pymusic_muxed_sync_done_gen = -1

                try:
                    native_video.pause()
                except Exception:
                    pass
                try:
                    audio.pause()
                except Exception:
                    pass

                try:
                    native_video.setVolume(1.0, 1.0)
                except Exception:
                    pass
                try:
                    media._mp_set_volume(0.0)
                except Exception:
                    try:
                        audio.setVolume(0.0, 0.0)
                    except Exception:
                        pass

                screen._pymusic_muxed_video_master = True

                @video_player.run_on_ui_thread
                def release(reason: str) -> None:
                    try:
                        if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                            return
                        if int(getattr(screen, "_pymusic_muxed_sync_done_gen", -1)) == sync_gen:
                            return
                        screen._pymusic_muxed_sync_done_gen = sync_gen

                        try:
                            current_listener = getattr(screen, "_pymusic_muxed_seek_listener", None)
                            if current_listener is not None:
                                native_video.setOnSeekCompleteListener(None)
                        except Exception:
                            pass
                        screen._pymusic_muxed_seek_listener = None

                        if not getattr(screen, "_pymusic_muxed_video_master", False):
                            return
                        if getattr(screen, "_user_paused", False) or not getattr(screen, "_playback_desired", False):
                            return

                        try:
                            video_pos = int(native_video.getCurrentPosition() or target)
                        except Exception:
                            video_pos = int(target)
                        try:
                            audio_pos = int(audio.getCurrentPosition() or 0)
                        except Exception:
                            audio_pos = 0

                        # The audio-only player is muted while video is visible,
                        # but keep its clock close because the rest of the app
                        # still reads it for progress/notification/background state.
                        if abs(audio_pos - video_pos) > 120:
                            try:
                                audio.seekTo(video_pos, 3)
                            except Exception:
                                try:
                                    audio.seekTo(video_pos)
                                except Exception:
                                    pass

                        old_set_video_mode(screen, True)
                        native_video.start()
                        audio.start()
                        print(
                            "[CORE-V2] muxed AV master started "
                            f"target={target} video={video_pos} audio={audio_pos} reason={reason}"
                        )

                        def align_shadow_clock(_dt=0):
                            try:
                                if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                                    return
                                if not getattr(screen, "_pymusic_muxed_video_master", False):
                                    return
                                if getattr(screen, "_user_paused", False) or not getattr(screen, "_playback_desired", False):
                                    return
                                vpos = int(native_video.getCurrentPosition() or 0)
                                apos = int(audio.getCurrentPosition() or 0)
                                if abs(apos - vpos) > 220:
                                    try:
                                        audio.seekTo(vpos, 3)
                                    except Exception:
                                        audio.seekTo(vpos)
                                    print(
                                        "[CORE-V2] shadow audio realigned "
                                        f"video={vpos} audio={apos}"
                                    )
                            except Exception as exc:
                                print("[CORE-V2] shadow realign failed:", exc)

                        Clock.schedule_once(align_shadow_clock, 0.35)
                    except Exception as exc:
                        print("[CORE-V2] master release failed:", exc)

                def on_video_seek_complete() -> None:
                    release("seek_complete")

                try:
                    listener = _MuxedSeekCompleteListener(on_video_seek_complete)
                    screen._pymusic_muxed_seek_listener = listener
                    native_video.setOnSeekCompleteListener(listener)
                except Exception as exc:
                    screen._pymusic_muxed_seek_listener = None
                    print("[CORE-V2] seek listener install failed:", exc)

                try:
                    audio.seekTo(int(target), 3)
                except Exception:
                    try:
                        audio.seekTo(int(target))
                    except Exception:
                        pass

                try:
                    native_video.seekTo(int(target), 3)
                except Exception:
                    native_video.seekTo(int(target))

                # Some vendor MediaPlayer implementations fail to emit
                # OnSeekComplete. Poll the actual position as a second signal
                # and keep a bounded timeout so neither player can stay paused.
                def poll_seek(attempt: int = 0):
                    try:
                        if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                            return
                        if int(getattr(screen, "_pymusic_muxed_sync_done_gen", -1)) == sync_gen:
                            return
                        current = int(native_video.getCurrentPosition() or 0)
                        if int(target) <= 0 or abs(current - int(target)) <= 120:
                            release("position_ready")
                            return
                        if attempt >= 23:
                            release("timeout")
                            return
                        Clock.schedule_once(lambda _dt: poll_seek(attempt + 1), 0.05)
                    except Exception:
                        release("poll_error")

                Clock.schedule_once(lambda _dt: poll_seek(0), 0.08)
            except Exception as exc:
                print("[CORE-V2] start master failed:", exc)

        def play_video_core(self, vurl: str, vheaders: dict):
            if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
                self._set_video_mode(False)
                return
            vp = getattr(self, "_video_player", None)
            if vp is None:
                return

            is_muxed = str(vurl or "") in _MUXED_URLS
            if not is_muxed:
                def show_fallback():
                    Clock.schedule_once(lambda _dt: old_set_video_mode(self, True), 0)
                vp.play(
                    vurl,
                    headers=(vheaders or {}),
                    loop=False,
                    start_pos_provider=self._audio_pos_ms,
                    on_prepared=show_fallback,
                )
                print("[CORE-V2] separate-stream fallback")
                return

            def prepared_muxed():
                try:
                    audio = getattr(media, "android_player", None)
                    target = int(audio.getCurrentPosition() or 0) if audio is not None else 0
                except Exception:
                    target = 0
                start_muxed_master(self, target)

            vp.play(
                vurl,
                headers=(vheaders or {}),
                loop=False,
                start_pos_provider=self._audio_pos_ms,
                start_paused=True,
                on_prepared=prepared_muxed,
            )

        def set_video_mode_core(self, video_on: bool):
            was_master = bool(getattr(self, "_pymusic_muxed_video_master", False))
            if not video_on and was_master:
                self._pymusic_muxed_sync_gen = int(
                    getattr(self, "_pymusic_muxed_sync_gen", 0) or 0
                ) + 1
                try:
                    listener = getattr(self, "_pymusic_muxed_seek_listener", None)
                    vp_for_listener = getattr(self, "_video_player", None)
                    native_for_listener = (
                        getattr(vp_for_listener, "player", None)
                        if vp_for_listener is not None
                        else None
                    )
                    if listener is not None and native_for_listener is not None:
                        native_for_listener.setOnSeekCompleteListener(None)
                except Exception:
                    pass
                self._pymusic_muxed_seek_listener = None

                vp = getattr(self, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp is not None else None
                audio = getattr(media, "android_player", None)
                try:
                    pos = (
                        int(native_video.getCurrentPosition() or 0)
                        if native_video is not None
                        else self._audio_pos_ms()
                    )
                except Exception:
                    pos = self._audio_pos_ms()
                try:
                    if audio is not None:
                        audio.pause()
                        try:
                            audio.seekTo(pos, 3)
                        except Exception:
                            audio.seekTo(pos)
                        audio.setVolume(1.0, 1.0)
                        if self._playback_desired and not self._user_paused:
                            Clock.schedule_once(lambda _dt: media._mp_start(), 0.12)
                except Exception:
                    try:
                        media._mp_set_volume(1.0)
                    except Exception:
                        pass
                try:
                    if native_video is not None:
                        native_video.setVolume(0.0, 0.0)
                except Exception:
                    pass
                self._pymusic_muxed_video_master = False
                print(f"[CORE-V2] returned audio master pos={pos}")
            return old_set_video_mode(self, video_on)

        screen_cls._play_video_if_screen_active = play_video_core
        screen_cls._set_video_mode = set_video_mode_core
        screen_cls._pymusic_core_av_master_v2 = True
        _INSTALLED = True
        print("[CORE-V2] synchronous muxed AV master installed")
        return True
