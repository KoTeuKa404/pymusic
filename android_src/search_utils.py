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
