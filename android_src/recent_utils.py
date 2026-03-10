# recent_utils.py

import os
import json

RECENT_PATH = "recent.json"
MAX_RECENT = 10

def load_recent():
    if os.path.exists(RECENT_PATH):
        try:
            with open(RECENT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_recent(recent_list):
    try:
        with open(RECENT_PATH, "w", encoding="utf-8") as f:
            json.dump(recent_list[:MAX_RECENT], f, ensure_ascii=False)
    except Exception as e:
        print("[RECENT] Error saving recent list:", e)


def update_recent_cache(url: str, cache_path: str | None):
    if not url:
        return
    try:
        recent = load_recent()
        updated = False
        for r in recent:
            if r.get("url") == url:
                if cache_path:
                    r["cache_path"] = cache_path
                else:
                    r.pop("cache_path", None)
                updated = True
                break
        if updated:
            save_recent(recent)
    except Exception as e:
        print("[RECENT] Error updating cache path:", e)


def update_recent_art(url: str, art_path: str | None):
    if not url:
        return
    try:
        recent = load_recent()
        updated = False
        for r in recent:
            if r.get("url") == url:
                if art_path:
                    r["art_path"] = art_path
                else:
                    r.pop("art_path", None)
                updated = True
                break
        if updated:
            save_recent(recent)
    except Exception as e:
        print("[RECENT] Error updating art path:", e)
