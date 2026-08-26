"""Long-session playback stability.

The base player proactively restarted the current stream shortly before the
signed Googlevideo URL expired.  That is unnecessary while Android MediaPlayer
already owns a working connection and it can visibly jump/restart long tracks.

This layer keeps the active MediaPlayer untouched:
- expiry only refreshes a spare direct URL in the background;
- transient duration=0 reports do not restart a stream that was just moving;
- the foreground buffer watchdog waits for a confirmed stall and routes through
  the normal recovery path instead of directly re-extracting;
- recovery always preserves the last known good playback position.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.RLock()
_INSTALLED = False
_STARTED = False


def _network_available(media) -> bool:
    try:
        return bool(media.is_network_available())
    except Exception:
        return True


def _install_now() -> bool:
    global _INSTALLED

    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
            import ytdlp_helpers as ydlh
        except Exception:
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False

        # Install last: both of these layers wrap the same playback/recovery
        # methods and must settle before long-session policy owns them.
        if not bool(getattr(cls, "_pymusic_stability_v6", False)):
            return False
        if not bool(getattr(cls, "_pymusic_fast_switch_v9", False)):
            return False
        if bool(getattr(cls, "_pymusic_longplay_v10", False)):
            _INSTALLED = True
            return True

        Clock = audio_screen.Clock
        media = audio_screen.ma

        old_tick = cls._tick
        old_recover = cls._recover_stream
        old_play_audio = cls.play_audio

        def ensure_state(self):
            if not hasattr(self, "_longplay_url_v10"):
                self._longplay_url_v10 = ""
            if not hasattr(self, "_longplay_last_pos_v10"):
                self._longplay_last_pos_v10 = 0
            if not hasattr(self, "_longplay_last_motion_v10"):
                self._longplay_last_motion_v10 = time.monotonic()
            if not hasattr(self, "_longplay_refresh_inflight_v10"):
                self._longplay_refresh_inflight_v10 = False
            if not hasattr(self, "_longplay_refresh_serial_v10"):
                self._longplay_refresh_serial_v10 = 0

        def current_position(self, fallback=0) -> int:
            try:
                player = getattr(media, "android_player", None)
                if player is not None:
                    return max(0, int(player.getCurrentPosition() or fallback or 0))
            except Exception:
                pass
            return max(0, int(fallback or 0))

        def record_motion(self, pos=None):
            ensure_state(self)
            if pos is None:
                pos = current_position(self, self._longplay_last_pos_v10)
            try:
                pos = max(0, int(pos or 0))
            except Exception:
                pos = 0
            previous = int(getattr(self, "_longplay_last_pos_v10", 0) or 0)
            # A normal 0.5 s tick advances by hundreds of ms. Also count seeks
            # as motion so watchdog/recovery do not fight a user action.
            if abs(pos - previous) >= 120:
                self._longplay_last_motion_v10 = time.monotonic()
                self._longplay_last_pos_v10 = pos
            elif pos > previous:
                self._longplay_last_pos_v10 = pos
            return pos

        def play_audio_v10(self, video_url, *args, **kwargs):
            ensure_state(self)
            url = str(video_url or "")
            if url and url != str(getattr(self, "_longplay_url_v10", "") or ""):
                self._longplay_url_v10 = url
                self._longplay_last_pos_v10 = 0
                self._longplay_last_motion_v10 = time.monotonic()
                self._longplay_refresh_serial_v10 += 1
                self._longplay_refresh_inflight_v10 = False
            return old_play_audio(self, video_url, *args, **kwargs)

        def tick_v10(self, dt):
            try:
                record_motion(self)
            except Exception:
                pass
            return old_tick(self, dt)

        def recover_v10(self, reason: str, pos: int, *, force_fresh: bool = False):
            ensure_state(self)
            try:
                supplied = max(0, int(pos or 0))
            except Exception:
                supplied = 0
            last_good = int(getattr(self, "_longplay_last_pos_v10", 0) or 0)
            saved = max(supplied, last_good)
            self._resume_pos_ms = saved

            # STABILITY-V6 owns real offline periods. Preserve its behaviour,
            # only pass a better resume position into it.
            if not _network_available(media):
                return old_recover(self, reason, saved, force_fresh=force_fresh)

            # duration=0/bg checks are heuristics. Never tear down a player that
            # demonstrably advanced a moment ago.
            transient = str(reason or "") in {
                "duration=0",
                "bg_stall",
                "watchdog",
                "watchdog-stall",
            }
            motion_age = time.monotonic() - float(
                getattr(self, "_longplay_last_motion_v10", 0.0) or 0.0
            )
            if transient and motion_age < 7.0:
                try:
                    print(
                        "[LONGPLAY-V10] ignored transient recovery "
                        f"reason={reason!r} pos={saved} motion_age={motion_age:.2f}s"
                    )
                except Exception:
                    pass
                return None

            return old_recover(self, reason, saved, force_fresh=force_fresh)

        def buffer_watchdog_v10(self, dt):
            ensure_state(self)
            if bool(getattr(self, "_app_in_background", False)):
                return
            if (time.time() - float(getattr(self, "_last_bg_resume_ts", 0.0) or 0.0)) < 6.0:
                return
            if not bool(getattr(self, "_playback_desired", False)) or bool(
                getattr(self, "_user_paused", False)
            ):
                return

            player = getattr(media, "android_player", None)
            if player is None:
                return

            pos = current_position(self, getattr(self, "_longplay_last_pos_v10", 0))
            previous = getattr(self, "_last_watch_pos", None)
            now = time.time()

            if previous is None or abs(int(pos) - int(previous or 0)) >= 120:
                self._last_watch_pos = pos
                self._buffer_watchdog_ts = now
                record_motion(self, pos)
                return

            self._last_watch_pos = pos
            stuck_for = now - float(getattr(self, "_buffer_watchdog_ts", now) or now)
            if stuck_for < 18.0:
                return

            # Reset the watchdog timer before recovery so one stall creates only
            # one recovery attempt, not a new extractor every three seconds.
            self._buffer_watchdog_ts = now
            saved = max(
                int(pos or 0),
                int(getattr(self, "_longplay_last_pos_v10", 0) or 0),
            )
            self._resume_pos_ms = saved

            if not _network_available(media):
                self._recover_stream("watchdog", saved)
                return

            try:
                print(
                    "[LONGPLAY-V10] confirmed stream stall; recovering "
                    f"at {saved} ms after {stuck_for:.1f}s"
                )
            except Exception:
                pass
            self._recover_stream("watchdog-stall", saved)

        def schedule_expiry_v10(self):
            """Refresh only the spare direct URL; never restart active playback."""
            ensure_state(self)

            try:
                event = getattr(self, "_refresh_ev", None)
                if event is not None:
                    event.cancel()
            except Exception:
                pass
            self._refresh_ev = None

            url = str(getattr(self, "_last_video_url", "") or "")
            try:
                expire_ts = int(getattr(self, "_expire_ts", 0) or 0)
            except Exception:
                expire_ts = 0
            if not url or expire_ts <= 0:
                return

            # Local cached files have no signed URL expiry at all.
            try:
                if str(getattr(self, "_stream_url", "") or "").startswith("file://"):
                    return
            except Exception:
                pass

            expected_gen = int(getattr(self, "_load_gen", 0) or 0)
            self._longplay_refresh_serial_v10 += 1
            serial = int(self._longplay_refresh_serial_v10)
            now = int(time.time())
            delay = max(45, expire_ts - now - 120)

            def still_current() -> bool:
                return bool(
                    int(getattr(self, "_load_gen", -1)) == expected_gen
                    and str(getattr(self, "_last_video_url", "") or "") == url
                    and int(getattr(self, "_longplay_refresh_serial_v10", -1)) == serial
                )

            def refresh_job():
                if not still_current():
                    return
                if self._longplay_refresh_inflight_v10:
                    return
                if not _network_available(media):
                    if still_current():
                        self._refresh_ev = Clock.schedule_once(refresh_fire, 60.0)
                    return

                self._longplay_refresh_inflight_v10 = True
                try:
                    info = ydlh.extract_audio_info(
                        url,
                        prefer_compat=bool(getattr(self, "_prefer_compat_audio", False)),
                    ) or {}
                    if not still_current():
                        return
                    fresh_url = str(info.get("audio_url") or "")
                    fresh_headers = dict(info.get("http_headers") or {})
                    fresh_expire = info.get("expire_ts")
                    if not fresh_url:
                        raise RuntimeError("fresh audio URL missing")

                    # Only prepare recovery cache. Do NOT replace _stream_url and
                    # do NOT call _start_from_known_stream while playback works.
                    self._put_cache(url, fresh_url, fresh_headers, fresh_expire)
                    try:
                        if fresh_expire:
                            self._expire_ts = int(fresh_expire)
                    except Exception:
                        pass
                    try:
                        print(
                            "[LONGPLAY-V10] refreshed spare stream URL without restart "
                            f"gen={expected_gen}"
                        )
                    except Exception:
                        pass
                    if still_current() and getattr(self, "_expire_ts", None):
                        Clock.schedule_once(lambda _dt: schedule_expiry_v10(self), 0)
                except Exception as exc:
                    try:
                        print("[LONGPLAY-V10] spare URL refresh failed:", exc)
                    except Exception:
                        pass
                    if still_current():
                        self._refresh_ev = Clock.schedule_once(refresh_fire, 90.0)
                finally:
                    self._longplay_refresh_inflight_v10 = False

            def refresh_fire(_dt=0):
                if not still_current():
                    return
                threading.Thread(
                    target=refresh_job,
                    name="pymusic-longplay-url-refresh-v10",
                    daemon=True,
                ).start()

            self._refresh_ev = Clock.schedule_once(refresh_fire, float(delay))
            try:
                print(
                    "[LONGPLAY-V10] passive expiry refresh scheduled "
                    f"in {delay}s; active player will not restart"
                )
            except Exception:
                pass

        cls.play_audio = play_audio_v10
        cls._tick = tick_v10
        cls._recover_stream = recover_v10
        cls._buffer_watchdog = buffer_watchdog_v10
        cls._schedule_expiry = schedule_expiry_v10
        cls._pymusic_longplay_v10 = True

        # Kivy Clock WeakMethod may resolve the function's Python __name__ after
        # monkey-patching. Keep aliases available on the class just like the
        # existing background-tick compatibility layer does.
        for method in (tick_v10, buffer_watchdog_v10):
            try:
                setattr(cls, method.__name__, method)
            except Exception:
                pass

        _INSTALLED = True
        print("[LONGPLAY-V10] long-session playback stability enabled")
        return True


def install_long_playback_stability_v10() -> bool:
    global _STARTED

    if _install_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(800):
            if _install_now():
                return
            time.sleep(0.05)
        print("[LONGPLAY-V10] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-longplay-v10-installer",
        daemon=True,
    ).start()
    return True
