"""Install a non-blocking muxed audio/video handoff for Android playback.

Audio-only playback remains audible while progressive video prepares muted.
The app switches sound to the muxed video only after both clocks are aligned,
so a failed synchronization can never remove the thumbnail or silence playback.
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
        except Exception as exc:
            print("[CORE-V3] import failed:", exc)
            return False

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        video_cls = getattr(video_player, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False
        if getattr(screen_cls, "_pymusic_core_av_master_v3", False):
            _INSTALLED = True
            return True

        Clock = audio_screen.Clock
        media = audio_screen.ma
        old_safe_video = ydlh.safe_extract_video_info
        old_set_video_mode = screen_cls._set_video_mode

        def extract_muxed_video(video_url: str):
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
                if (
                    not url
                    or str(chosen.get("acodec") or "none") == "none"
                    or str(chosen.get("vcodec") or "none") == "none"
                ):
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
                    "[CORE-V3] muxed format "
                    f"id={chosen.get('format_id')} ext={chosen.get('ext')} "
                    f"height={chosen.get('height')}"
                )
                return {
                    "video_url": url,
                    "http_headers": headers,
                    "thumb": info.get("thumbnail", "") or "",
                    "muxed_av": True,
                }
            except Exception as exc:
                print("[CORE-V3] muxed extraction fallback:", exc)
                return old_safe_video(video_url)

        ydlh.safe_extract_video_info = extract_muxed_video

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

        def seek_player(player, position_ms: int) -> None:
            if player is None:
                return
            try:
                player.seekTo(int(position_ms), 3)
            except Exception:
                try:
                    player.seekTo(int(position_ms))
                except Exception:
                    pass

        @video_player.run_on_ui_thread
        def begin_muxed_handoff(screen) -> None:
            try:
                vp = getattr(screen, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp is not None else None
                audio = getattr(media, "android_player", None)
                if native_video is None or audio is None:
                    return
                if not getattr(vp, "_prepared", False):
                    return
                if not getattr(screen, "_playback_desired", False):
                    return
                if getattr(screen, "_user_paused", False):
                    return

                sync_gen = int(getattr(screen, "_pymusic_muxed_sync_gen", 0) or 0) + 1
                screen._pymusic_muxed_sync_gen = sync_gen
                screen._pymusic_muxed_video_master = False
                screen._pymusic_muxed_handoff_pending = True

                set_audio_volume(audio, 1.0)
                try:
                    native_video.setVolume(0.0, 0.0)
                except Exception:
                    pass
                try:
                    if not native_video.isPlaying():
                        native_video.start()
                except Exception:
                    try:
                        native_video.start()
                    except Exception:
                        pass

                def cancel(reason: str) -> None:
                    if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                        return
                    screen._pymusic_muxed_handoff_pending = False
                    screen._pymusic_muxed_video_master = False
                    set_audio_volume(audio, 1.0)
                    try:
                        native_video.setVolume(0.0, 0.0)
                    except Exception:
                        pass
                    print(f"[CORE-V3] handoff fallback: {reason}")

                def complete(audio_pos: int, video_pos: int) -> None:
                    if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                        return
                    if getattr(screen, "_user_paused", False):
                        cancel("paused")
                        return
                    if not getattr(screen, "_playback_desired", False):
                        cancel("not desired")
                        return

                    old_set_video_mode(screen, True)
                    try:
                        native_video.setVolume(1.0, 1.0)
                    except Exception:
                        cancel("video volume failed")
                        return
                    set_audio_volume(audio, 0.0)
                    screen._pymusic_muxed_handoff_pending = False
                    screen._pymusic_muxed_video_master = True
                    print(
                        "[CORE-V3] handoff complete "
                        f"audio={audio_pos} video={video_pos} "
                        f"drift={video_pos - audio_pos}"
                    )

                    def align_shadow(_dt=0):
                        try:
                            if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                                return
                            if not getattr(screen, "_pymusic_muxed_video_master", False):
                                return
                            vpos = int(native_video.getCurrentPosition() or 0)
                            apos = int(audio.getCurrentPosition() or 0)
                            if abs(apos - vpos) > 250:
                                seek_player(audio, vpos)
                        except Exception as exc:
                            print("[CORE-V3] shadow align failed:", exc)

                    Clock.schedule_once(align_shadow, 0.4)

                def poll(attempt: int = 0):
                    try:
                        if int(getattr(screen, "_pymusic_muxed_sync_gen", -1)) != sync_gen:
                            return
                        if not getattr(screen, "_pymusic_muxed_handoff_pending", False):
                            return
                        if getattr(screen, "_user_paused", False):
                            cancel("paused")
                            return
                        if not getattr(screen, "_playback_desired", False):
                            cancel("not desired")
                            return

                        audio_pos = int(audio.getCurrentPosition() or 0)
                        video_pos = int(native_video.getCurrentPosition() or 0)
                        try:
                            video_playing = bool(native_video.isPlaying())
                        except Exception:
                            video_playing = video_pos > 0
                        drift = video_pos - audio_pos

                        if video_playing and abs(drift) <= 100:
                            complete(audio_pos, video_pos)
                            return

                        if attempt in (0, 8, 16, 24) and abs(drift) > 140:
                            seek_player(native_video, audio_pos)

                        if attempt >= 32:
                            cancel(
                                f"timeout audio={audio_pos} video={video_pos} drift={drift}"
                            )
                            return
                        Clock.schedule_once(lambda _dt: poll(attempt + 1), 0.05)
                    except Exception as exc:
                        cancel(f"poll error: {exc}")

                Clock.schedule_once(lambda _dt: poll(0), 0.05)
            except Exception as exc:
                print("[CORE-V3] begin handoff failed:", exc)
                try:
                    set_audio_volume(getattr(media, "android_player", None), 1.0)
                except Exception:
                    pass

        def play_video_core(self, vurl: str, vheaders: dict):
            if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
                self._set_video_mode(False)
                return
            vp = getattr(self, "_video_player", None)
            if vp is None:
                return

            if str(vurl or "") not in _MUXED_URLS:
                def show_fallback():
                    Clock.schedule_once(lambda _dt: old_set_video_mode(self, True), 0)

                vp.play(
                    vurl,
                    headers=(vheaders or {}),
                    loop=False,
                    start_pos_provider=self._audio_pos_ms,
                    on_prepared=show_fallback,
                )
                print("[CORE-V3] separate-stream fallback")
                return

            self._pymusic_muxed_video_master = False
            self._pymusic_muxed_handoff_pending = True
            set_audio_volume(getattr(media, "android_player", None), 1.0)

            vp.play(
                vurl,
                headers=(vheaders or {}),
                loop=False,
                start_pos_provider=self._audio_pos_ms,
                start_paused=False,
                on_prepared=lambda: begin_muxed_handoff(self),
            )

        def set_video_mode_core(self, video_on: bool):
            was_master = bool(getattr(self, "_pymusic_muxed_video_master", False))
            was_pending = bool(getattr(self, "_pymusic_muxed_handoff_pending", False))

            if not video_on and (was_master or was_pending):
                self._pymusic_muxed_sync_gen = int(
                    getattr(self, "_pymusic_muxed_sync_gen", 0) or 0
                ) + 1
                self._pymusic_muxed_handoff_pending = False

                vp = getattr(self, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp is not None else None
                audio = getattr(media, "android_player", None)

                if was_master:
                    try:
                        pos = int(native_video.getCurrentPosition() or 0)
                    except Exception:
                        pos = int(self._audio_pos_ms() or 0)
                    seek_player(audio, pos)

                set_audio_volume(audio, 1.0)
                try:
                    if (
                        audio is not None
                        and self._playback_desired
                        and not self._user_paused
                        and not audio.isPlaying()
                    ):
                        audio.start()
                except Exception:
                    try:
                        if self._playback_desired and not self._user_paused:
                            media._mp_start()
                    except Exception:
                        pass
                try:
                    if native_video is not None:
                        native_video.setVolume(0.0, 0.0)
                except Exception:
                    pass

                self._pymusic_muxed_video_master = False
                print("[CORE-V3] returned audio master")

            return old_set_video_mode(self, video_on)

        screen_cls._play_video_if_screen_active = play_video_core
        screen_cls._set_video_mode = set_video_mode_core
        screen_cls._pymusic_core_av_master_v3 = True
        _INSTALLED = True
        print("[CORE-V3] non-blocking muxed AV handoff installed")
        return True
