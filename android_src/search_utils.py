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

# Add the like counter/like-dislike ratio next to Favorites and make the channel
# name bold. Apply the tiny layout tuning before installing the runtime hooks so
# the channel gets the full remaining width and the thumb matches the PNG action
# buttons visually.
try:
    import likes_ui_patch as _likes_ui_patch
    from likes_ui_tuning import apply_likes_ui_tuning

    if not apply_likes_ui_tuning(_likes_ui_patch):
        print("[LIKES-UI] visual tuning returned false")
    if not _likes_ui_patch.install_likes_ui_patch():
        print("[LIKES-UI] synchronous install returned false")
except Exception as exc:
    print("[LIKES-UI] synchronous install failed:", exc)

# Tune the hidden Kivy controls too. This is useful as a fallback when the
# SurfaceView/native overlay is not active on a particular device.
try:
    from video_controls_ui_fix import install_video_controls_ui_fix

    if not install_video_controls_ui_fix():
        print("[VIDEO-CTRL] synchronous install returned false")
except Exception as exc:
    print("[VIDEO-CTRL] synchronous install failed:", exc)

# Keep the previous Python native path as a fallback for taps on the transparent
# video surface itself. The actual three ImageButtons are rebound below to a
# Java-only transport path, so Python/GIL latency cannot delay pause/seek.
try:
    from native_video_controls_fast_fix import install_native_video_controls_fast_fix

    if not install_native_video_controls_fast_fix():
        print("[VIDEO-CTRL-FAST] synchronous install returned false")
except Exception as exc:
    print("[VIDEO-CTRL-FAST] synchronous install failed:", exc)

# Final transport layer: the visible rewind/play/forward ImageButtons execute
# MediaPlayer commands directly inside Java on ACTION_DOWN. Python only mirrors
# state later for notifications and the rest of the Kivy UI.
try:
    from native_java_transport_fix import install_native_java_transport_fix

    if not install_native_java_transport_fix():
        print("[JAVA-CTRL] synchronous install returned false")
except Exception as exc:
    print("[JAVA-CTRL] synchronous install failed:", exc)

# Restyle only the native SurfaceView timeline after the Java transport layer is
# installed. The seek callback itself is unchanged, so audio/video sync and the
# low-latency transport path stay intact.
try:
    from youtube_timeline_fix import install_youtube_timeline_fix

    if not install_youtube_timeline_fix():
        print("[YT-TIMELINE] synchronous install returned false")
except Exception as exc:
    print("[YT-TIMELINE] synchronous install failed:", exc)

# Narrow stable-regression fixes only: clamp the outer player scroll to real
# bounds and treat YouTube RD/start_radio watch URLs as a single video so the
# lower section is recommendations instead of a fake playlist queue.
try:
    from latest_regression_fix import install_latest_regression_fix

    if not install_latest_regression_fix():
        print("[LATEST-FIX] synchronous install returned false")
except Exception as exc:
    print("[LATEST-FIX] synchronous install failed:", exc)

# The video SurfaceView already keeps the real aspect ratio. Keep the Kivy
# thumbnail on the same rule too, even if final_player_fix tries to stretch it
# to the 16:9 viewport while aligning the native video layer.
try:
    from thumbnail_aspect_fix import install_thumbnail_aspect_fix

    if not install_thumbnail_aspect_fix():
        print("[THUMB-ASPECT] synchronous install returned false")
except Exception as exc:
    print("[THUMB-ASPECT] synchronous install failed:", exc)

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

# yt-dlp's 2026 client table no longer embeds INNERTUBE_API_KEY. If a watch page
# does not expose it in ytcfg either, scrape YouTube's current public web key
# over verified HTTPS and use it for the exact watch-next request.
try:
    from watch_next_key_fix_v7 import install_watch_next_key_fix_v7

    if not install_watch_next_key_fix_v7():
        print("[WATCH-NEXT-V7] install returned false")
except Exception as exc:
    print("[WATCH-NEXT-V7] install failed:", exc)

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