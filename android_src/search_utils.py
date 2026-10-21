import os
import json

# Runtime-free startup: the player's playlist transitions, audio-first
# behavior, current video and recommendation UI are defined directly in
# audio_screen.AudioPlayerScreen. Only the remaining independent engine-level
# services are initialized here.

try:
    from core_player_runtime import install_core_player_fix
    if not install_core_player_fix():
        print("[CORE-V4] synchronous install returned false")
except Exception as exc:
    print("[CORE-V4] synchronous install failed:", exc)

try:
    from player_stability_v6 import install_player_stability_v6
    if not install_player_stability_v6():
        print("[STABILITY-V6] install returned false")
except Exception as exc:
    print("[STABILITY-V6] install failed:", exc)

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