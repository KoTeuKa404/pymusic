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
