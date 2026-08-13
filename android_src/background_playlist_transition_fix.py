"""Make playlist auto-next reliable while the Android screen is off.

The base player already owns MediaPlayer completion callbacks, wake locks and the
playlist model.  This patch only fixes three background-specific latency/race
points:

* the fallback completion guard must not sleep for up to 10 seconds;
* next-track URL prefetch must not depend on Kivy Clock while Activity is paused;
* a completion callback must not perform the whole next-track transition inline
  on MediaPlayer's callback thread.
"""
from __future__ import annotations

import threading
import time

_INSTALLED = False
_INSTALL_LOCK = threading.RLock()


def install_background_playlist_transition_fix() -> bool:
    global _INSTALLED

    with _INSTALL_LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
            import ytdlp_helpers as ydlh
        except Exception as exc:
            print("[BG-NEXT] import failed:", exc)
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_background_next_v1", False)):
            _INSTALLED = True
            return True

        Clock = audio_screen.Clock
        media = audio_screen.ma

        # --------------------------------------------------------------
        # Fast independent prefetch.
        # --------------------------------------------------------------
        def prefetch_next_background_safe(self):
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
                expected_gen = int(getattr(self, "_load_gen", 0) or 0)

                if not next_url or next_url == current_url:
                    return None

                cached = getattr(self, "_url_cache", {}).get(next_url)
                if cached and cached.get("audio_url"):
                    return None

                inflight = getattr(self, "_prefetch_inflight", None)
                if inflight is None:
                    self._prefetch_inflight = set()
                    inflight = self._prefetch_inflight
                if next_url in inflight:
                    return None
                inflight.add(next_url)
            except Exception:
                return None

            def job():
                try:
                    # A short delay avoids competing with the current track's
                    # prepare/start, but unlike Clock.schedule_once it continues
                    # to run when PythonActivity is paused by screen-off.
                    time.sleep(0.35)

                    if int(getattr(self, "_load_gen", -1)) != expected_gen:
                        return
                    if str(getattr(self, "_last_video_url", "") or "") != current_url:
                        return

                    cached_now = getattr(self, "_url_cache", {}).get(next_url)
                    if cached_now and cached_now.get("audio_url"):
                        return

                    try:
                        info = ydlh.extract_audio_info(
                            next_url,
                            prefer_compat=bool(
                                getattr(self, "_prefer_compat_audio", False)
                            ),
                        )
                    except TypeError:
                        info = ydlh.extract_audio_info(next_url)

                    audio_url = str((info or {}).get("audio_url") or "")
                    if not audio_url:
                        return

                    headers = dict((info or {}).get("http_headers") or {})
                    expire_ts = (info or {}).get("expire_ts")
                    self._put_cache(next_url, audio_url, headers, expire_ts)

                    # Do not download the whole next song here.  A full-file
                    # prefetch competes for mobile bandwidth with the current
                    # stream.  The direct URL cache is enough for instant start.
                    print(f"[BG-NEXT] prefetched next URL: {next_url}")
                except Exception as exc:
                    print("[BG-NEXT] prefetch failed:", exc)
                finally:
                    try:
                        inflight.discard(next_url)
                    except Exception:
                        pass

            threading.Thread(target=job, daemon=True).start()
            return None

        # --------------------------------------------------------------
        # Responsive fallback completion guard.
        # --------------------------------------------------------------
        def start_completion_guard_fast(self, gen: int, dur_ms: int):
            try:
                dur_ms = int(dur_ms or 0)
            except Exception:
                dur_ms = 0
            if dur_ms <= 0:
                return

            self._completion_guard_gen = gen
            if getattr(self, "_pymusic_end_lock", None) is None:
                self._pymusic_end_lock = threading.Lock()

            def fire_end_once(reason: str, pos: int, playing: bool) -> bool:
                lock = self._pymusic_end_lock
                with lock:
                    if not self._is_current_gen(gen):
                        return False
                    if self._completion_guard_gen != gen:
                        return False
                    if self._bg_endguard_fired_gen == gen:
                        return False
                    self._bg_endguard_fired_gen = gen

                print(
                    "[BG-NEXT] completion guard fired "
                    f"gen={gen} pos={pos} dur={dur_ms} playing={playing} "
                    f"reason={reason}"
                )
                self._resume_pos_ms = 0
                if self.repeat:
                    # Keep repeat off the watcher thread too.
                    threading.Thread(target=self._restart_same, daemon=True).start()
                elif self._auto_skip:
                    self._queue_auto_next()
                else:
                    self._playback_desired = False
                    self._user_paused = True
                    Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
                return True

            def job():
                while (
                    self._is_current_gen(gen)
                    and self._completion_guard_gen == gen
                ):
                    if not self._playback_desired or self._user_paused:
                        return
                    if self._bg_endguard_fired_gen == gen:
                        return

                    player = getattr(media, "android_player", None)
                    if player is None:
                        time.sleep(0.15)
                        continue

                    try:
                        pos = max(0, int(player.getCurrentPosition() or 0))
                    except Exception:
                        pos = 0
                    try:
                        playing = bool(player.isPlaying())
                    except Exception:
                        playing = True

                    remaining = max(0, dur_ms - pos)

                    # A missed Android onCompletion is detected immediately once
                    # MediaPlayer stops at the end.  Do not treat buffering in
                    # the middle of a track as completion.
                    if (not playing) and remaining <= 1500:
                        fire_end_once("player-stopped-near-end", pos, playing)
                        return

                    # Some vendor MediaPlayers keep reporting isPlaying() for a
                    # fraction of a second after their position reaches duration.
                    if remaining <= 80:
                        time.sleep(0.08)
                        try:
                            pos2 = max(0, int(player.getCurrentPosition() or 0))
                        except Exception:
                            pos2 = pos
                        try:
                            playing2 = bool(player.isPlaying())
                        except Exception:
                            playing2 = playing
                        if (not playing2) or pos2 >= max(0, dur_ms - 40):
                            fire_end_once("position-reached-end", pos2, playing2)
                            return

                    # Frequent enough for a seamless transition, cheap enough to
                    # keep a single daemon watcher alive for long tracks.
                    if remaining > 5000:
                        delay = 0.75
                    elif remaining > 1500:
                        delay = 0.35
                    else:
                        delay = 0.12
                    time.sleep(delay)

            threading.Thread(target=job, daemon=True).start()

        # --------------------------------------------------------------
        # Never block MediaPlayer's completion callback with extraction/reset.
        # --------------------------------------------------------------
        def queue_auto_next_worker(self) -> bool:
            try:
                gen = int(getattr(self, "_load_gen", 0) or 0)
            except Exception:
                gen = 0

            if getattr(self, "_pymusic_next_lock", None) is None:
                self._pymusic_next_lock = threading.Lock()

            with self._pymusic_next_lock:
                if int(getattr(self, "_pymusic_next_started_gen", -1)) == gen:
                    return True
                self._pymusic_next_started_gen = gen

            def run():
                try:
                    # If another action already moved to a new generation, this
                    # stale completion event must not advance again.
                    if int(getattr(self, "_load_gen", -1)) != gen:
                        return
                    print(
                        f"[BG-NEXT] advancing playlist bg={bool(getattr(self, '_app_in_background', False))} gen={gen}"
                    )
                    if self._advance_to_next_track():
                        return

                    self._playback_desired = False
                    self._user_paused = True
                    Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
                except Exception as exc:
                    print("[BG-NEXT] advance failed:", exc)

            if bool(getattr(self, "_app_in_background", False)):
                threading.Thread(target=run, daemon=True).start()
            else:
                Clock.schedule_once(lambda _dt: run(), 0)
            return True

        cls._prefetch_next_track_audio = prefetch_next_background_safe
        cls._start_completion_guard = start_completion_guard_fast
        cls._queue_auto_next = queue_auto_next_worker
        cls._pymusic_background_next_v1 = True

        _INSTALLED = True
        print("[BG-NEXT] background playlist transition fix enabled")
        return True
