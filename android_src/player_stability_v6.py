"""Final playback stability layer.

Keeps four behaviours together because they affect the same transition path:
- align playlist.index with the actually playing URL before moving next;
- do not reset/re-extract the player while Android reports no network;
- reuse yt-dlp's current public Innertube API key for YouTube watch-next;
- serialize/delay comment extraction so it cannot compete with startup workers.

This module is intentionally installed after CURRENT-V4.
"""
from __future__ import annotations

import threading
import time

_INSTALLED = False
_STARTED = False
_LOCK = threading.RLock()
_COMMENT_LOCK = threading.Lock()


def _network_available(media) -> bool:
    try:
        return bool(media.is_network_available())
    except Exception:
        # If Android connectivity state itself is unavailable, do not block
        # playback based on an assumption.
        return True


def _patch_watch_next_key() -> None:
    """Give related_videos the same public Innertube key as bundled yt-dlp."""
    try:
        import related_videos as related
    except Exception as exc:
        print("[STABILITY-V6] related import failed:", exc)
        return

    if bool(getattr(related, "_pymusic_watch_next_key_v6", False)):
        return

    old_profiles = getattr(related, "_client_profiles", None)
    old_merge = getattr(related, "_merge_page_config", None)
    if not callable(old_profiles) or not callable(old_merge):
        return

    def client_profiles_with_key():
        profiles = list(old_profiles() or [])
        try:
            from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

            by_name = {
                "web": INNERTUBE_CLIENTS.get("web") or {},
                "mweb": INNERTUBE_CLIENTS.get("mweb") or {},
            }
            for profile in profiles:
                name = str(profile.get("name") or "").lower()
                source = by_name.get(name) or {}
                api_key = str(source.get("INNERTUBE_API_KEY") or "")
                if api_key:
                    profile["api_key"] = api_key
        except Exception as exc:
            print("[STABILITY-V6] Innertube key lookup failed:", exc)
        return profiles

    def merge_page_config_with_key(profile, ytcfg):
        context, visitor, api_key = old_merge(profile, ytcfg)
        if not api_key:
            api_key = str((profile or {}).get("api_key") or "")
        return context, visitor, api_key

    related._client_profiles = client_profiles_with_key
    related._merge_page_config = merge_page_config_with_key
    related._pymusic_watch_next_key_v6 = True
    print("[STABILITY-V6] YouTube watch-next API key fallback enabled")


def _patch_comment_loader() -> None:
    """Keep the optional comments worker away from critical player startup."""
    try:
        import current_video_related_fix as current
        import audio_screen
    except Exception:
        return

    if bool(getattr(current, "_pymusic_comments_stable_v6", False)):
        return

    old_fetch = getattr(current, "_fetch_comments", None)
    if not callable(old_fetch):
        return
    media = audio_screen.ma

    def fetch_comments_stable(video_url: str, limit: int = 3):
        # Playback extraction + video extraction + watch-next already start at
        # track open. Give those workers priority and allow only one comment
        # extractor process at a time.
        time.sleep(2.5)
        if not _network_available(media):
            return [], None
        if not _COMMENT_LOCK.acquire(blocking=False):
            return [], None
        try:
            return old_fetch(video_url, min(max(int(limit or 3), 1), 3))
        except BaseException as exc:
            print("[STABILITY-V6] comments worker stopped safely:", exc)
            return [], None
        finally:
            try:
                _COMMENT_LOCK.release()
            except Exception:
                pass

    current._fetch_comments = fetch_comments_stable
    current._pymusic_comments_stable_v6 = True
    print("[STABILITY-V6] comment loading serialized")


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
        # CURRENT-V4 must already own related/autoplay before this final wrapper.
        if not bool(getattr(cls, "_pymusic_current_related_v4", False)):
            return False
        if bool(getattr(cls, "_pymusic_stability_v6", False)):
            _INSTALLED = True
            return True

        media = audio_screen.ma
        Playlist = audio_screen.Playlist
        old_advance = cls._advance_to_next_track
        old_recover = cls._recover_stream
        old_extract_gen = cls._extract_and_start_gen

        def ensure_network_state(self):
            if not hasattr(self, "_network_waiting_v6"):
                self._network_waiting_v6 = False
            if not hasattr(self, "_network_wait_thread_v6"):
                self._network_wait_thread_v6 = None
            if not hasattr(self, "_network_wait_url_v6"):
                self._network_wait_url_v6 = ""
            if not hasattr(self, "_network_wait_gen_v6"):
                self._network_wait_gen_v6 = -1
            if not hasattr(self, "_network_wait_pos_v6"):
                self._network_wait_pos_v6 = 0

        def align_playlist_to_current(self) -> None:
            """Repair index drift without changing the currently playing track."""
            try:
                playlist = getattr(self, "playlist", None)
                tracks = list(getattr(playlist, "tracks", []) or []) if playlist else []
                if len(tracks) < 2:
                    return
                current_url = str(getattr(self, "_last_video_url", "") or "")
                current_id = Playlist._normalize_video_id(current_url)
                if not current_id:
                    return

                actual_index = None
                for idx, track in enumerate(tracks):
                    track_id = Playlist._normalize_video_id(
                        str((track or {}).get("url") or (track or {}).get("video_id") or "")
                    )
                    if track_id and track_id == current_id:
                        actual_index = idx
                        break

                if actual_index is not None and int(getattr(playlist, "index", -1)) != actual_index:
                    print(
                        "[STABILITY-V6] repaired playlist index "
                        f"{getattr(playlist, 'index', -1)} -> {actual_index} for {current_id}"
                    )
                    playlist.index = actual_index
            except Exception as exc:
                print("[STABILITY-V6] playlist alignment failed:", exc)

        def advance_stable(self) -> bool:
            align_playlist_to_current(self)
            return bool(old_advance(self))

        def current_position(self, fallback=0) -> int:
            try:
                player = getattr(media, "android_player", None)
                if player is not None:
                    return max(0, int(player.getCurrentPosition() or fallback or 0))
            except Exception:
                pass
            return max(0, int(fallback or 0))

        def player_is_actively_moving(self, saved_pos: int) -> bool:
            try:
                player = getattr(media, "android_player", None)
                if player is None:
                    return False
                if not bool(player.isPlaying()):
                    return False
                pos = int(player.getCurrentPosition() or 0)
                return pos > int(saved_pos or 0) + 500
            except Exception:
                return False

        def start_network_wait(self, pos: int = 0) -> None:
            ensure_network_state(self)
            url = str(getattr(self, "_last_video_url", "") or "")
            if not url:
                return
            gen = int(getattr(self, "_load_gen", 0) or 0)
            self._network_wait_url_v6 = url
            self._network_wait_gen_v6 = gen
            self._network_wait_pos_v6 = max(
                int(getattr(self, "_network_wait_pos_v6", 0) or 0),
                current_position(self, pos),
            )
            self._resume_pos_ms = self._network_wait_pos_v6
            self._network_waiting_v6 = True

            thread = getattr(self, "_network_wait_thread_v6", None)
            if thread is not None and thread.is_alive():
                return

            def wait_job(expected_url=url, expected_gen=gen):
                print(
                    "[STABILITY-V6] network lost; preserving player "
                    f"url={expected_url!r} pos={self._network_wait_pos_v6}"
                )
                while True:
                    if str(getattr(self, "_last_video_url", "") or "") != expected_url:
                        self._network_waiting_v6 = False
                        return
                    if int(getattr(self, "_load_gen", -1)) != expected_gen:
                        self._network_waiting_v6 = False
                        return
                    if not bool(getattr(self, "_playback_desired", True)):
                        self._network_waiting_v6 = False
                        return
                    if _network_available(media):
                        break
                    time.sleep(0.75)

                saved_pos = int(getattr(self, "_network_wait_pos_v6", 0) or 0)
                self._network_waiting_v6 = False
                print(
                    "[STABILITY-V6] network restored "
                    f"url={expected_url!r} pos={saved_pos}"
                )

                # A very short outage can recover inside MediaPlayer by itself.
                # Do not reset a stream that is already moving again.
                time.sleep(0.25)
                if player_is_actively_moving(self, saved_pos):
                    print("[STABILITY-V6] MediaPlayer recovered without reset")
                    return

                if str(getattr(self, "_last_video_url", "") or "") != expected_url:
                    return
                if int(getattr(self, "_load_gen", -1)) != expected_gen:
                    return

                self._resume_pos_ms = saved_pos
                try:
                    self._last_stream_recover_ts = 0.0
                except Exception:
                    pass
                try:
                    old_recover(self, "network-restored", saved_pos, force_fresh=True)
                except Exception as exc:
                    print("[STABILITY-V6] restore failed:", exc)

            thread = threading.Thread(
                target=wait_job,
                name="pymusic-network-wait-v6",
                daemon=True,
            )
            self._network_wait_thread_v6 = thread
            thread.start()

        def recover_network_safe(self, reason: str, pos: int, *, force_fresh: bool = False):
            ensure_network_state(self)
            if not _network_available(media):
                # Critical rule: loss of connectivity must not reset the video,
                # generation or UI. Preserve state and wait outside Kivy Clock.
                self._playback_desired = True
                self._user_paused = False
                start_network_wait(self, pos)
                return None
            return old_recover(self, reason, pos, force_fresh=force_fresh)

        def extract_gen_network_safe(self, video_url: str, gen=None, *, prefer_compat=None):
            ensure_network_state(self)
            if not _network_available(media):
                self._playback_desired = True
                self._user_paused = False
                try:
                    pos = current_position(self, getattr(self, "_resume_pos_ms", 0))
                except Exception:
                    pos = int(getattr(self, "_resume_pos_ms", 0) or 0)
                start_network_wait(self, pos)
                return None
            return old_extract_gen(
                self,
                video_url,
                gen,
                prefer_compat=prefer_compat,
            )

        cls._advance_to_next_track = advance_stable
        cls._recover_stream = recover_network_safe
        cls._extract_and_start_gen = extract_gen_network_safe
        cls._pymusic_stability_v6 = True

        _patch_watch_next_key()
        _patch_comment_loader()

        _INSTALLED = True
        print("[STABILITY-V6] playlist/network/watch-next stability enabled")
        return True


def install_player_stability_v6() -> bool:
    global _STARTED
    if _install_now():
        return True

    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        for _attempt in range(500):
            if _install_now():
                return
            time.sleep(0.05)
        print("[STABILITY-V6] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-stability-v6-installer",
        daemon=True,
    ).start()
    return True
