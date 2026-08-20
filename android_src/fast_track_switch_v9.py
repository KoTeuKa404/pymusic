"""Fast foreground track switching without starving the audio critical path.

The app can keep playing while the user browses the YT tab.  When a different
video is selected, the old player and old background workers must become stale
immediately; slow metadata/recommendation work must not compete with the new
audio extraction.

This layer is intentionally installed after CURRENT-V4 / STABILITY-V6.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlparse

_INSTALLED = False
_STARTED = False
_LOCK = threading.RLock()
_RELEASE_LOCK = threading.RLock()
_RELATED_LOCK = threading.RLock()
_RELATED_CACHE: dict[str, tuple[float, list]] = {}
_RELATED_INFLIGHT: dict[str, threading.Event] = {}


def _video_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and all(ch.isalnum() or ch in "-_" for ch in raw):
        return raw
    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query or "")
        vid = str((query.get("v") or [""])[0] or "")
        if vid:
            return vid
        host = (parsed.netloc or "").lower()
        if "youtu.be" in host:
            return (parsed.path or "").strip("/").split("/", 1)[0]
        path = parsed.path or ""
        for prefix in ("/shorts/", "/live/", "/embed/"):
            if prefix in path:
                return path.split(prefix, 1)[1].split("/", 1)[0]
    except Exception:
        pass
    return ""


def _same_video(a: str, b: str) -> bool:
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    a_id = _video_id(a)
    b_id = _video_id(b)
    return bool(a_id and b_id and a_id == b_id)


def _active_audio_screen():
    try:
        from kivy.app import App

        app = App.get_running_app()
        root = getattr(app, "root", None)
        sm = getattr(root, "sm", None)
        if sm is not None and hasattr(sm, "get_screen"):
            return sm.get_screen("audio")
    except Exception:
        pass
    return None


def _request_is_current(video_url: str) -> bool:
    screen = _active_audio_screen()
    if screen is None:
        return True
    current = str(getattr(screen, "_last_video_url", "") or "")
    return not current or _same_video(current, video_url)


def _audio_ready(media) -> bool:
    try:
        player = getattr(media, "android_player", None)
        if player is None or not bool(media._is_prepared()):
            return False
        return bool(player.isPlaying())
    except Exception:
        return False


def _wait_for_audio_priority(media, video_url: str, timeout: float = 5.0) -> bool:
    """Wait until critical audio startup has won the network/CPU race."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if not _request_is_current(video_url):
            return False
        if _audio_ready(media):
            return True
        time.sleep(0.06)
    return _request_is_current(video_url)


def _patch_media_release(media) -> None:
    if bool(getattr(media, "_pymusic_fast_release_v9", False)):
        return

    try:
        run_on_ui_thread = media.run_on_ui_thread
    except Exception:
        from android.runnable import run_on_ui_thread

    @run_on_ui_thread
    def release_snapshot(player):
        if player is None:
            return
        # Silence first. release() can occasionally take noticeable time on a
        # remote MediaPlayer, but the old track must stop being audible at once.
        try:
            player.setVolume(0.0, 0.0)
        except Exception:
            pass
        try:
            if bool(player.isPlaying()):
                player.pause()
        except Exception:
            pass
        try:
            player.release()
        except Exception as exc:
            try:
                media.log(f"[FAST-SWITCH-V9] old player release failed: {exc}")
            except Exception:
                pass

    def fast_reset_release():
        # Detach the global player synchronously on the Python side.  The UI
        # runnable only owns this snapshot, so a late release can never destroy
        # a newly-created MediaPlayer.
        with _RELEASE_LOCK:
            player = getattr(media, "android_player", None)
            media.android_player = None
            media.is_playing = False
            media._mp_prepared = False
        if player is not None:
            release_snapshot(player)

    media._mp_reset_release = fast_reset_release
    media._pymusic_fast_release_v9 = True
    print("[FAST-SWITCH-V9] snapshot MediaPlayer release enabled")


def _patch_related_loader(media) -> None:
    try:
        import related_videos as related
    except Exception as exc:
        print("[FAST-SWITCH-V9] related import failed:", exc)
        return

    if bool(getattr(related, "_pymusic_low_priority_v9", False)):
        return

    old_fetch = getattr(related, "fetch_related_videos", None)
    if not callable(old_fetch):
        return

    def fetch_related_low_priority(
        video_url: str,
        title: str = "",
        channel: str = "",
        limit: int = 8,
    ):
        if not _wait_for_audio_priority(media, video_url, timeout=5.0):
            return []

        key = _video_id(video_url) or str(video_url or "")
        owner = False
        event = None

        with _RELATED_LOCK:
            cached = _RELATED_CACHE.get(key)
            if cached and (time.monotonic() - cached[0]) < 120.0:
                return list(cached[1])[: max(1, int(limit or 8))]
            event = _RELATED_INFLIGHT.get(key)
            if event is None:
                event = threading.Event()
                _RELATED_INFLIGHT[key] = event
                owner = True

        if not owner:
            event.wait(10.0)
            with _RELATED_LOCK:
                cached = _RELATED_CACHE.get(key)
                return list(cached[1])[: max(1, int(limit or 8))] if cached else []

        result = []
        try:
            if not _request_is_current(video_url):
                return []
            result = old_fetch(video_url, title, channel, limit=limit) or []
            if not _request_is_current(video_url):
                return []
            return result
        except BaseException as exc:
            print("[FAST-SWITCH-V9] related worker stopped safely:", exc)
            return []
        finally:
            with _RELATED_LOCK:
                if result:
                    _RELATED_CACHE[key] = (time.monotonic(), list(result))
                    while len(_RELATED_CACHE) > 32:
                        _RELATED_CACHE.pop(next(iter(_RELATED_CACHE)), None)
                current_event = _RELATED_INFLIGHT.pop(key, None)
                if current_event is not None:
                    current_event.set()

    related.fetch_related_videos = fetch_related_low_priority
    related._pymusic_low_priority_v9 = True
    print("[FAST-SWITCH-V9] related loading moved behind audio startup")


def _patch_comment_loader(media) -> None:
    try:
        import current_video_related_fix as current
    except Exception:
        return

    if bool(getattr(current, "_pymusic_comments_priority_v9", False)):
        return

    old_fetch = getattr(current, "_fetch_comments", None)
    if not callable(old_fetch):
        return

    def comments_after_audio(video_url: str, limit: int = 3):
        if not _wait_for_audio_priority(media, video_url, timeout=6.0):
            return [], None
        if not _request_is_current(video_url):
            return [], None
        try:
            return old_fetch(video_url, limit)
        except BaseException as exc:
            print("[FAST-SWITCH-V9] comments stopped safely:", exc)
            return [], None

    current._fetch_comments = comments_after_audio
    current._pymusic_comments_priority_v9 = True
    print("[FAST-SWITCH-V9] comments moved behind audio startup")


def _install_now() -> bool:
    global _INSTALLED

    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
        except Exception:
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False

        # Wait until the layers that also wrap play_audio/recovery are final.
        if not bool(getattr(cls, "_pymusic_current_related_v4", False)):
            return False
        if not bool(getattr(cls, "_pymusic_stability_v6", False)):
            return False

        if bool(getattr(cls, "_pymusic_fast_switch_v9", False)):
            _INSTALLED = True
            return True

        media = audio_screen.ma
        _patch_media_release(media)
        _patch_related_loader(media)
        _patch_comment_loader(media)

        old_play_audio = cls.play_audio
        old_metadata = cls._ensure_metadata_async

        def ensure_switch_state(self):
            if not hasattr(self, "_pymusic_deferred_meta_v9"):
                self._pymusic_deferred_meta_v9 = set()
            if not hasattr(self, "_pymusic_switch_serial_v9"):
                self._pymusic_switch_serial_v9 = 0

        def metadata_after_audio(self, video_url: str):
            ensure_switch_state(self)
            url = str(video_url or "")
            if not url:
                return
            try:
                if url in getattr(self, "_metadata_loaded_urls", set()):
                    return
            except Exception:
                pass

            gen = int(getattr(self, "_load_gen", 0) or 0)
            key = (url, gen)
            if key in self._pymusic_deferred_meta_v9:
                return
            self._pymusic_deferred_meta_v9.add(key)

            def worker(expected_url=url, expected_gen=gen, token=key):
                try:
                    # Fresh extraction already carries title/channel/thumb and
                    # marks _metadata_loaded_urls.  Waiting lets that path win,
                    # so a duplicate yt-dlp extraction is normally avoided.
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if int(getattr(self, "_load_gen", -1)) != expected_gen:
                            return
                        if not _same_video(
                            str(getattr(self, "_last_video_url", "") or ""),
                            expected_url,
                        ):
                            return
                        if expected_url in getattr(self, "_metadata_loaded_urls", set()):
                            return
                        if _audio_ready(media):
                            break
                        time.sleep(0.06)

                    if int(getattr(self, "_load_gen", -1)) != expected_gen:
                        return
                    if not _same_video(
                        str(getattr(self, "_last_video_url", "") or ""),
                        expected_url,
                    ):
                        return
                    if expected_url in getattr(self, "_metadata_loaded_urls", set()):
                        return
                    old_metadata(self, expected_url)
                finally:
                    try:
                        self._pymusic_deferred_meta_v9.discard(token)
                    except Exception:
                        pass

            threading.Thread(
                target=worker,
                name="pymusic-deferred-metadata-v9",
                daemon=True,
            ).start()

        def play_audio_fast_switch(self, video_url, *args, **kwargs):
            ensure_switch_state(self)
            new_url = str(video_url or "")
            previous_url = str(getattr(self, "_last_video_url", "") or "")
            switching = bool(
                new_url
                and previous_url
                and not _same_video(new_url, previous_url)
            )

            if switching:
                self._pymusic_switch_serial_v9 += 1

                # Invalidate every old generation before cleanup/UI work.  The
                # base play_audio will allocate its normal new generation later;
                # an extra increment here is intentional and makes stale
                # completion/extract/video callbacks harmless immediately.
                self._load_gen = int(getattr(self, "_load_gen", 0) or 0) + 1
                self._completion_guard_gen = -1
                self._bg_endguard_fired_gen = -1
                self._extract_fail_handled_gen = -1
                self._pymusic_video_start_request = int(
                    getattr(self, "_pymusic_video_start_request", 0) or 0
                ) + 1

                # Cancel old recovery/autoplay intent before it can race the
                # user's explicit selection.
                try:
                    self._network_waiting_v6 = False
                    self._network_wait_gen_v6 = -1
                except Exception:
                    pass
                try:
                    self._current_auto_next_pending_v4 = False
                except Exception:
                    pass
                try:
                    self._playlist_play_current_before_next_v1 = False
                except Exception:
                    pass

                self._resume_pos_ms = 0
                self._last_good_dur_ms = 0
                self._bad_dur_hits = 0
                self._stream_url = None
                self._headers = {}
                self._expire_ts = None
                self._art_path = None

                # Detach old audio now instead of waiting for extraction of the
                # new URL.  The patched release works on the old snapshot only.
                try:
                    media._mp_reset_release()
                except Exception:
                    pass

                try:
                    print(
                        "[FAST-SWITCH-V9] switching immediately "
                        f"{previous_url!r} -> {new_url!r}"
                    )
                except Exception:
                    pass

            result = old_play_audio(self, video_url, *args, **kwargs)

            if switching and not bool(getattr(self, "_app_in_background", False)):
                try:
                    audio_screen.Clock.schedule_once(
                        lambda _dt: self._sync_ui_loading(), 0
                    )
                except Exception:
                    pass
            return result

        cls._ensure_metadata_async = metadata_after_audio
        cls.play_audio = play_audio_fast_switch
        cls._pymusic_fast_switch_v9 = True

        _INSTALLED = True
        print(
            "[FAST-SWITCH-V9] immediate track invalidation + "
            "audio-priority startup enabled"
        )
        return True


def install_fast_track_switch_v9() -> bool:
    global _STARTED

    if _install_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(600):
            if _install_now():
                return
            time.sleep(0.05)
        print("[FAST-SWITCH-V9] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-fast-switch-v9-installer",
        daemon=True,
    ).start()
    return True
