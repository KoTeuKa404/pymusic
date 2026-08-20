"""Do not tear down the native video surface on a real network outage.

Android MediaPlayer enters an error state when a remote stream loses network.
The old video_player handler immediately called stop(), which removed/reset the
video surface.  AudioPlayerScreen already owns reconnection and will call
AndroidVideoPlayer.play() again after connectivity returns, so while Android
reports offline we preserve the visible surface and consume the error.

This module also bootstraps FAST-SWITCH-V9.  Keeping that startup here means the
fast-switch layer is installed after the network/recovery layers that also touch
the same player lifecycle.
"""
from __future__ import annotations

import threading

_LOCK = threading.RLock()
_INSTALLED = False


def _start_fast_switch_v9() -> None:
    try:
        from fast_track_switch_v9 import install_fast_track_switch_v9

        if not install_fast_track_switch_v9():
            print("[FAST-SWITCH-V9] install returned false")
    except Exception as exc:
        print("[FAST-SWITCH-V9] install failed:", exc)


def install_video_network_stability_v8() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            _start_fast_switch_v9()
            return True
        try:
            import video_player
            import media_android as media
        except Exception as exc:
            print("[VIDEO-NET-V8] import failed:", exc)
            return False

        cls = getattr(video_player, "AndroidVideoPlayer", None)
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_video_network_v8", False)):
            _INSTALLED = True
            _start_fast_switch_v9()
            return True

        old_error = cls._on_error

        def network_safe_error(self, mp, what, extra, gen: int):
            if gen != getattr(self, "_play_gen", -1):
                return True

            offline = False
            try:
                offline = not bool(media.is_network_available())
            except Exception:
                offline = False

            if offline:
                try:
                    video_player._log(
                        f"[VIDEO-NET-V8] offline MediaPlayer error preserved "
                        f"what={what} extra={extra} gen={gen}"
                    )
                except Exception:
                    pass
                # Do not call stop(), do not increment _play_gen and do not hide
                # the surface. The failed MediaPlayer instance will be replaced
                # by the normal play() path after AudioPlayerScreen reconnects.
                try:
                    self._prepared = False
                    self._surface_ready_to_show = False
                except Exception:
                    pass
                return True

            return old_error(self, mp, what, extra, gen)

        cls._on_error = network_safe_error
        cls._pymusic_video_network_v8 = True
        _INSTALLED = True
        print("[VIDEO-NET-V8] offline video surface preservation enabled")

        _start_fast_switch_v9()
        return True
