# recent_utils.py

import os
import json
import threading
import time

# Restore the modern player runtime/metadata/recommendations patch chain.
# Only the experimental whole-page scrolling rewrite is disabled: the
# playlist owns a bounded MDScrollView as in the user-verified APK.
try:
    import sitecustomize as _player_hotfix
    import final_player_fix as _final_player_fix
    import playlist_open_guard_v12 as _playlist_open_guard_v12

    def _install_player_hotfix_when_ready():
        ready = {"base": False, "final": False}
        for attempt in range(400):
            if not ready["base"]:
                try:
                    ready["base"] = bool(_player_hotfix._patch_audio_screen())
                    if ready["base"]:
                        print("[HOTFIX] loader installed: base")
                except Exception as exc:
                    print("[HOTFIX] base install failed:", exc)
            if ready["base"] and not ready["final"]:
                try:
                    ready["final"] = bool(_final_player_fix._patch_final_player())
                    if ready["final"]:
                        print("[HOTFIX] loader installed: final")
                except Exception as exc:
                    print("[HOTFIX] final install failed:", exc)
            if all(ready.values()):
                print("[HOTFIX] loader ready", ready)
                return
            time.sleep(0.05)
        print("[HOTFIX] loader timeout", ready)

    threading.Thread(
        target=_install_player_hotfix_when_ready,
        name="pymusic-player-hotfix",
        daemon=True,
    ).start()

    try:
        _playlist_open_guard_v12.install_playlist_open_guard_v12()
    except Exception as exc:
        print("[PLAYLIST-OPEN-V12] initialization failed:", exc)
except Exception as exc:
    print("[HOTFIX] loader failed:", exc)


RECENT_PATH = "recent.json"
FAVORITES_PATH = "favorites.json"
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


def load_favorites():
    if os.path.exists(FAVORITES_PATH):
        try:
            with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_favorites(items):
    try:
        with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception as e:
        print("[FAV] Error saving favorites:", e)


def is_favorite(url: str) -> bool:
    if not url:
        return False
    try:
        for it in load_favorites():
            if it.get("url") == url:
                return True
    except Exception:
        pass
    return False


def upsert_favorite(item: dict):
    url = str((item or {}).get("url") or "")
    if not url:
        return
    favs = load_favorites()
    found = False
    for i, f in enumerate(favs):
        if f.get("url") == url:
            favs[i] = dict(favs[i], **item)
            found = True
            break
    if not found:
        favs.insert(0, dict(item))
    save_favorites(favs)


def remove_favorite(url: str):
    if not url:
        return
    favs = load_favorites()
    favs = [f for f in favs if f.get("url") != url]
    save_favorites(favs)


def update_favorite_cache(url: str, cache_path: str | None):
    if not url:
        return
    favs = load_favorites()
    updated = False
    for f in favs:
        if f.get("url") == url:
            if cache_path:
                f["cache_path"] = cache_path
            else:
                f.pop("cache_path", None)
            updated = True
            break
    if updated:
        save_favorites(favs)
