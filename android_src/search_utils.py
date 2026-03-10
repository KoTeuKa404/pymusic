import os
import json

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
