"""Eliminate the remaining initial audio/video offset.

The user-observed clue is decisive: a manual seek through either the native
buttons or slider immediately makes playback perfectly synchronized.  That
operation seeks both Android MediaPlayers.  Earlier startup patches mainly
realigned the video player, so a decoder could still display an older frame even
when both reported positions looked close.

This final patch repeats the exact dual-player seek automatically just after the
video is fully prepared and visible.  It runs only for the latest track/video
load and never during user scrubbing or while the app is in background.
"""
from __future__ import annotations

import sys
import threading
import time
import weakref

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_startup_dual_seek() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        if player_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_visual_fill_v2", False):
            return False
        if getattr(player_cls, "_pymusic_startup_dual_seek_v5", False):
            _PATCHED = True
            return True

        media = module.ma
        old_play_video = player_cls._play_video_if_screen_active

        def valid(self, token: int, load_gen: int, video_url: str) -> bool:
            try:
                return bool(
                    int(token) == int(getattr(self, "_pymusic_dual_seek_token", -1))
                    and int(load_gen) == int(getattr(self, "_load_gen", -1))
                    and str(video_url or "") == str(getattr(self, "_last_video_url", "") or "")
                    and bool(getattr(self, "_video_enabled", False))
                    and bool(getattr(self, "_playback_desired", False))
                    and not bool(getattr(self, "_user_paused", False))
                    and not bool(getattr(self, "_app_in_background", False))
                )
            except Exception:
                return False

        def dual_seek_once(
            self,
            token: int,
            load_gen: int,
            video_url: str,
            stage: str,
        ) -> bool:
            if not valid(self, token, load_gen, video_url):
                return False
            if bool(getattr(self, "_is_scrubbing", False)):
                return False
            if bool(getattr(self, "_pymusic_startup_barrier_active", False)):
                return False

            audio_player = getattr(media, "android_player", None)
            video_player = getattr(self, "_video_player", None)
            native_video = getattr(video_player, "player", None) if video_player else None
            if (
                audio_player is None
                or video_player is None
                or native_video is None
                or not bool(getattr(video_player, "_prepared", False))
            ):
                return False

            try:
                target = max(0, int(audio_player.getCurrentPosition() or 0))
            except Exception:
                return False

            try:
                # Reset any temporary speed correction before flushing both
                # decoders to the same clock position.
                speed = getattr(video_player, "_pymusic_set_sync_speed", None)
                if callable(speed):
                    speed(1.0)
            except Exception:
                pass

            try:
                # These are intentionally the same two calls used by
                # AudioPlayerScreen.video_seek(), which the user confirmed
                # produces perfect synchronization on the device.
                media._mp_seek_to(target)
                video_player.seek_to(target)
            except Exception as exc:
                print("[VIDEO] automatic dual seek failed:", exc)
                return False

            now = time.monotonic()
            try:
                self._resume_pos_ms = target
                self._pymusic_sync_player_id = id(native_video)
                self._pymusic_sync_last_seek = now
                self._pymusic_sync_settle_until = now + 0.24
                self._pymusic_sync_outside_since = 0.0
                self._pymusic_sync_bias_ms = 0.0
            except Exception:
                pass

            try:
                video_pos = int(video_player.get_current_position() or 0)
            except Exception:
                video_pos = -1
            print(
                "[VIDEO] automatic manual-equivalent dual seek "
                f"stage={stage} target={target} video_before={video_pos}"
            )
            return True

        def start_dual_seek_worker(self, video_url: str) -> None:
            self._pymusic_dual_seek_token = int(
                getattr(self, "_pymusic_dual_seek_token", 0)
            ) + 1
            token = int(self._pymusic_dual_seek_token)
            load_gen = int(getattr(self, "_load_gen", 0))
            owner_ref = weakref.ref(self)

            def worker() -> None:
                owner = owner_ref()
                if owner is None:
                    return

                # Wait until the startup barrier has released both players and
                # the native video player is actually prepared.
                deadline = time.monotonic() + 10.0
                while time.monotonic() < deadline:
                    owner = owner_ref()
                    if owner is None or not valid(owner, token, load_gen, video_url):
                        return
                    vp = getattr(owner, "_video_player", None)
                    ready = bool(
                        vp is not None
                        and getattr(vp, "player", None) is not None
                        and getattr(vp, "_prepared", False)
                        and not getattr(owner, "_pymusic_startup_barrier_active", False)
                    )
                    if ready:
                        break
                    time.sleep(0.025)
                else:
                    print("[VIDEO] automatic dual seek timed out waiting for ready video")
                    return

                # First flush immediately after startup, then repeat after the
                # first decoder buffers are populated.  Repeating the same
                # no-op-position seek is cheap and prevents a late first-GOP
                # frame from reintroducing a 1–1.5 second visual delay.
                stages = (
                    (0.06, "ready"),
                    (0.42, "decoder-warm"),
                    (0.95, "final-check"),
                )
                previous = 0.0
                for delay, stage in stages:
                    time.sleep(max(0.0, delay - previous))
                    previous = delay
                    owner = owner_ref()
                    if owner is None or not valid(owner, token, load_gen, video_url):
                        return
                    dual_seek_once(owner, token, load_gen, video_url, stage)

            threading.Thread(
                target=worker,
                name="pymusic-startup-dual-seek",
                daemon=True,
            ).start()

        def play_video_with_dual_seek(self, vurl: str, vheaders: dict):
            result = old_play_video(self, vurl, vheaders)
            try:
                start_dual_seek_worker(self, str(getattr(self, "_last_video_url", "") or ""))
            except Exception as exc:
                print("[VIDEO] failed to start dual seek worker:", exc)
            return result

        player_cls._play_video_if_screen_active = play_video_with_dual_seek
        player_cls._pymusic_startup_dual_seek_v5 = True
        _PATCHED = True
        print("[HOTFIX] manual-equivalent startup dual seek v5 enabled")
        return True


_patch_startup_dual_seek()
