import os
import json

# main.py imports search_utils only after audio_screen.AudioPlayerScreen has
# finished being defined. Install the core player changes synchronously before
# Builder creates the screen instance.
try:
    from core_player_runtime import install_core_player_fix
    if not install_core_player_fix():
        print("[CORE-V4] synchronous install returned false")
except Exception as exc:
    print("[CORE-V4] synchronous install failed:", exc)

# Apply the regression layer after the muxed AV patch. It restores audio-first
# startup and makes screen-off handoff reuse the already-running shadow audio
# clock without a seek/restart.
try:
    from playback_regression_fix import install_playback_regression_fix
    if not install_playback_regression_fix():
        print("[PLAYBACK-V5] synchronous install returned false")
except Exception as exc:
    print("[PLAYBACK-V5] synchronous install failed:", exc)

# Screen-off playlist transitions must not depend on Kivy Clock. Install this
# after PLAYBACK-V5 because that layer replaces _prefetch_next_track_audio.
try:
    from background_playlist_transition_fix import install_background_playlist_transition_fix

    if not install_background_playlist_transition_fix():
        print("[BG-NEXT] synchronous install returned false")
except Exception as exc:
    print("[BG-NEXT] synchronous install failed:", exc)

# Kivy Clock stores bound callbacks through WeakMethod and resolves them later
# using the function's __name__. The runtime patch assigns the function named
# "background_tick_with_shadow_sync" to the class attribute "_background_tick".
# Without this alias WeakMethod later looks up the original function name and
# crashes during the first scheduled tick. Register both names before Builder
# creates AudioPlayerScreen instances.
try:
    import audio_screen as _audio_screen

    _player_cls = getattr(_audio_screen, "AudioPlayerScreen", None)
    _patched_tick = (
        getattr(_player_cls, "_background_tick", None)
        if _player_cls is not None
        else None
    )
    if callable(_patched_tick):
        _callback_name = str(
            getattr(_patched_tick, "__name__", "_background_tick")
            or "_background_tick"
        )
        setattr(_player_cls, _callback_name, _patched_tick)
        print(f"[PLAYBACK-V5] Clock callback alias installed: {_callback_name}")
except Exception as exc:
    print("[PLAYBACK-V5] Clock callback alias failed:", exc)

# Channel avatar, like/dislike statistics and centered UI geometry now live
# directly in audio_screen.AudioPlayerScreen. No installer/monkey-patch needed.

# Kivy fallback control size, spacing and on_press actions live in
# youtube_gui.kv. No runtime method wrapper is needed here.

# Keep the previous Python native path as a fallback for taps on the transparent
# video surface itself. The actual three ImageButtons are rebound below to a
# Java-only transport path, so Python/GIL latency cannot delay pause/seek.
try:
    from native_video_controls_fast_fix import install_native_video_controls_fast_fix

    if not install_native_video_controls_fast_fix():
        print("[VIDEO-CTRL-FAST] synchronous install returned false")
except Exception as exc:
    print("[VIDEO-CTRL-FAST] synchronous install failed:", exc)

# AndroidVideoPlayer binds the three Java transport buttons directly;
# AudioPlayerScreen polls the bridge's state without monkey-patching methods.

# Native timeline/SeekBar styling belongs to AndroidVideoPlayer itself.
# No second Android OnSeekBarChangeListener installation is needed.

# Narrow stable-regression fixes only: clamp the outer player scroll to real
# bounds and treat YouTube RD/start_radio watch URLs as a single video so the
# lower section is recommendations instead of a fake playlist queue.
try:
    from latest_regression_fix import install_latest_regression_fix

    if not install_latest_regression_fix():
        print("[LATEST-FIX] synchronous install returned false")
except Exception as exc:
    print("[LATEST-FIX] synchronous install failed:", exc)

# Thumbnail aspect ratio is enforced by youtube_gui.kv and final_player_fix,
# without an additional runtime method wrapper.

# Final owner of the current-video -> recommendations -> autoplay chain.
# It has its own readiness waiter, so it installs after the old core/final
# patches even when recent_utils finishes asynchronously.
try:
    from current_video_related_fix import install_current_video_related_fix

    if not install_current_video_related_fix():
        print("[CURRENT-V4] install returned false")
except Exception as exc:
    print("[CURRENT-V4] install failed:", exc)

# If a fast-opened playlist queue omits the already-playing seed video, index 0
# is the first *next* item. The normal next() call would skip it and jump to 1.
# Install this after CURRENT-V4 because that layer also wraps auto-next.
try:
    from playlist_first_track_fix import install_playlist_first_track_fix

    if not install_playlist_first_track_fix():
        print("[PLAYLIST-FIRST] install returned false")
except Exception as exc:
    print("[PLAYLIST-FIRST] install failed:", exc)

# Final stability owner: aligns queue index to the actually playing URL before
# any next(), waits through real offline periods without resetting video state,
# supplies yt-dlp's current public Innertube key to watch-next, and keeps the
# optional comments extractor from competing with critical startup workers.
try:
    from player_stability_v6 import install_player_stability_v6

    if not install_player_stability_v6():
        print("[STABILITY-V6] install returned false")
except Exception as exc:
    print("[STABILITY-V6] install failed:", exc)

# related_videos._merge_page_config now includes current public API-key
# recovery directly, without a post-import monkey patch.

# AndroidVideoPlayer._on_error directly preserves its SurfaceView while offline.
# The fast-switch audio-priority layer still installs after reconnect/stability.
try:
    from fast_track_switch_v9 import install_fast_track_switch_v9
    if not install_fast_track_switch_v9():
        print("[FAST-SWITCH-V9] install returned false")
except Exception as exc:
    print("[FAST-SWITCH-V9] install failed:", exc)


SEARCH_HISTORY_PATH = "search_history.json"
MAX_HISTORY = 10


def load_search_history():
    if os.path.exists(SEARCH_HISTORY_PATH):
        try:
            with open(SEARCH_HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_search_history(history):
    try:
        with open(SEARCH_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history[:MAX_HISTORY], f, ensure_ascii=False)
    except Exception as e:
        print("[SEARCH] Error saving search history:", e)