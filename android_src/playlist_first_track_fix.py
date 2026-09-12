"""Keep the requested first playlist item from being skipped.

There are two different first-track races in PyMusic:

1. A watch+playlist URL can start its seed video before the queue arrives.  If
   the returned queue omits that seed, the first normal next() must play queue
   index 0 instead of incrementing to 1.
2. When a normal playlist is opened, ``play_playlist`` installs the new queue
   before ``play_audio`` has invalidated callbacks from the old MediaPlayer.
   A late completion/auto-next from the old track can therefore see the freshly
   installed queue at index 0 and advance it to index 1 before track 0 starts.

V2 invalidates the old playback generation *before* the new queue is installed
and verifies that the requested start index is still the track that was opened.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlparse

_LOCK = threading.RLock()
_INSTALLED = False
_STARTED = False


def _video_id(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and all(c.isalnum() or c in "-_" for c in raw):
        return raw
    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query or "")
        vid = str((query.get("v") or [""])[0] or "")
        if vid:
            return vid
        if "youtu.be" in (parsed.netloc or "").lower():
            return (parsed.path or "").strip("/").split("/", 1)[0]
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


def _requested_start_index(args, kwargs, count: int) -> int:
    try:
        start = int(kwargs.get("start_index", 0) or 0)
    except Exception:
        start = 0

    # play_playlist(tracks, maybe2, *, start_index=...).  A positional int is
    # itself the start index, while bool/string keep the keyword/default value.
    if args:
        maybe2 = args[0]
        if isinstance(maybe2, int) and not isinstance(maybe2, bool):
            try:
                start = int(maybe2)
            except Exception:
                start = 0

    if count <= 0 or start < 0 or start >= count:
        return 0
    return start


def _invalidate_old_transition(self) -> None:
    """Make every queued callback from the previous track stale immediately."""
    try:
        self._load_gen = int(getattr(self, "_load_gen", 0) or 0) + 1
    except Exception:
        pass

    for attr, value in (
        ("_completion_guard_gen", -1),
        ("_bg_endguard_fired_gen", -1),
        ("_extract_fail_handled_gen", -1),
        ("_pymusic_next_started_gen", -1),
    ):
        try:
            setattr(self, attr, value)
        except Exception:
            pass

    # Clear pending intents owned by the later runtime layers too.  These attrs
    # are optional so this remains safe regardless of patch install timing.
    try:
        self._current_auto_next_pending_v4 = False
    except Exception:
        pass
    try:
        self._playlist_play_current_before_next_v1 = False
    except Exception:
        pass
    try:
        self._network_waiting_v6 = False
        self._network_wait_gen_v6 = -1
    except Exception:
        pass


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
        # Install after the current-video layer because it also owns
        # _advance_to_next_track.
        if not bool(getattr(cls, "_pymusic_current_related_v4", False)):
            return False
        if bool(getattr(cls, "_pymusic_playlist_first_track_v2", False)):
            _INSTALLED = True
            return True

        old_play_playlist = cls.play_playlist
        old_advance = cls._advance_to_next_track

        def play_playlist_fixed(self, tracks, *args, **kwargs):
            start_playback = bool(kwargs.get("start_playback", True))
            current_before = str(getattr(self, "_last_video_url", "") or "")

            # Materialize once.  yt-dlp callers normally pass a list, but doing
            # this here also makes generators deterministic for both the guard
            # and the original play_playlist implementation.
            try:
                incoming = list(tracks or [])
            except Exception:
                incoming = tracks

            requested_index = _requested_start_index(
                args,
                kwargs,
                len(incoming) if isinstance(incoming, list) else 0,
            )

            if start_playback:
                # Critical ordering: invalidate the old MediaPlayer generation
                # BEFORE old_play_playlist calls playlist.set_tracks().  A stale
                # completion can no longer advance the new queue from 0 to 1.
                _invalidate_old_transition(self)
                try:
                    print(
                        "[PLAYLIST-FIRST-V2] opening queue "
                        f"requested_index={requested_index} pre_gen={getattr(self, '_load_gen', -1)}"
                    )
                except Exception:
                    pass

            result = old_play_playlist(self, incoming, *args, **kwargs)

            # Only the background queue-install path can have an already playing
            # seed video that is absent from the returned queue.
            pending = False
            if not start_playback and current_before:
                try:
                    queue = list(getattr(getattr(self, "playlist", None), "tracks", []) or [])
                    current_present = any(
                        _same_video(
                            current_before,
                            str((item or {}).get("url") or (item or {}).get("video_id") or ""),
                        )
                        for item in queue
                        if isinstance(item, dict)
                    )
                    pending = bool(queue and not current_present)
                except Exception:
                    pending = False

            self._playlist_play_current_before_next_v1 = pending
            if pending:
                try:
                    print(
                        "[PLAYLIST-FIRST-V2] current seed is outside queue; "
                        "first transition will play queue index 0"
                    )
                except Exception:
                    pass

            if start_playback:
                # Defensive verification.  Normally the generation invalidation
                # above is enough.  If another non-generation callback changed
                # the queue synchronously, restore the requested item now.
                try:
                    playlist = getattr(self, "playlist", None)
                    queue = list(getattr(playlist, "tracks", []) or []) if playlist else []
                    if queue:
                        target_index = requested_index if 0 <= requested_index < len(queue) else 0
                        target = queue[target_index]
                        target_url = str((target or {}).get("url") or "")
                        current_url = str(getattr(self, "_last_video_url", "") or "")
                        actual_index = int(getattr(playlist, "index", 0) or 0)

                        if actual_index != target_index:
                            print(
                                "[PLAYLIST-FIRST-V2] repaired opening index "
                                f"{actual_index} -> {target_index}"
                            )
                            playlist.index = target_index

                        if target_url and not _same_video(current_url, target_url):
                            print(
                                "[PLAYLIST-FIRST-V2] stale transition selected wrong track; "
                                f"restoring index={target_index} url={target_url!r}"
                            )
                            self.play_audio(
                                target_url,
                                (target or {}).get("title") or "",
                                (target or {}).get("channel") or "",
                                (target or {}).get("thumb") or "",
                                clear_playlist=False,
                                hard_reset=True,
                            )
                except Exception as exc:
                    print("[PLAYLIST-FIRST-V2] opening verification failed:", exc)

            return result

        def advance_fixed(self) -> bool:
            if bool(getattr(self, "_playlist_play_current_before_next_v1", False)):
                self._playlist_play_current_before_next_v1 = False
                try:
                    playlist = getattr(self, "playlist", None)
                    track = playlist.current() if playlist else None
                    if track:
                        print(
                            f"[PLAYLIST-FIRST-V2] playing first queued track "
                            f"index={getattr(playlist, 'index', 0)} "
                            f"url={track.get('url')!r}"
                        )
                        self.play_audio(
                            track["url"],
                            track.get("title") or "",
                            track.get("channel") or "",
                            track.get("thumb") or "",
                            clear_playlist=False,
                            hard_reset=True,
                        )
                        return True
                except Exception as exc:
                    print("[PLAYLIST-FIRST-V2] first queued track failed:", exc)
            return bool(old_advance(self))

        cls.play_playlist = play_playlist_fixed
        cls._advance_to_next_track = advance_fixed
        cls._pymusic_playlist_first_track_v1 = True
        cls._pymusic_playlist_first_track_v2 = True
        _INSTALLED = True
        print("[PLAYLIST-FIRST-V2] first-track opening race fix enabled")
        return True


def install_playlist_first_track_fix() -> bool:
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
        print("[PLAYLIST-FIRST-V2] install timeout")

    threading.Thread(
        target=waiter,
        name="pymusic-playlist-first-track-installer",
        daemon=True,
    ).start()
    return True
